# Protecting a shallow energy well (beta)

If the borehole is too shallow for the house, a long cold spell pulls the
brine colder and colder. The heat pump gets less efficient, and in the worst
case it stops on its own brine alarm, leaving the house without heat until
someone resets it.

The integration ships a blueprint (a ready-made automation) that eases off
**before** the alarm does: it lowers the heating curve's maximum supply
temperature while the brine is close to the alarm limit, and restores it
when the brine has recovered.

## How it works

| Brine (while the brine pump runs) | Heating curve maximum |
|---|---|
| Within *act this far above the alarm* (2 °C) of the alarm limit | **Eased** (45 °C) |
| Recovering, but not yet *recovery* (3 °C) above that point | Stays eased |
| Recovered | **Normal** (55 °C) |
| Brine pump stopped, or no reading | Unchanged |

A cooler supply means the heat pump delivers a little less heat and draws a
little less from the well, for as long as it takes the rock and groundwater
around the borehole to catch up. The house cools slightly meanwhile; a
concrete floor or other thermal mass covers that for a while.

It only makes a difference on cold days, when the heating curve asks for
more than the eased maximum. Those are the days the brine runs low.

### Which brine reading

It watches **brine out** (leaving the heat pump, the colder side) by default,
and compares it with the heat pump's own alarm limit (*Brine temp., min
limit*). Brine out is never warmer than brine in, so the blueprint acts
before the alarm whichever side the alarm watches.

The reading is only used while the brine pump runs: when the pump stands
still, the pipe drifts towards room temperature and says nothing about the
well.

The heat pump treats an alarm limit of −15 °C as "off". The blueprint then
uses its own *brine limit when the heat pump's alarm is off* (−5 °C).

## Turning it on

1. Update the integration and restart Home Assistant.
2. **Settings → Automations & scenes → Blueprints**: *ThermIQ – protect a
   shallow energy well (beta)* is already there. Nothing runs until you
   create an automation from it.
3. *Create automation*: set the **normal curve maximum** to what your heat
   pump uses today, and an **eased maximum** 5–10 °C below it. Save.

To stop: disable or delete the automation, then check that the heating
curve maximum on the heat pump is back at your normal value.

## Settings to start with

| Setting | Start at | Notes |
|---|---|---|
| Act this far above the alarm | 2 °C | Larger = eases earlier |
| Recovery before restoring | 3 °C | Larger = stays eased longer, switches less often |
| Normal curve maximum | your current value | Written whenever the brine is fine |
| Eased curve maximum | 5–10 °C below normal | Must be below normal to do anything |
| Brine limit when the alarm is off | −5 °C | Only used if the alarm limit is −15 °C |

## Things to know

- **While enabled, the blueprint owns the curve maximum.** Change the
  *normal curve maximum* in the automation, not on the heat pump, or your
  change is overwritten within 5 minutes.
- **It works alongside the floor pre-heat blueprint.** That one changes the
  indoor target; this one changes the curve maximum, so they do not
  overwrite each other. While the well is protected, a pre-heat boost can
  be cut short by the eased maximum: the well comes first.
- It only changes heating curve 1 (`integral1_curve_max`). How that plays
  with a shunt group on curve 2 has not been checked on real hardware.
- If the house has another heat source that could take over, blocking the
  heat pump (EVU) while the brine is low would protect the well better.
  This blueprint does not do that.
- Beta: nobody has run it on real hardware yet. Watch the brine and indoor
  temperatures on the first cold days.
