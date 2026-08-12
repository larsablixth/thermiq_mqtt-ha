/* thermiq-bridge: a Thermia/Danfoss heat pump on MQTT, with a web UI and an
 * API, and no Home Assistant.
 *
 * One process, one thread, one poll loop. The MQTT client and the HTTP server
 * are state machines serviced from the same loop, so the pump state is never
 * read while it is being written and there is nothing to lock. Nothing is
 * allocated after startup: every buffer is fixed and bounded.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include "config.h"
#include "http.h"
#include "mqtt.h"
#include "registers.h"
#include "state.h"
#include "util.h"

static volatile sig_atomic_t stop_requested;

static void on_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

/* Long-lived, and large enough that keeping them off the stack matters. */
static struct config config;
static struct pump_state pump;
static struct mqtt_client mqtt;
static struct http_server http;

/* ---- republishing ------------------------------------------------------ */

/* Last value published per register, so an unchanged reading is not
 * republished 140 times every 30 seconds. */
static char published[REGISTER_MAX][STATE_STRING_MAX];

static const char *presented_domain(const struct reg_def *def)
{
    enum control control = reg_control(def->type);
    if (control != CTRL_NONE)
        return control_name(control);
    if (def->type == RT_BINARY_SENSOR)
        return "binary_sensor";
    return "sensor";
}

static void republish_changed(void)
{
    if (!config.publish_prefix[0] || !mqtt_connected(&mqtt))
        return;
    for (int i = 0; i < REGISTER_COUNT; i++) {
        const struct reg_def *def = &REGISTERS[i];
        char current[STATE_STRING_MAX];
        state_entity_string(&pump, presented_domain(def), def, current, sizeof(current));
        if (strcmp(current, "unknown") == 0 || strcmp(published[i], current) == 0)
            continue;
        char topic[CONFIG_TOPIC_MAX + 64];
        snprintf(topic, sizeof(topic), "%s/%s", config.publish_prefix, def->key);
        if (!mqtt_publish(&mqtt, topic, current))
            return; /* queue full: the next message will try again */
        snprintf(published[i], STATE_STRING_MAX, "%s", current);
    }
}

/* ---- demo mode --------------------------------------------------------- */

struct demo_value {
    const char *key;
    double number;
    const char *text;
    int boolean; /* -1 when not a binary register */
};

/* The scenario the project's README image has always shown: compressor and
 * both pumps running, no hot water, Optimum fitted. */
static const struct demo_value DEMO_VALUES[] = {
    {"outdoor_t", -3, NULL, -1},        {"indoor_t", 21, NULL, -1},
    {"supplyline_t", 38, NULL, -1},     {"returnline_t", 32, NULL, -1},
    {"boiler_t", 52, NULL, -1},         {"brine_out_t", -1, NULL, -1},
    {"brine_in_t", 2, NULL, -1},        {"supply_shunt_t", 38, NULL, -1},
    {"pressurepipe_t", 78, NULL, -1},   {"hgw_water_t", 45, NULL, -1},
    {"heating_stop_t", 17, NULL, -1},   {"brine_pump_speed", 65, NULL, -1},
    {"supply_pump_speed", 70, NULL, -1}, {"integral2_curve_target", 28, NULL, -1},
    {"indoor_requested_t", 21, NULL, -1}, {"main_mode", 1, NULL, -1},
    {"compressor_runtime_h", 12045, NULL, -1},
    {"hotwater_runtime_h", 3120, NULL, -1},
    {"time_str", 0, "demo data", -1},   {"communication_status", 0, "Ok", -1},
    {"compressor_on", 0, NULL, 1},      {"brine_pump_on", 0, NULL, 1},
    {"supply_pump_on", 0, NULL, 1},     {"opt_hgw_installed", 0, NULL, 1},
    {"opt_optimum_installed", 0, NULL, 1}, {"hotwaterproduction_on", 0, NULL, 0},
    {"alarm_indication_on", 0, NULL, 0}, {"boiler_3kw_on", 0, NULL, 0},
    {"boiler_6kw_on", 0, NULL, 0},      {"heatpump_evu_block", 0, NULL, 0},
};

