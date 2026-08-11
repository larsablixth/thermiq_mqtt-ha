# Animated heat-pump widget

A live SVG schematic of the heat pump as a Lovelace card. Since v3.5.0 this
is the visualization used by `ThermIQ_Card.yaml`, replacing the PNG-based
picture that came before it.

The whole machine is drawn as one SVG: cabinet, brine loop, evaporator,
scroll compressor, condenser, hot-water tank, 3-way valve, radiator
circuit and (optionally) a pool/secondary shunt circuit.

![The widget rendered with demo values](../docs/heatpump_widget.svg)

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

   > **If you did not already have a `www` folder, restart Home Assistant
   > now.** Home Assistant maps `/config/www/` to the `/local/` URL only when
   > it starts, so on a fresh install — the HA OS image included — files put
   > into a newly created `www` folder return 404 until you restart. This is
   > the most common reason the card comes up blank.

2. Register the card: *Settings → Dashboards → ⋮ → Resources → Add*,
   URL `/local/thermiq/thermiq-widget-card.js?v=1.1.0`, type
   *JavaScript module*. (Bump the `?v=` whenever you update the JS.)

   If your dashboards are in YAML mode the Resources page is hidden, and the
   resource goes in `configuration.yaml` instead:

   ```yaml
   lovelace:
     mode: yaml
     resources:
       - url: /local/thermiq/thermiq-widget-card.js?v=1.1.0
         type: module
   ```

3. Reload the browser with a hard refresh (Ctrl/Cmd+Shift+R). A newly
   registered resource is often not picked up until the cached page is
   dropped.

4. Add the card to a dashboard — standalone or as a row inside an
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

### If it doesn't come up

Work through these in order — the first two catch almost everything.

1. **Is the template served?** Open
   `http://<your-ha>:8123/local/thermiq/heatpump_widget.j2` in a browser. You
   should get a wall of SVG markup. A 404 means the files are not where Home
   Assistant is looking: check they are in `/config/www/thermiq/` (not
   `/config/thermiq/`, and not `www/community/`), and that you restarted after
   creating the `www` folder.
2. **Is the card resource loaded?** If the card row shows *"Custom element
   doesn't exist: thermiq-widget-card"*, the JS was not loaded — re-check the
   resource URL and hard-refresh. The browser console will show a 404 for the
   `.js` if the path is wrong.
3. **Card loads but shows an error box.** The card prints
   `cannot load /local/thermiq/heatpump_widget.j2` inside the card when the
   template fetch fails, which points back at step 1.
4. **Card draws but values are blank or the pump looks idle.** The template
   reads `sensor.thermiq_mqtt_vp1_*`. If your integration entry ID isn't
   `vp1`, set `entity_prefix` on the card as shown above; the widget cannot
   guess it.

`/config` is reachable over the Samba, SSH or File editor add-ons on the HA OS
image.

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

The widget reads the integration's sensors and binary sensors, plus
`number.…_integral2_curve_target` for the pool-target colour and
`switch.…_heatpump_evu_block` for the EVU badge. All of these exist on this
fork. Against **upstream** ThermIQ, which still uses the `input_*` platforms,
those two references fall back to defaults and everything else works
unchanged.
