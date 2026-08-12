#include "http.h"

#include <errno.h>
#include <fcntl.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <unistd.h>

#include "assets.h"
#include "json.h"
#include "registers.h"
#include "util.h"
#include "widget.h"

/* A request that has not finished arriving by then is not going to. */
#define REQUEST_TIMEOUT 15.0

/* ---- helpers ---------------------------------------------------------- */

static void connection_close(struct http_connection *connection)
{
    if (connection->fd >= 0)
        close(connection->fd);
    connection->fd = -1;
    connection->in_use = false;
    connection->request_len = 0;
    connection->headers_done = false;
    connection->response_len = 0;
    connection->response_sent = 0;
}

static void begin_response(struct buf *out, int status, const char *reason,
                           const char *content_type, size_t length)
{
    buf_printf(out, "HTTP/1.1 %d %s\r\n", status, reason);
    buf_printf(out, "Content-Type: %s\r\n", content_type);
    buf_printf(out, "Content-Length: %zu\r\n", length);
    /* The UI polls; nothing here may be cached. */
    buf_str(out, "Cache-Control: no-store\r\n");
    buf_str(out, "Connection: close\r\n");
    /* This serves a private LAN service and never authenticates anything, but
     * these cost nothing and stop a stray page from framing or sniffing it. */
    buf_str(out, "X-Content-Type-Options: nosniff\r\n");
    buf_str(out, "X-Frame-Options: SAMEORIGIN\r\n");
    buf_str(out, "\r\n");
}

/* Body is built first, then the headers are prepended, because Content-Length
 * has to be known up front and chunked encoding is not worth the code. */
static void finish_body(struct buf *out, struct buf *body, int status,
                        const char *reason, const char *content_type)
{
    begin_response(out, status, reason, content_type, body->len);
    buf_add(out, body->data, body->len);
}

static void send_simple(struct buf *out, int status, const char *reason,
                        const char *message)
{
    char json[256];
    int length = snprintf(json, sizeof(json), "{\"error\": \"%s\"}\n", message);
    begin_response(out, status, reason, "application/json", (size_t)length);
    buf_add(out, json, (size_t)length);
}

/* ---- JSON writers ----------------------------------------------------- */

/* The domain a register is presented as: the control if it has one, else the
 * sensor domain it belongs to. Registers that are both a sensor and a number
 * are shown as the number, which is what the dashboard card does. */
static const char *primary_domain(const struct reg_def *def)
{
    enum control control = reg_control(def->type);
    if (control != CTRL_NONE)
        return control_name(control);
    if (def->type == RT_BINARY_SENSOR)
        return "binary_sensor";
    return "sensor";
}

static void write_status(struct buf *body, const struct http_server *server)
{
    const struct config *config = server->config;
    const struct pump_state *state = server->state;
    double age = state_seconds_since_message(state);

    buf_str(body, "{\"id\": ");
    buf_json_string(body, config->id_name);
    buf_printf(body, ", \"available\": %s", state_available(state) ? "true" : "false");
    /* Honest even in demo mode, where there is no broker at all; the UI
     * shows the demo badge instead of a connection pill. */
    buf_printf(body, ", \"connected\": %s",
               mqtt_connected(server->mqtt) ? "true" : "false");
    buf_printf(body, ", \"demo\": %s", config->demo ? "true" : "false");
    buf_printf(body, ", \"read_only\": %s", config->read_only ? "true" : "false");
    buf_printf(body, ", \"debug_writes\": %s", config->debug_writes ? "true" : "false");
    buf_printf(body, ", \"messages\": %llu", (unsigned long long)state->message_count);
    buf_printf(body, ", \"rejected\": %llu", (unsigned long long)state->rejected_count);
    if (age < 0)
        buf_str(body, ", \"seconds_since_message\": null");
    else
        buf_printf(body, ", \"seconds_since_message\": %.1f", age);
    buf_str(body, ", \"language\": ");
    buf_json_string(body, config->language_code);
    buf_str(body, ", \"last_error\": ");
    buf_json_string(body, state->last_error);
    buf_str(body, "}");
}

