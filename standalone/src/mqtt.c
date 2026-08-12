#include "mqtt.h"

#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "util.h"

#define BACKOFF_MIN 1.0
#define BACKOFF_MAX 30.0

/* Control packet types, high nibble of the fixed header. */
#define PKT_CONNECT 0x10
#define PKT_CONNACK 0x20
#define PKT_PUBLISH 0x30
#define PKT_PUBACK 0x40
#define PKT_SUBSCRIBE 0x80
#define PKT_SUBACK 0x90
#define PKT_PINGREQ 0xC0
#define PKT_PINGRESP 0xD0
#define PKT_DISCONNECT 0xE0

static void set_error(struct mqtt_client *client, const char *reason)
{
    snprintf(client->last_error, sizeof(client->last_error), "%s", reason);
}

void mqtt_init(struct mqtt_client *client, const struct mqtt_config *config,
               mqtt_on_message on_message, void *user)
{
    memset(client, 0, sizeof(*client));
    client->config = *config;
    client->fd = -1;
    client->phase = MQTT_IDLE;
    client->next_packet_id = 1;
    client->backoff = BACKOFF_MIN;
    client->next_attempt = 0.0; /* connect immediately */
    client->on_message = on_message;
    client->user = user;
}

int mqtt_fd(const struct mqtt_client *client)
{
    return client->fd;
}

bool mqtt_connected(const struct mqtt_client *client)
{
    return client->phase == MQTT_READY;
}

bool mqtt_wants_write(const struct mqtt_client *client)
{
    return client->phase == MQTT_CONNECTING || client->tx_sent < client->tx_len;
}

void mqtt_close(struct mqtt_client *client, const char *reason)
{
    if (client->fd >= 0) {
        close(client->fd);
        client->fd = -1;
    }
    bool was_up = client->phase == MQTT_READY;
    client->phase = MQTT_IDLE;
    client->rx_len = 0;
    client->tx_len = 0;
    client->tx_sent = 0;
    client->ping_outstanding = false;
    /* Anything in flight goes back on the queue; the broker may or may not
     * have seen it, and these writes are absolute setpoints, so resending is
     * safe. */
    for (int i = 0; i < client->queue_count; i++) {
        int index = (client->queue_head + i) % MQTT_QUEUE_MAX;
        client->queue[index].in_flight = false;
    }
    if (reason) {
        set_error(client, reason);
        if (was_up)
            log_warn("mqtt: disconnected: %s", reason);
        else
            log_debug("mqtt: %s", reason);
    }
    client->state_changed = client->state_changed || was_up;
    client->next_attempt = now_monotonic() + client->backoff;
    client->backoff *= 2.0;
    if (client->backoff > BACKOFF_MAX)
        client->backoff = BACKOFF_MAX;
}

/* ---- writing ---------------------------------------------------------- */

static bool tx_room(const struct mqtt_client *client, size_t needed)
{
    return client->tx_len + needed <= MQTT_TX_MAX;
}

static void tx_byte(struct mqtt_client *client, unsigned char byte)
{
    if (client->tx_len < MQTT_TX_MAX)
        client->tx[client->tx_len++] = byte;
}

static void tx_bytes(struct mqtt_client *client, const void *data, size_t len)
{
    if (client->tx_len + len > MQTT_TX_MAX)
        return;
    memcpy(client->tx + client->tx_len, data, len);
    client->tx_len += len;
}

static void tx_string(struct mqtt_client *client, const char *text)
{
    size_t len = text ? strlen(text) : 0;
    tx_byte(client, (unsigned char)(len >> 8));
    tx_byte(client, (unsigned char)(len & 0xFF));
    tx_bytes(client, text, len);
}

/* MQTT's variable-length integer, up to four bytes. */
static void tx_remaining_length(struct mqtt_client *client, size_t length)
{
    do {
        unsigned char byte = (unsigned char)(length % 128);
        length /= 128;
        if (length > 0)
            byte |= 0x80;
        tx_byte(client, byte);
    } while (length > 0);
}

static size_t string_field(const char *text)
{
    return 2 + (text ? strlen(text) : 0);
}