static void seed_demo(void)
{
    for (size_t i = 0; i < sizeof(DEMO_VALUES) / sizeof(DEMO_VALUES[0]); i++) {
        const struct demo_value *demo = &DEMO_VALUES[i];
        const struct reg_def *def = reg_find(demo->key);
        if (!def) {
            log_warn("demo: no register named %s", demo->key);
            continue;
        }
        if (demo->boolean >= 0) {
            /* Binary registers live as bits in a shared status word. */
            const struct reg_value *current = state_value(&pump, def);
            double word = current && current->kind != VAL_NONE ? current->number : 0;
            uint32_t bits = (uint32_t)((int64_t)word & 0xFFFF);
            bits = demo->boolean ? (bits | def->bitmask) : (bits & ~def->bitmask);
            state_set_demo_number(&pump, def, bits, true);
        } else if (demo->text) {
            /* No public setter for text; the demo table only needs two. */
            state_set_demo_text(&pump, def, demo->text);
        } else {
            /* Whole demo readings are integers, as they arrive from a pump;
             * a float would render as "-3.0" where the real thing says "-3". */
            state_set_demo_number(&pump, def, demo->number,
                                  demo->number == (double)(long long)demo->number);
        }
    }
    pump.message_count = 1;
    pump.last_message = now_monotonic();
    log_warn("demo mode: serving canned values, no broker connection");
}

/* ---- MQTT callback ------------------------------------------------------ */

static void on_mqtt_message(void *user, char *payload, size_t len)
{
    (void)user;
    int stored = state_ingest(&pump, payload, len);
    if (stored < 0)
        return;
    log_debug("decoded %d registers (message %llu)", stored,
              (unsigned long long)pump.message_count);
    republish_changed();
}

/* ---- health check ------------------------------------------------------- */

/* A scratch image has no shell and no curl, so the binary probes itself:
 * `thermiq-bridge --healthcheck` is what the image's HEALTHCHECK runs. Exits
 * 0 when /healthz answers 200, 1 otherwise. */
static int run_healthcheck(void)
{
    /* Deliberately independent of config_load: the probe needs a port and
     * nothing else, and it must not fail because a broker host is unset. */
    unsigned long port = 8080;
    const char *from_env = getenv("THERMIQ_HTTP_PORT");
    if (from_env && *from_env) {
        char *end = NULL;
        unsigned long parsed = strtoul(from_env, &end, 10);
        if (end != from_env && *end == '\0' && parsed >= 1 && parsed <= 65535)
            port = parsed;
    }

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return 1;

    struct timeval timeout = {3, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
    /* Always the loopback: the probe runs inside the container, and the
     * configured bind address may be a wildcard. */
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    int status = 1;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0) {
        static const char request[] = "GET /healthz HTTP/1.0\r\n"
                                      "Host: localhost\r\n"
                                      "Connection: close\r\n\r\n";
        if (send(fd, request, sizeof(request) - 1, 0) > 0) {
            char reply[64];
            ssize_t got = recv(fd, reply, sizeof(reply) - 1, 0);
            if (got > 0) {
                reply[got] = '\0';
                if (strncmp(reply, "HTTP/1.1 200", 12) == 0)
                    status = 0;
            }
        }
    }
    close(fd);
    return status;
}