static void route_state(struct buf *out, const struct http_server *server)
{
    static char storage[HTTP_RESPONSE_MAX];
    struct buf body = buf_wrap(storage, sizeof(storage));

    buf_str(&body, "{\"status\": ");
    write_status(&body, server);
    buf_str(&body, ", \"values\": {");
    for (int i = 0; i < REGISTER_COUNT; i++) {
        const struct reg_def *def = &REGISTERS[i];
        char state_string[STATE_STRING_MAX];
        state_entity_string(server->state, primary_domain(def), def, state_string,
                            sizeof(state_string));
        if (i)
            buf_str(&body, ", ");
        buf_json_string(&body, def->key);
        buf_str(&body, ": {\"state\": ");
        buf_json_string(&body, state_string);
        buf_str(&body, "}");
    }
    buf_str(&body, "}}\n");
    finish_body(out, &body, 200, "OK", "application/json");
}

static void route_registers(struct buf *out, const struct http_server *server)
{
    static char storage[HTTP_RESPONSE_MAX];
    struct buf body = buf_wrap(storage, sizeof(storage));
    int language = server->config->language;

    buf_str(&body, "{\"panels\": [");
    for (int i = 0; i < PANEL_COUNT; i++) {
        if (i)
            buf_str(&body, ", ");
        buf_printf(&body, "{\"panel\": %u, \"title\": ", PANEL_ORDER[i]);
        buf_json_string(&body, panel_title(PANEL_ORDER[i]));
        buf_str(&body, "}");
    }
    buf_str(&body, "], \"registers\": [");

    /* Emitted in panel order so the UI can render straight through. */
    bool first = true;
    for (int p = 0; p < PANEL_COUNT; p++) {
        for (int pass = 0; pass < 2; pass++) {
            for (int i = 0; i < REGISTER_COUNT; i++) {
                const struct reg_def *def = &REGISTERS[i];
                if (def->panel != PANEL_ORDER[p])
                    continue;
                /* Entries the card gives an explicit order to come first. */
                bool ordered = def->panel_order > 0;
                if ((pass == 0) != ordered)
                    continue;

                const char *unit = NULL;
                enum device_class device_class = DC_NONE;
                reg_presentation(def, &unit, &device_class);
                enum control control = reg_control(def->type);

                if (!first)
                    buf_str(&body, ", ");
                first = false;
                buf_str(&body, "{\"key\": ");
                buf_json_string(&body, def->key);
                buf_str(&body, ", \"name\": ");
                buf_json_string(&body, reg_name(def, language));
                buf_str(&body, ", \"register\": ");
                buf_json_string(&body, def->reg);
                buf_str(&body, ", \"domain\": ");
                buf_json_string(&body, primary_domain(def));
                buf_printf(&body, ", \"panel\": %u, \"order\": %u", def->panel,
                           def->panel_order);
                if (unit) {
                    buf_str(&body, ", \"unit\": ");
                    buf_json_string(&body, unit);
                }
                if (device_class_name(device_class)) {
                    buf_str(&body, ", \"device_class\": ");
                    buf_json_string(&body, device_class_name(device_class));
                }
                if (control != CTRL_NONE) {
                    buf_str(&body, ", \"control\": ");
                    buf_json_string(&body, control_name(control));
                }
                if (def->has_bounds)
                    buf_printf(&body, ", \"minimum\": %g, \"maximum\": %g", def->minimum,
                               def->maximum);
                if (control == CTRL_SELECT) {
                    buf_str(&body, ", \"options\": [");
                    for (int mode = 0; mode < MODE_COUNT; mode++) {
                        char label[STATE_STRING_MAX];
                        if (!state_mode_option(language, mode, label, sizeof(label)))
                            continue;
                        if (mode)
                            buf_str(&body, ", ");
                        buf_json_string(&body, label);
                    }
                    buf_str(&body, "]");
                }
                buf_str(&body, "}");
            }
        }
    }
    buf_str(&body, "]}\n");
    finish_body(out, &body, 200, "OK", "application/json");
}

