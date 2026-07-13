# Animated heat-pump widget

A live SVG schematic of the heat pump as a Lovelace card — a drop-in
replacement for the PNG-based visualization in `ThermIQ_Card.yaml`.

The whole machine is drawn as one SVG: cabinet, brine loop, evaporator,
scroll compressor, condenser, hot-water tank, 3-way valve, radiator
circuit and (optionally) a pool/secondary shunt circuit.

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

1. Copy `heatpump_widget.j2` and `thermiq-widget-card.js` to
   `/config/www/thermiq/` on your Home Assistant machine.
2. Register the card: *Settings → Dashboards → ⋮ → Resources → Add*,
   URL `/local/thermiq/thermiq-widget-card.js?v=1.1.0`, type
   *JavaScript module*. (Bump the `?v=` whenever you update the JS.)
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

Editing the visualization is just editing `heatpump_widget.j2` and
reloading the page — the card fetches the template fresh on each page
load.

## Optional extras

**Pool / secondary shunt circuit.** This requires the optional shunt
group extension card in the heat pump (a Thermia accessory — most pumps
don't have it; it drives the Curve 2 / `integral2_*` registers). If you
don't have the card, skip this section: the widget never draws the pool
branch. If you do use the shunt circuit (e.g. for a pool), define a
template binary sensor named `binary_sensor.pool_heating_active`; while
it is `on` the widget draws the secondary heat-exchanger branch with
flow animation. Example (strict "actually heating right now"
semantics):

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
