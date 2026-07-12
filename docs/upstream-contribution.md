# Upstream contribution plan (staged, not yet sent)

Prepared branches on `larsablixth/thermiq_mqtt-ha`, each based on
`ThermIQ/thermiq_mqtt-ha` master so they can be merged independently.

## Umbrella issue draft (open this first on ThermIQ/thermiq_mqtt-ha)

---

**Title:** Offering a series of bug fixes from a maintained fork

Hi! I run this integration daily against a Thermia Villa Classic and have been
maintaining a fork ([larsablixth/thermiq_mqtt-ha](https://github.com/larsablixth/thermiq_mqtt-ha))
with a number of fixes, all verified on a live pump. I'd like to contribute
back whatever you find sensible. Everything below is prepared as a separate,
independent PR against your master — tell me which ones you want and I'll open
them (or all of them, and you cherry-pick).

**Bug fixes (no behavior change for working setups):**

- [ ] **MQTT debug mode writes to the real pump.** The cmd/set topics are
  built before the `dbg_` prefix is applied, so enabling "Debug: write to
  dbg_ topics" still writes to the real heatpump. Also: a malformed key in an
  incoming MQTT message aborts processing of the whole message, and registers
  are seeded with `-1` at startup which briefly lights up every alarm
  bitmask. *(branch: `upstream-pr1-safety`)*
- [ ] **Runtime-added config entries never subscribe to MQTT.** Adding the
  integration via the UI after HA has started waits for
  `EVENT_HOMEASSISTANT_STARTED`, which has already fired and never will
  again. Also: unloading an entry leaks the MQTT subscription and the input_*
  helper entities, and the DB migration calls a loop-only recorder API from
  the executor (and doesn't await the executor job). *(branch:
  `upstream-pr2-lifecycle`)*
- [ ] **Config flow swallows all errors with bare `except:`,** including the
  duplicate-ID abort, so adding a duplicate shows a misleading form error
  instead of "already configured". Also adds validation that the ID is
  slug-safe (it's embedded in entity_ids). *(branch:
  `upstream-pr3-config-flow`)*
- [ ] **Platform setup race:** `async_forward_entry_setups` is fired as a
  task instead of awaited, so the entry is marked ready before the platforms
  are. *(branch: `upstream-pr4-setup-await`)*

**Enhancements:**

- [ ] **Translations:** the Swedish config-flow translation file contained
  English; adds real `se.json` plus `de`/`fi`/`no`. *(branch:
  `upstream-pr5-translations`)*

**Bigger topics — for discussion before any code:**

- [ ] **Replace the input_number/input_select/input_boolean injection with
  native `number`/`select`/`switch` entities.** The current approach hijacks
  `hass.data[CONF_ENTITY_PLATFORM]`, an internal API that can break on any HA
  release. Native platforms are the supported way, but this is a breaking
  change (entity domains change), so it needs a migration story and a major
  version. I run this in my fork if you want to look.
- [ ] **Availability:** entities report `unavailable` until the first MQTT
  message and when the pump stops publishing (watchdog), instead of showing
  stale values forever.
- [ ] **Write validation:** validate outgoing register writes against the
  register table min/max before publishing (validating *before* the 16-bit
  two's-complement conversion so negative values still work).
- [ ] **Tests + CI:** a pytest suite (register table integrity, MQTT message
  parsing) and a CI test job.

No hurry with any of this — happy to adjust scope, style, or split things
differently.

---

## Send commands (run when the user says go)

```bash
# 1. The umbrella issue
gh issue create -R ThermIQ/thermiq_mqtt-ha --title "Offering a series of bug fixes from a maintained fork" --body-file <this file's issue section>

# 2. PRs (after maintainer responds, or immediately if he asks for them)
gh pr create -R ThermIQ/thermiq_mqtt-ha --head larsablixth:upstream-pr1-safety \
  --title "Fix MQTT debug mode writing to the real pump; harden message processing" ...
# ... same for pr2..pr5
```

Branch contents:
- `upstream-pr1-safety` — 1 commit: dbg topics after prefix, per-key error
  handling in message_received, seed registers None not -1
- `upstream-pr2-lifecycle` — 3 commits: input_* unload tracking,
  sensor/binary_sensor device_class/is_on/listener fixes, runtime MQTT
  subscribe + clean unload + migration executor/await fixes
- `upstream-pr3-config-flow` — 2 commits: specific exceptions (AbortFlow
  propagates), id_name slug validation
- `upstream-pr4-setup-await` — 1 commit: await async_forward_entry_setups
- `upstream-pr5-translations` — 1 commit: real se.json + de/fi/no

Kept out on purpose: pool label renames, manifest URLs pointing at the fork,
version bumps, device-grouping removal / entity naming (fork preference),
the breaking platform rewrite (issue discussion first).