static void route_widget(struct buf *out, const struct http_server *server)
{
    static char storage[HTTP_RESPONSE_MAX];
    static char scratch[64 * STATE_STRING_MAX];
    const char *states[64];

    struct buf body = buf_wrap(storage, sizeof(storage));
    state_widget_states(server->state, states, scratch, sizeof(scratch));
    widget_render(&body, states);
    finish_body(out, &body, 200, "OK", "text/html; charset=utf-8");
}

static void route_metrics(struct buf *out, const struct http_server *server)
{
    static char storage[HTTP_RESPONSE_MAX];
    struct buf body = buf_wrap(storage, sizeof(storage));
    const struct pump_state *state = server->state;
    bool available = state_available(state);

    buf_str(&body, "# HELP thermiq_up Whether recent data has arrived from the pump.\n");
    buf_str(&body, "# TYPE thermiq_up gauge\n");
    buf_printf(&body, "thermiq_up %d\n", available ? 1 : 0);
    buf_str(&body, "# HELP thermiq_mqtt_connected Whether the broker is connected.\n");
    buf_str(&body, "# TYPE thermiq_mqtt_connected gauge\n");
    buf_printf(&body, "thermiq_mqtt_connected %d\n",
               mqtt_connected(server->mqtt) ? 1 : 0);
    buf_str(&body, "# HELP thermiq_messages_total Messages decoded from the pump.\n");
    buf_str(&body, "# TYPE thermiq_messages_total counter\n");
    buf_printf(&body, "thermiq_messages_total %llu\n",
               (unsigned long long)state->message_count);
    buf_str(&body, "# HELP thermiq_rejected_total Payloads ignored as not ours.\n");
    buf_str(&body, "# TYPE thermiq_rejected_total counter\n");
    buf_printf(&body, "thermiq_rejected_total %llu\n",
               (unsigned long long)state->rejected_count);
    double age = state_seconds_since_message(state);
    if (age >= 0) {
        buf_str(&body, "# HELP thermiq_last_message_age_seconds Age of the last "
                       "message.\n");
        buf_str(&body, "# TYPE thermiq_last_message_age_seconds gauge\n");
        buf_printf(&body, "thermiq_last_message_age_seconds %.1f\n", age);
    }

    /* Numeric registers only: a text status has no place in a time series. */
    buf_str(&body, "# HELP thermiq_register Register value, labelled by key.\n");
    buf_str(&body, "# TYPE thermiq_register gauge\n");
    for (int i = 0; i < REGISTER_COUNT; i++) {
        const struct reg_def *def = &REGISTERS[i];
        const struct reg_value *value = state_value(server->state, def);
        if (!value || (value->kind != VAL_INT && value->kind != VAL_FLOAT))
            continue;
        double reading = value->number;
        if (def->type == RT_BINARY_SENSOR && def->has_bitmask)
            reading = ((uint32_t)((int64_t)reading & 0xFFFF) & def->bitmask) ? 1 : 0;
        buf_printf(&body, "thermiq_register{key=\"%s\",register=\"%s\"} %g\n", def->key,
                   def->reg, reading);
    }
    finish_body(out, &body, 200, "OK", "text/plain; version=0.0.4; charset=utf-8");
}

static void route_health(struct buf *out, const struct http_server *server)
{
    static char storage[4096];
    struct buf body = buf_wrap(storage, sizeof(storage));
    bool healthy = server->config->demo || state_available(server->state);
    write_status(&body, server);
    buf_str(&body, "\n");
    finish_body(out, &body, healthy ? 200 : 503, healthy ? "OK" : "Service Unavailable",
               "application/json");
}

