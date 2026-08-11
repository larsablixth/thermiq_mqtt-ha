# Animated heat-pump widget

A live SVG schematic of the heat pump as a Lovelace card — a drop-in
replacement for the PNG-based visualization in `ThermIQ_Card.yaml`.

The whole machine is drawn as one SVG: cabinet, brine loop, evaporator,
scroll compressor, condenser, hot-water tank, 3-way valve, radiator
circuit and (optionally) a pool / second heating circuit.

Everything on screen tells the truth about the machine:

- **Color is temperature, everywhere.** Every pipe, badge and vessel is
  colored from the live register value on shared scales (water, hot gas
  and brine each have their own palette).
- **Flows render only when physically flowing.** Arrows and waves appear
  when the corresponding pump/compressor/valve state says the medium
  moves; an idle machine is a still picture.
- **Mechanics animate true to their kinematics.** The scroll compressor's
  orbiting spiral translates in a circle without rotating, exactly like
  the real thing; circulation pump impellers spin only while running.
- Conditional drawing: HGW branch appears only when
  `opt_hgw_installed` is on, alarm state turns the background red, the
  auxiliary boiler steps and EVU block have their own indicators.

## Why a custom card?

Template cards that re-render by replacing `innerHTML` restart every CSS
animation each time any referenced entity changes — with ~25 live
entities (including the pump's own clock) that means visible stuttering
every few seconds. `thermiq-widget-card` subscribes to HA's
`render_template` websocket API and applies each update by **DOM
morphing**: only attributes and text that actually changed are touched,
so animations run uninterrupted. It has no dependencies.

## Install

**Nothing to install.** The card ships inside the integration, which serves it
at `/thermiq_mqtt_frontend/` and registers it with the frontend during setup —
no files to copy into `www/`, and no dashboard resource to add by hand.

1. Restart Home Assistant after installing the integration, if you haven't
   already. The card is registered during setup, so it doesn't exist until the
   integration has loaded once.

2. Hard-refresh the browser (Ctrl/Cmd+Shift+R) the first time, so it picks up
   the newly registered module instead of a cached page.

3. Add the card to a dashboard — standalone or as a row inside an
   `entities` card:

   ```yaml
   type: custom:thermiq-widget-card
   ```

   If your integration entry ID isn't the default `vp1`:

   ```yaml
   type: custom:thermiq-widget-card
   entity_prefix: thermiq_mqtt_myid
   ```

Editing the visualization means editing
`custom_components/thermiq_mqtt/frontend/heatpump_widget.j2` and reloading the
page — the card fetches the template fresh on each page load, and it is served
with caching disabled so edits show up immediately.

### If it doesn't come up

1. **Has the integration loaded?** The card is registered during setup, so it
   does not exist until Home Assistant has started with the integration
   installed. Check the log for `Could not register the ThermIQ dashboard card`.
2. **Is the card served?** Open
   `http://<your-ha>:8123/thermiq_mqtt_frontend/thermiq-widget-card.js` in a
   browser. You should get JavaScript; a 404 means the static path was not
   registered, and the log line above will say why.
3. **Custom element doesn't exist: thermiq-widget-card.** The browser did not
   load the module — hard-refresh, and check the console for a failed request.
4. **Card loads but shows an error box.** The card prints
   `cannot load /thermiq_mqtt_frontend/heatpump_widget.j2` inside itself when
   the template fetch fails, which points back at step 2.
5. **Card draws but values are blank.** The template reads
   `sensor.thermiq_mqtt_vp1_*`; set `entity_prefix` if your entry ID isn't
   `vp1`.

**Upgrading from a manual install.** If you previously copied the files into
`www/thermiq/` and registered `/local/thermiq/thermiq-widget-card.js` as a
resource, remove that resource and delete the copies, or the card is loaded
twice from two different versions.

## Optional extras

**Pool / second heating circuit.** This needs an optional expansion card
fitted in the heat pump — most pumps don't have one. It provides the
second heating circuit, which the pump calls **Curve 2** and which this
integration exposes as the `integral2_*` registers (curve slope, min,
max, target, actual). Note this is a separate thing from the pump's
`shunt1_*` / `shunt2_*` / `shunt_cooling_*` signals, which exist on
pumps without the expansion card.

If you don't have the card, skip this section: the widget never draws
the pool branch. If you do use the second circuit (e.g. for a pool),
define a template binary sensor named
`binary_sensor.pool_heating_active`; while it is `on` the widget draws
the secondary heat-exchanger branch with flow animation. Example
(strict "actually heating right now" semantics):

```yaml
template:
  - binary_sensor:
      - name: pool_heating_active
        state: >
          {{ states('number.thermiq_mqtt_vp1_integral2_curve_target')|float(0) > 10
             and is_state('binary_sensor.thermiq_mqtt_vp1_compressor_on','on')
             and is_state('binary_sensor.thermiq_mqtt_vp1_supply_pump_on','on')
             and is_state('binary_sensor.thermiq_mqtt_vp1_hotwaterproduction_on','off') }}
```

**Demo mode.** Create `input_boolean.hpviz_demo` and toggle it to stage a
hot-water charging cycle visually (forced flows, staged 63 °C tank
color) without touching the pump — handy for testing the card or showing
it off. Without the helper, demo mode is simply off.

## Compatibility note

The widget reads the integration's sensors and binary sensors, which
exist on current master. Two references assume the native
`number`/`switch` platforms proposed in PR #76 (`number.…_integral2_curve_target`
for the pool-target color and `switch.…_heatpump_evu_block` for the EVU
badge); on current master those two gracefully fall back to defaults and
everything else works unchanged.