/* ---- main -------------------------------------------------------------- */

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--healthcheck") == 0)
        return run_healthcheck();
    if (argc > 1) {
        fprintf(stderr, "usage: thermiq-bridge [--healthcheck]\n"
                        "everything else is configured through the environment; "
                        "see the README\n");
        return 2;
    }

    char error[256];
    if (!config_load(&config, error, sizeof(error))) {
        fprintf(stderr, "configuration error: %s\n", error);
        return 2;
    }
    log_set_level((enum log_level)config.log_level);
    log_info("thermiq-bridge starting");
    config_log_summary(&config);

    /* A broker that vanishes mid-write must not kill the process. */
    signal(SIGPIPE, SIG_IGN);
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = on_signal;
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);

    state_init(&pump, config.availability_timeout, config.language);
    pump.demo = config.demo;
    if (config.demo)
        seed_demo();

    struct mqtt_config mqtt_config = {
        .host = config.mqtt_host,
        .port = config.mqtt_port,
        .client_id = config.mqtt_client_id,
        .username = config.mqtt_username,
        .password = config.mqtt_password,
        .subscribe_topic = config.data_topic,
        .keepalive = 60,
    };
    mqtt_init(&mqtt, &mqtt_config, on_mqtt_message, NULL);

    if (!http_start(&http, &config, &pump, &mqtt))
        return 1;
    log_info("ready: http://%s:%u/", config.http_host, config.http_port);

    while (!stop_requested) {
        struct pollfd fds[2 + HTTP_MAX_CONNECTIONS];
        int count = 0;

        int listen_index = count;
        fds[count].fd = http.listen_fd;
        fds[count].events = POLLIN;
        fds[count].revents = 0;
        count++;

        int mqtt_index = -1;
        if (!config.demo) {
            mqtt_service(&mqtt, false, false); /* lets backoff and keepalive run */
            if (mqtt_fd(&mqtt) >= 0) {
                mqtt_index = count;
                fds[count].fd = mqtt_fd(&mqtt);
                fds[count].events = POLLIN | (mqtt_wants_write(&mqtt) ? POLLOUT : 0);
                fds[count].revents = 0;
                count++;
            }
        }

        int connection_index[HTTP_MAX_CONNECTIONS];
        for (int i = 0; i < HTTP_MAX_CONNECTIONS; i++) {
            connection_index[i] = -1;
            struct http_connection *connection = &http.connections[i];
            if (!connection->in_use)
                continue;
            connection_index[i] = count;
            fds[count].fd = connection->fd;
            fds[count].events = connection->response_len > 0 ? POLLOUT : POLLIN;
            fds[count].revents = 0;
            count++;
        }

        double wait = config.demo ? 1.0 : mqtt_next_deadline(&mqtt);
        int timeout_ms = (int)(wait * 1000.0);
        if (timeout_ms < 50)
            timeout_ms = 50;
        if (timeout_ms > 1000)
            timeout_ms = 1000;

        int ready = poll(fds, (nfds_t)count, timeout_ms);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            log_error("poll: %s", strerror(errno));
            break;
        }

        if (fds[listen_index].revents & POLLIN)
            http_service_listener(&http);

        if (mqtt_index >= 0) {
            short events = fds[mqtt_index].revents;
            if (events & (POLLERR | POLLHUP | POLLNVAL))
                mqtt_close(&mqtt, "socket error");
            else if (events)
                mqtt_service(&mqtt, (events & POLLIN) != 0, (events & POLLOUT) != 0);
        }

        for (int i = 0; i < HTTP_MAX_CONNECTIONS; i++) {
            int index = connection_index[i];
            if (index < 0)
                continue;
            short events = fds[index].revents;
            if (events & (POLLERR | POLLNVAL)) {
                http_service_connection(&http, i, false, false);
                continue;
            }
            if (events)
                http_service_connection(&http, i, (events & POLLIN) != 0,
                                        (events & POLLOUT) != 0);
        }
        http_expire(&http);

        if (!config.demo) {
            state_set_connected(&pump, mqtt_connected(&mqtt),
                                mqtt.last_error[0] ? mqtt.last_error : NULL);
            /* Republish anything that changed while the broker was away. */
            if (mqtt.state_changed) {
                mqtt.state_changed = false;
                republish_changed();
            }
        }
    }

    log_info("shutting down");
    http_stop(&http);
    mqtt_close(&mqtt, NULL);
    return 0;
}