static bool send_connect(struct mqtt_client *client)
{
    const struct mqtt_config *config = &client->config;
    bool has_user = config->username && config->username[0];
    bool has_pass = has_user && config->password && config->password[0];

    size_t payload = string_field(config->client_id);
    if (has_user)
        payload += string_field(config->username);
    if (has_pass)
        payload += string_field(config->password);
    size_t variable = 10; /* protocol name + level + flags + keepalive */
    if (!tx_room(client, variable + payload + 5))
        return false;

    unsigned char flags = 0x02; /* clean session */
    if (has_user)
        flags |= 0x80;
    if (has_pass)
        flags |= 0x40;

    tx_byte(client, PKT_CONNECT);
    tx_remaining_length(client, variable + payload);
    tx_string(client, "MQTT");
    tx_byte(client, 4); /* protocol level 3.1.1 */
    tx_byte(client, flags);
    tx_byte(client, (unsigned char)(config->keepalive >> 8));
    tx_byte(client, (unsigned char)(config->keepalive & 0xFF));
    tx_string(client, config->client_id);
    if (has_user)
        tx_string(client, config->username);
    if (has_pass)
        tx_string(client, config->password);
    return true;
}

static bool send_subscribe(struct mqtt_client *client)
{
    const char *topic = client->config.subscribe_topic;
    size_t length = 2 + string_field(topic) + 1;
    if (!tx_room(client, length + 5))
        return false;
    uint16_t packet_id = client->next_packet_id++;
    if (client->next_packet_id == 0)
        client->next_packet_id = 1;
    tx_byte(client, PKT_SUBSCRIBE | 0x02);
    tx_remaining_length(client, length);
    tx_byte(client, (unsigned char)(packet_id >> 8));
    tx_byte(client, (unsigned char)(packet_id & 0xFF));
    tx_string(client, topic);
    tx_byte(client, 0); /* QoS 0: the pump republishes every 30 s anyway */
    return true;
}

static bool send_publish(struct mqtt_client *client, struct mqtt_queued *message)
{
    size_t length = string_field(message->topic) + 2 + strlen(message->payload);
    if (!tx_room(client, length + 5))
        return false;
    /* QoS 1. The integration uses QoS 2, but every write here is an absolute
     * setpoint, so a duplicate delivery is indistinguishable from the
     * original - and one less handshake is one less way to wedge. */
    tx_byte(client, PKT_PUBLISH | 0x02);
    tx_remaining_length(client, length);
    tx_string(client, message->topic);
    tx_byte(client, (unsigned char)(message->packet_id >> 8));
    tx_byte(client, (unsigned char)(message->packet_id & 0xFF));
    tx_bytes(client, message->payload, strlen(message->payload));
    message->in_flight = true;
    return true;
}

static void send_pingreq(struct mqtt_client *client)
{
    if (!tx_room(client, 2))
        return;
    tx_byte(client, PKT_PINGREQ);
    tx_byte(client, 0);
    client->ping_outstanding = true;
    client->last_ping = now_monotonic();
}

static void send_puback(struct mqtt_client *client, uint16_t packet_id)
{
    if (!tx_room(client, 4))
        return;
    tx_byte(client, PKT_PUBACK);
    tx_byte(client, 2);
    tx_byte(client, (unsigned char)(packet_id >> 8));
    tx_byte(client, (unsigned char)(packet_id & 0xFF));
}

bool mqtt_publish(struct mqtt_client *client, const char *topic, const char *payload)
{
    if (client->queue_count >= MQTT_QUEUE_MAX) {
        log_warn("mqtt: publish queue full, dropping write to %s", topic);
        return false;
    }
    if (strlen(topic) >= MQTT_TOPIC_MAX || strlen(payload) >= MQTT_PAYLOAD_MAX) {
        log_warn("mqtt: publish too large for %s", topic);
        return false;
    }
    int index = (client->queue_head + client->queue_count) % MQTT_QUEUE_MAX;
    struct mqtt_queued *message = &client->queue[index];
    snprintf(message->topic, sizeof(message->topic), "%s", topic);
    snprintf(message->payload, sizeof(message->payload), "%s", payload);
    message->packet_id = client->next_packet_id++;
    if (client->next_packet_id == 0)
        client->next_packet_id = 1;
    message->in_flight = false;
    client->queue_count++;
    return true;
}

static void drain_queue(struct mqtt_client *client)
{
    while (client->queue_count > 0 && client->phase == MQTT_READY) {
        struct mqtt_queued *message = &client->queue[client->queue_head];
        if (message->in_flight)
            return; /* one at a time, so an ack always means the oldest */
        if (!send_publish(client, message))
            return; /* no room in the write buffer; try again next turn */
        log_info("mqtt: publishing %s to %s", message->payload, message->topic);
        return;
    }
}

static void ack_queue_head(struct mqtt_client *client, uint16_t packet_id)
{
    if (client->queue_count == 0)
        return;
    struct mqtt_queued *message = &client->queue[client->queue_head];
    if (!message->in_flight || message->packet_id != packet_id)
        return;
    client->queue_head = (client->queue_head + 1) % MQTT_QUEUE_MAX;
    client->queue_count--;
}

/* ---- connecting ------------------------------------------------------- */

