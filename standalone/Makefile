# thermiq-bridge
#
# Generated sources are checked in, so a plain `make` needs only a C compiler.
# Run `make generate` after changing the register table, the widget template
# or anything under web/ - CI fails if the checked-in output is stale.

CC ?= cc
PYTHON ?= python3
TARGET ?=

CFLAGS ?= -Os -std=c11 -D_POSIX_C_SOURCE=200809L \
          -Wall -Wextra -Wshadow -Wconversion -Wsign-conversion \
          -Wstrict-prototypes -Wmissing-prototypes -Wcast-qual -Wpointer-arith \
          -fno-common -ffunction-sections -fdata-sections
# Hardening: stack protector and fortified libc calls.
CFLAGS += -fstack-protector-strong -D_FORTIFY_SOURCE=2
LDFLAGS ?= -Wl,--gc-sections
LDLIBS ?= -lm

ifneq ($(TARGET),)
CFLAGS += -target $(TARGET)
LDFLAGS += -target $(TARGET)
endif

SRC := src/main.c src/config.c src/http.c src/json.c src/mqtt.c src/registers.c \
       src/registers_gen.c src/state.c src/util.c src/widget.c src/widget_gen.c \
       src/assets_gen.c
LIB := $(filter-out src/main.c,$(SRC))

BUILD := build
BIN := $(BUILD)/thermiq-bridge

.PHONY: all generate check-generated test clean run-demo

all: $(BIN)

$(BIN): $(SRC) | $(BUILD)
	$(CC) $(CFLAGS) -Isrc -o $@ $(SRC) $(LDFLAGS) $(LDLIBS)

$(BUILD):
	mkdir -p $(BUILD)

generate:
	$(PYTHON) codegen/gen_registers.py
	$(PYTHON) codegen/gen_widget.py
	$(PYTHON) codegen/gen_assets.py
	$(PYTHON) codegen/gen_widget_cases.py

check-generated:
	$(PYTHON) codegen/gen_registers.py --check
	$(PYTHON) codegen/gen_widget.py --check
	$(PYTHON) codegen/gen_assets.py --check

# The widget cases are rendered by real Jinja2; the C renderer must match them
# byte for byte. That is what makes transpiling the template safe.
test: | $(BUILD)
	$(PYTHON) codegen/gen_widget_cases.py
	$(CC) $(CFLAGS) -Isrc -Itests -o $(BUILD)/test_widget \
	    src/util.c src/widget.c src/widget_gen.c \
	    tests/test_widget.c tests/widget_cases_gen.c $(LDFLAGS) $(LDLIBS)
	$(BUILD)/test_widget tests/expected
	$(CC) $(CFLAGS) -Isrc -Itests -o $(BUILD)/test_state \
	    src/util.c src/json.c src/registers.c src/registers_gen.c src/state.c \
	    src/widget.c src/widget_gen.c tests/test_state.c $(LDFLAGS) $(LDLIBS)
	$(BUILD)/test_state
	$(CC) $(CFLAGS) -Isrc -Itests -o $(BUILD)/test_http \
	    $(LIB) tests/test_http.c $(LDFLAGS) $(LDLIBS)
	$(BUILD)/test_http

run-demo: $(BIN)
	THERMIQ_DEMO=1 $(BIN)

clean:
	rm -rf $(BUILD)