static void route_write(struct buf *out, struct http_server *server, char *body,
                        size_t body_len)
{
    if (server->config->read_only) {
        send_simple(out, 403, "Forbidden", "the bridge is running in read-only mode");
        return;
    }

    static struct json_member members[JSON_MAX_MEMBERS];
    int count = json_parse_object(body, body_len, members, JSON_MAX_MEMBERS);
    if (count < 0) {
        send_simple(out, 400, "Bad Request", "body must be a JSON object");
        return;
    }
    const struct json_member *key = json_get(members, count, "key");
    if (!key || key->kind != JSON_STRING) {
        send_simple(out, 400, "Bad Request", "key must be a string");
        return;
    }
    const struct reg_def *def = reg_find(key->string);
    if (!def) {
        send_simple(out, 404, "Not Found", "no such register");
        return;
    }

    double value = 0;
    const struct json_member *option = json_get(members, count, "option");
    const struct json_member *raw = json_get(members, count, "value");
    if (option && option->kind == JSON_STRING) {
        if (!state_mode_value(server->config->language, option->string, &value)) {
            send_simple(out, 400, "Bad Request", "not one of the available options");
            return;
        }
    } else if (raw && raw->kind == JSON_NUMBER) {
        value = raw->number;
    } else if (raw && raw->kind == JSON_BOOL) {
        value = raw->boolean ? 1 : 0;
    } else {
        send_simple(out, 400, "Bad Request", "value must be a number, or option a string");
        return;
    }

    struct write_topics topics = {server->config->write_topic, server->config->set_topic,
                                  server->config->hexformat};
    struct write_message message;
    enum write_error error =
        state_encode_write(server->state, &topics, key->string, value, &message);
    if (error != WRITE_OK) {
        log_warn("write to %s refused: %s", key->string, write_error_message(error));
        send_simple(out, 400, "Bad Request", write_error_message(error));
        return;
    }
    if (!server->config->demo &&
        !mqtt_publish(server->mqtt, message.topic, message.payload)) {
        send_simple(out, 503, "Service Unavailable", "the publish queue is full");
        return;
    }
    /* Show the new value straight away, exactly as the integration's entities
     * do; the pump's next message confirms or corrects it. */
    state_set_number(server->state, def, message.applied);

    static char storage[512];
    struct buf response = buf_wrap(storage, sizeof(storage));
    buf_str(&response, "{\"key\": ");
    buf_json_string(&response, key->string);
    buf_printf(&response, ", \"value\": %g, \"topic\": ", message.applied);
    /* Demo mode has no broker: the value is applied locally so the UI still
     * responds, and the reply says where it would have gone. */
    buf_json_string(&response, server->config->demo ? "(demo, not published)"
                                                    : message.topic);
    buf_str(&response, "}\n");
    finish_body(out, &response, 200, "OK", "application/json");
}

/* ---- request handling ------------------------------------------------- */