static int connect_socket(struct mqtt_client *client)
{
    char port[8];
    snprintf(port, sizeof(port), "%u", client->config.port);

    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    struct addrinfo *results = NULL;
    int error = getaddrinfo(client->config.host, port, &hints, &results);
    if (error != 0 || !results) {
        set_error(client, gai_strerror(error));
        return -1;
    }

    int fd = -1;
    for (struct addrinfo *candidate = results; candidate; candidate = candidate->ai_next) {
        fd = socket(candidate->ai_family, candidate->ai_socktype | SOCK_NONBLOCK,
                    candidate->ai_protocol);
        if (fd < 0)
            continue;
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        if (connect(fd, candidate->ai_addr, candidate->ai_addrlen) == 0 ||
            errno == EINPROGRESS)
            break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(results);
    if (fd < 0)
        set_error(client, strerror(errno));
    return fd;
}

/* ---- reading ---------------------------------------------------------- */

/* Decodes a remaining-length field. Returns the number of bytes consumed, 0
 * if more input is needed, or -1 if it is malformed. */
static int decode_remaining_length(const unsigned char *data, size_t available,
                                   size_t *value)
{
    size_t result = 0;
    size_t multiplier = 1;
    for (size_t i = 0; i < 4; i++) {
        if (i >= available)
            return 0;
        unsigned char byte = data[i];
        result += (size_t)(byte & 0x7F) * multiplier;
        if ((byte & 0x80) == 0) {
            *value = result;
            return (int)i + 1;
        }
        multiplier *= 128;
    }
    return -1;
}

static void handle_publish(struct mqtt_client *client, unsigned char header,
                           unsigned char *body, size_t length)
{
    if (length < 2)
        return;
    size_t topic_len = ((size_t)body[0] << 8) | body[1];
    if (2 + topic_len > length)
        return;
    size_t offset = 2 + topic_len;

    unsigned char qos = (header >> 1) & 0x03;
    if (qos > 0) {
        if (offset + 2 > length)
            return;
        uint16_t packet_id = (uint16_t)((body[offset] << 8) | body[offset + 1]);
        offset += 2;
        if (qos == 1)
            send_puback(client, packet_id);
        /* We only ever subscribe at QoS 0, so QoS 2 should not arrive; if it
         * does, treating it as QoS 1 is closer to right than dropping it. */
    }

    size_t payload_len = length - offset;
    if (payload_len >= MQTT_RX_MAX)
        return;
    /* Copied out rather than handed over in place: the decoder terminates the
     * buffer it is given, and in the receive buffer that byte belongs to the
     * next packet. */
    static char payload[MQTT_RX_MAX];
    memcpy(payload, body + offset, payload_len);
    payload[payload_len] = '\0';
    if (client->on_message)
        client->on_message(client->user, payload, payload_len);
}

static void handle_packet(struct mqtt_client *client, unsigned char header,
                          unsigned char *body, size_t length)
{
    unsigned char type = header & 0xF0;
    switch (type) {
    case PKT_CONNACK:
        if (length < 2 || body[1] != 0) {
            char reason[64];
            snprintf(reason, sizeof(reason), "broker refused the connection (code %u)",
                     length >= 2 ? body[1] : 0);
            mqtt_close(client, reason);
            return;
        }
        if (client->phase != MQTT_WAIT_ACK)
            return;
        log_info("mqtt: connected to %s:%u", client->config.host, client->config.port);
        client->phase = MQTT_WAIT_SUB;
        if (!send_subscribe(client))
            mqtt_close(client, "could not send SUBSCRIBE");
        return;

    case PKT_SUBACK:
        if (client->phase != MQTT_WAIT_SUB)
            return;
        /* 0x80 is a subscription failure - usually an ACL on the broker. */
        if (length >= 3 && body[2] == 0x80) {
            mqtt_close(client, "broker refused the subscription");
            return;
        }
        log_info("mqtt: subscribed to %s", client->config.subscribe_topic);
        client->phase = MQTT_READY;
        client->backoff = BACKOFF_MIN;
        client->state_changed = true;
        client->last_error[0] = '\0';
        drain_queue(client);
        return;

    case PKT_PUBLISH:
        handle_publish(client, header, body, length);
        return;

    case PKT_PUBACK:
        if (length >= 2)
            ack_queue_head(client, (uint16_t)((body[0] << 8) | body[1]));
        drain_queue(client);
        return;

    case PKT_PINGRESP:
        client->ping_outstanding = false;
        return;

    default:
        /* Nothing else is expected; ignoring is safer than reacting. */
        return;
    }
}

static void read_available(struct mqtt_client *client)
{
    for (;;) {
        if (client->rx_len >= MQTT_RX_MAX) {
            mqtt_close(client, "receive buffer full");
            return;
        }
        ssize_t got = recv(client->fd, client->rx + client->rx_len,
                           MQTT_RX_MAX - client->rx_len, 0);
        if (got == 0) {
            mqtt_close(client, "broker closed the connection");
            return;
        }
        if (got < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                break;
            if (errno == EINTR)
                continue;
            mqtt_close(client, strerror(errno));
            return;
        }
        client->rx_len += (size_t)got;
        client->last_rx = now_monotonic();
    }

    /* Consume as many whole packets as arrived. */
    size_t consumed = 0;
    while (client->rx_len - consumed >= 2) {
        unsigned char *packet = client->rx + consumed;
        size_t available = client->rx_len - consumed;
        size_t length = 0;
        int header_len = decode_remaining_length(packet + 1, available - 1, &length);
        if (header_len < 0) {
            mqtt_close(client, "malformed packet length");
            return;
        }
        if (header_len == 0)
            break; /* need more bytes */
        size_t total = 1 + (size_t)header_len + length;
        if (total > MQTT_RX_MAX) {
            mqtt_close(client, "packet larger than the receive buffer");
            return;
        }
        if (available < total)
            break;
        handle_packet(client, packet[0], packet + 1 + header_len, length);
        if (client->fd < 0)
            return; /* handle_packet closed us */
        consumed += total;
    }
    if (consumed > 0) {
        memmove(client->rx, client->rx + consumed, client->rx_len - consumed);
        client->rx_len -= consumed;
    }
}

static void write_pending(struct mqtt_client *client)
{
    while (client->tx_sent < client->tx_len) {
        ssize_t sent = send(client->fd, client->tx + client->tx_sent,
                            client->tx_len - client->tx_sent, MSG_NOSIGNAL);
        if (sent < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                return;
            if (errno == EINTR)
                continue;
            mqtt_close(client, strerror(errno));
            return;
        }
        client->tx_sent += (size_t)sent;
    }
    client->tx_len = 0;
    client->tx_sent = 0;
}

void mqtt_service(struct mqtt_client *client, bool readable, bool writable)
{
    double now = now_monotonic();

    if (client->phase == MQTT_IDLE) {
        if (now < client->next_attempt)
            return;
        client->fd = connect_socket(client);
        if (client->fd < 0) {
            log_warn("mqtt: cannot reach %s:%u: %s", client->config.host,
                     client->config.port, client->last_error);
            client->next_attempt = now + client->backoff;
            client->backoff *= 2.0;
            if (client->backoff > BACKOFF_MAX)
                client->backoff = BACKOFF_MAX;
            return;
        }
        client->phase = MQTT_CONNECTING;
        client->last_rx = now;
        return;
    }

    if (client->phase == MQTT_CONNECTING) {
        if (!writable) {
            /* A connect that never completes must not hang the client. */
            if (now - client->last_rx > 20.0)
                mqtt_close(client, "connect timed out");
            return;
        }
        int error = 0;
        socklen_t size = sizeof(error);
        if (getsockopt(client->fd, SOL_SOCKET, SO_ERROR, &error, &size) != 0 || error != 0) {
            mqtt_close(client, strerror(error ? error : errno));
            return;
        }
        client->phase = MQTT_WAIT_ACK;
        client->last_rx = now;
        if (!send_connect(client)) {
            mqtt_close(client, "could not build CONNECT");
            return;
        }
        write_pending(client);
        return;
    }

    if (readable)
        read_available(client);
    if (client->fd < 0)
        return;

    if (client->phase == MQTT_READY)
        drain_queue(client);

    if (writable || client->tx_sent < client->tx_len)
        write_pending(client);
    if (client->fd < 0)
        return;

    /* Keepalive. A silent broker is indistinguishable from a dead one, so
     * ping, and give up if the ping is not answered. */
    double keepalive = client->config.keepalive;
    if (client->phase == MQTT_READY) {
        if (client->ping_outstanding && now - client->last_ping > keepalive) {
            mqtt_close(client, "no PINGRESP from the broker");
            return;
        }
        if (!client->ping_outstanding && now - client->last_rx > keepalive / 2.0) {
            send_pingreq(client);
            write_pending(client);
        }
    } else if (now - client->last_rx > 20.0) {
        mqtt_close(client, "broker did not answer the handshake");
    }
}

double mqtt_next_deadline(const struct mqtt_client *client)
{
    double now = now_monotonic();
    if (client->phase == MQTT_IDLE) {
        double wait = client->next_attempt - now;
        return wait > 0 ? wait : 0.0;
    }
    /* Often enough to keep the keepalive honest without spinning. */
    return 1.0;
}
