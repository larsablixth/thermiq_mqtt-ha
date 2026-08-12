/* A small MQTT 3.1.1 client: connect, subscribe, publish, keepalive.
 *
 * Only what talking to a Mosquitto on the same LAN needs. It is a state
 * machine driven by the event loop - never blocking, never allocating - and
 * it reconnects by itself with backoff, because a bridge that stops when the
 * broker restarts is not much use.
 *
 * No TLS: the broker this speaks to is a local Mosquitto, and a TLS stack is
 * the single largest thing that could be added to this binary. Put it behind
 * a tunnel if the broker is remote.
 */
#ifndef THERMIQ_MQTT_H
#define THERMIQ_MQTT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MQTT_RX_MAX 8192
#define MQTT_TX_MAX 4096
#define MQTT_QUEUE_MAX 8
#define MQTT_TOPIC_MAX 192
#define MQTT_PAYLOAD_MAX 256

struct mqtt_config {
    const char *host;
    uint16_t port;
    const char *client_id;
    const char *username;
    const char *password;
    const char *subscribe_topic;
    uint16_t keepalive;
};

enum mqtt_phase {
    MQTT_IDLE,      /* waiting for the backoff to expire */
    MQTT_CONNECTING,/* TCP connect in flight */
    MQTT_WAIT_ACK,  /* CONNECT sent, waiting for CONNACK */
    MQTT_WAIT_SUB,  /* SUBSCRIBE sent, waiting for SUBACK */
    MQTT_READY,
};

/* Called for each message on the subscribed topic. `payload` is writable and
 * has one spare byte, so the decoder can work in place. */
typedef void (*mqtt_on_message)(void *user, char *payload, size_t len);

struct mqtt_queued {
    char topic[MQTT_TOPIC_MAX];
    char payload[MQTT_PAYLOAD_MAX];
    uint16_t packet_id;
    bool in_flight;
};

struct mqtt_client {
    struct mqtt_config config;
    int fd;
    enum mqtt_phase phase;

    unsigned char rx[MQTT_RX_MAX];
    size_t rx_len;
    unsigned char tx[MQTT_TX_MAX];
    size_t tx_len;
    size_t tx_sent;

    struct mqtt_queued queue[MQTT_QUEUE_MAX];
    int queue_head;
    int queue_count;

    uint16_t next_packet_id;
    double next_attempt;
    double backoff;
    double last_rx;
    double last_ping;
    bool ping_outstanding;

    mqtt_on_message on_message;
    void *user;
    /* Set whenever the connection state changes, for /healthz and the UI. */
    bool state_changed;
    char last_error[160];
};

void mqtt_init(struct mqtt_client *client, const struct mqtt_config *config,
               mqtt_on_message on_message, void *user);

/* File descriptor to poll, or -1 while backing off. */
int mqtt_fd(const struct mqtt_client *client);
/* True when the client has bytes to write. */
bool mqtt_wants_write(const struct mqtt_client *client);
bool mqtt_connected(const struct mqtt_client *client);

/* Advance the state machine. `readable`/`writable` come from poll(); call it
 * with both false on a timeout to let backoff and keepalive run. */
void mqtt_service(struct mqtt_client *client, bool readable, bool writable);

/* Seconds until the client next needs servicing, for the poll timeout. */
double mqtt_next_deadline(const struct mqtt_client *client);

/* Queue a publish at QoS 1. Returns false if the queue is full. */
bool mqtt_publish(struct mqtt_client *client, const char *topic, const char *payload);

void mqtt_close(struct mqtt_client *client, const char *reason);

#endif /* THERMIQ_MQTT_H */