static void handle_request(struct http_server *server, struct http_connection *connection)
{
    struct buf out = buf_wrap(connection->response, HTTP_RESPONSE_MAX);

    char *request = connection->request;
    char *space = strchr(request, ' ');
    if (!space) {
        send_simple(&out, 400, "Bad Request", "malformed request line");
        goto done;
    }
    *space = '\0';
    const char *method = request;
    char *path = space + 1;
    char *path_end = strpbrk(path, " ?\r\n");
    if (!path_end) {
        send_simple(&out, 400, "Bad Request", "malformed request line");
        goto done;
    }
    char terminator = *path_end;
    *path_end = '\0';
    (void)terminator;

    bool is_get = strcmp(method, "GET") == 0 || strcmp(method, "HEAD") == 0;
    log_debug("http %s %s", method, path);

    if (is_get && strcmp(path, "/api/state") == 0) {
        route_state(&out, server);
    } else if (is_get && strcmp(path, "/api/registers") == 0) {
        route_registers(&out, server);
    } else if (is_get && strcmp(path, "/api/widget") == 0) {
        route_widget(&out, server);
    } else if (is_get && strcmp(path, "/metrics") == 0) {
        route_metrics(&out, server);
    } else if (is_get && (strcmp(path, "/healthz") == 0 || strcmp(path, "/health") == 0)) {
        route_health(&out, server);
    } else if (strcmp(method, "POST") == 0 && strcmp(path, "/api/write") == 0) {
        char *body = connection->request + connection->header_len;
        route_write(&out, server, body, connection->content_length);
    } else if (is_get) {
        const struct asset *found = NULL;
        for (int i = 0; i < ASSET_COUNT; i++) {
            if (strcmp(ASSETS[i].path, path) == 0) {
                found = &ASSETS[i];
                break;
            }
        }
        if (found) {
            begin_response(&out, 200, "OK", found->content_type, found->len);
            buf_add(&out, (const char *)found->data, found->len);
        } else {
            send_simple(&out, 404, "Not Found", "no such path");
        }
    } else {
        send_simple(&out, 405, "Method Not Allowed", "unsupported method");
    }

done:
    if (out.truncated) {
        /* Better an honest error than a half-written page. */
        log_error("http: response did not fit the buffer");
        buf_reset(&out);
        send_simple(&out, 500, "Internal Server Error", "response too large");
    }
    connection->response_len = out.len;
    connection->response_sent = 0;
}

/* Returns true when the whole request (headers and body) has arrived. */
bool http_scan_headers(const char *request, size_t len, size_t *header_len,
                       size_t *content_length)
{
    const char *end = NULL;
    size_t skip = 0;
    for (size_t i = 0; i + 1 < len; i++) {
        if (i + 3 < len && memcmp(request + i, "\r\n\r\n", 4) == 0) {
            end = request + i;
            skip = 4;
            break;
        }
        if (memcmp(request + i, "\n\n", 2) == 0) {
            end = request + i;
            skip = 2;
            break;
        }
    }
    if (!end)
        return false;

    *header_len = (size_t)(end - request) + skip;
    *content_length = 0;

    /* Scan the header block. The final header's newline is the first byte of
     * the blank-line terminator, so the bound has to include it - a stricter
     * bound skips the last header, which is where Content-Length usually is. */
    const char *limit = end + 1;
    for (const char *line = request; line < limit;) {
        if ((size_t)(limit - line) > 15 && strncasecmp(line, "content-length:", 15) == 0) {
            long value = strtol(line + 15, NULL, 10);
            if (value > 0)
                *content_length = (size_t)value;
        }
        const char *newline = memchr(line, '\n', (size_t)(limit - line));
        if (!newline)
            break;
        line = newline + 1;
    }
    return true;
}

static bool request_complete(struct http_connection *connection)
{
    if (!connection->headers_done) {
        connection->request[connection->request_len] = '\0';
        if (!http_scan_headers(connection->request, connection->request_len,
                               &connection->header_len, &connection->content_length))
            return false;
        connection->headers_done = true;
        if (connection->content_length > HTTP_REQUEST_MAX - connection->header_len - 1)
            connection->content_length = 0; /* refuse to buffer more than we can */
    }
    return connection->request_len >= connection->header_len + connection->content_length;
}

/* ---- socket plumbing --------------------------------------------------- */

bool http_start(struct http_server *server, const struct config *config,
                struct pump_state *state, struct mqtt_client *mqtt)
{
    memset(server, 0, sizeof(*server));
    server->config = config;
    server->state = state;
    server->mqtt = mqtt;
    for (int i = 0; i < HTTP_MAX_CONNECTIONS; i++) {
        server->connections[i].fd = -1;
        server->connections[i].response = server->response_storage[i];
    }

