# Hot water in cheap hours (beta)

After a few showers the heat pump normally reheats the tank straight away,
often in the morning when electricity is most expensive. Most of the time
that can wait: 35 °C is enough for a quick shower, and a few hours later
power is cheaper.

The integration ships a blueprint (a ready-made automation) that does that
with one heat pump setting, the hot water **start temperature**:

| When | Start temperature | Effect |
|---|---|---|
| Most of the time | **35 °C** (your lowest accepted) | The pump reheats only if the tank really falls that low |
| The cheapest hours of the next 24 h | stop temperature − 3 °C | The tank is topped up while power is cheap |

The stop temperature (how hot the tank gets) is never changed.

## Turning it on

1. Update the integration to 3.5.12-beta.2 or later (HACS: *Show beta
   versions*) and restart Home Assistant.
2. **Settings → Automations & scenes → Blueprints**: *ThermIQ – hot water in
   cheap hours (beta)* is already there. Nothing runs until you create an
   automation from it.
3. Check the legionella settings on the heat pump (below), including that
   the legionella run is actually switched on.
4. *Create automation*, pick your price source, enter your peak grid fee if
   you have one, save.

To stop: disable or delete that automation, then set the hot water start
temperature on the heat pump back to what it was.

The blueprint file is kept in `blueprints/automation/thermiq_mqtt/` and is
replaced when an update brings a new version. To change it yourself, copy it
under another name first.

## Why this saves money with no downside in efficiency

Unlike pre-heating the floor, moving hot water production costs no
efficiency: the tank is already an insulated store, and heating it from a
lower temperature is, if anything, slightly more efficient. A tank that sits
cooler for longer also loses less heat. On SE3 prices 2023–2026 with
Vattenfall's time tariff, moving 8 kWh/day of hot water electricity to the
cheapest hours was worth roughly **800–950 kr per winter**.

## Legionella: keep the heat pump's own protection on

A tank allowed down to 35 °C spends long periods at 25–45 °C, the range in
which legionella bacteria grow. The heat pump has its own periodic
high-temperature run; **keep it on**:

- *Legionella peak heating enable* (`legionella_run_on`): **1**. This is
  the on/off switch. With it at 0 the two settings below are kept but never
  run, so check it first: on one installation the interval and temperature
  looked right while the switch was off.
- *Legionella interval* (`legionella_interval_d`): at most 7 days
- *Legionella stop temperature* (`legionella_stop_t`): at least 60 °C

The run often needs the heat pump's **electric heater** to reach 60 °C. If
you have switched the heater off, watch the tank temperature (`boiler_t`)
during a run to see whether it gets there.

If the legionella run is off and you don't want to turn it on, don't let the
tank sit low: set the *lowest accepted temperature* to at least 45 °C, or
don't use this blueprint. Some heat pumps keep the start temperature at no
more than 40 °C (see *Things to know*); on those, 45 °C is not possible
through the start temperature, and this blueprint has nothing left to do.

This blueprint does not touch any of these settings.

## Settings

| Setting | Default | Notes |
|---|---|---|
| Price source | Tibber | Or Nordpool (HACS) / Entso-e with their price sensor |
| Lowest accepted temperature | 35 °C | The tank never goes below this |
| Cheap hours per day | 3 h | Enough to heat the tank from the lowest temperature |
| Top-up margin | 3 °C | In cheap hours, start = stop − this |
| Highest start temperature the heat pump accepts | 60 °C (no limit) | Set it if your pump stores a lower start temperature than it is sent (see below) |
| Extra grid fee in peak hours | 0 | Vattenfall time tariff: 0.46 SEK/kWh, weekdays 06–22, Nov–Mar |

With a time-of-day grid fee, enter it: otherwise a cheap spot hour at 10:00
looks as good as one at 02:00, though the grid fee makes it 0.46 SEK/kWh
dearer. Public holidays are not recognised; they count as weekdays.

## How the cheap hours are picked

Every 15 minutes the blueprint takes the next 24 hours of known prices (plus
the peak fee where it applies) and asks whether the current hour is among the
*N* cheapest. Before tomorrow's prices are published (around 13:00) it ranks
against the whole of today instead, so a late-evening hour is never picked
just because it is the last one known. With 15-minute prices, *N* hours means
4 × *N* quarter-hours.

If no prices are available (Tibber not reachable, sensor down) the start
temperature stays at the lowest accepted temperature: the tank is still kept
above it, it just is not topped up early.

## Things to know

- **While enabled, the blueprint owns the start temperature.** Change the
  lowest accepted temperature in the automation, not on the heat pump. If you
  turn the automation off, set the start temperature back by hand.
- Every change is a write to the heat pump; it only writes when the value
  actually changes (a few times a day).
- **Some heat pumps limit the start temperature.** One stores 40 °C when it
  is sent 56 °C. You can see it in the start temperature's history: the value
  jumps back a few seconds after the automation writes it. Enter that value
  as *highest start temperature the heat pump accepts*. In cheap hours the
  tank is then topped up only if it has fallen below that value, so less of
  the reheating moves to cheap hours, but the automation stops rewriting the
  value every 15 minutes.
- Beta: nobody has run it on real hardware yet. Watch the tank
  temperature (`boiler_t`) for the first days.