    server->listen_fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
    if (server->listen_fd < 0) {
        log_error("http: socket: %s", strerror(errno));
        return false;
    }
    int one = 1;
    setsockopt(server->listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons(config->http_port);
    if (strcmp(config->http_host, "0.0.0.0") == 0) {
        address.sin_addr.s_addr = htonl(INADDR_ANY);
    } else if (inet_pton(AF_INET, config->http_host, &address.sin_addr) != 1) {
        log_error("http: THERMIQ_HTTP_HOST is not an IPv4 address: %s", config->http_host);
        close(server->listen_fd);
        server->listen_fd = -1;
        return false;
    }
    if (bind(server->listen_fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        log_error("http: cannot bind %s:%u: %s", config->http_host, config->http_port,
                  strerror(errno));
        close(server->listen_fd);
        server->listen_fd = -1;
        return false;
    }
    if (listen(server->listen_fd, 16) != 0) {
        log_error("http: listen: %s", strerror(errno));
        close(server->listen_fd);
        server->listen_fd = -1;
        return false;
    }
    return true;
}

void http_stop(struct http_server *server)
{
    for (int i = 0; i < HTTP_MAX_CONNECTIONS; i++)
        if (server->connections[i].in_use)
            connection_close(&server->connections[i]);
    if (server->listen_fd >= 0)
        close(server->listen_fd);
    server->listen_fd = -1;
}

void http_service_listener(struct http_server *server)
{
    for (;;) {
        int fd = accept(server->listen_fd, NULL, NULL);
        if (fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
                return;
            log_debug("http: accept: %s", strerror(errno));
            return;
        }
        int slot = -1;
        for (int i = 0; i < HTTP_MAX_CONNECTIONS; i++) {
            if (!server->connections[i].in_use) {
                slot = i;
                break;
            }
        }
        if (slot < 0) {
            /* Every slot busy: refuse rather than queue unboundedly. */
            log_warn("http: too many connections, refusing one");
            close(fd);
            continue;
        }
        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

        struct http_connection *connection = &server->connections[slot];
        connection->fd = fd;
        connection->in_use = true;
        connection->started = now_monotonic();
        connection->request_len = 0;
        connection->headers_done = false;
        connection->header_len = 0;
        connection->content_length = 0;
        connection->response_len = 0;
        connection->response_sent = 0;
    }
}

void http_service_connection(struct http_server *server, int index, bool readable,
                             bool writable)
{
    struct http_connection *connection = &server->connections[index];
    if (!connection->in_use)
        return;

    if (readable && connection->response_len == 0) {
        for (;;) {
            size_t room = HTTP_REQUEST_MAX - connection->request_len - 1;
            if (room == 0) {
                struct buf out = buf_wrap(connection->response, HTTP_RESPONSE_MAX);
                send_simple(&out, 431, "Request Header Fields Too Large", "request too "
                                                                         "large");
                connection->response_len = out.len;
                break;
            }
            ssize_t got = recv(connection->fd, connection->request + connection->request_len,
                               room, 0);
            if (got == 0) {
                connection_close(connection);
                return;
            }
            if (got < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK)
                    break;
                if (errno == EINTR)
                    continue;
                connection_close(connection);
                return;
            }
            connection->request_len += (size_t)got;
        }

        if (connection->response_len == 0 && request_complete(connection))
            handle_request(server, connection);
    }

    if (connection->response_len > 0 && (writable || readable)) {
        while (connection->response_sent < connection->response_len) {
            ssize_t sent = send(connection->fd,
                                connection->response + connection->response_sent,
                                connection->response_len - connection->response_sent,
                                MSG_NOSIGNAL);
            if (sent < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK)
                    return;
                if (errno == EINTR)
                    continue;
                connection_close(connection);
                return;
            }
            connection->response_sent += (size_t)sent;
        }
        connection_close(connection);
    }
}

void http_expire(struct http_server *server)
{
    double now = now_monotonic();
    for (int i = 0; i < HTTP_MAX_CONNECTIONS; i++) {
        struct http_connection *connection = &server->connections[i];
        if (connection->in_use && now - connection->started > REQUEST_TIMEOUT) {
            log_debug("http: closing an idle connection");
            connection_close(connection);
        }
    }
}
