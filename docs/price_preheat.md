# Pre-heating the floor in cheap hours (experimental)

A concrete floor can store heat. Warm it a little while electricity is cheap,
let it give the heat back while electricity is expensive, and you buy less
power at the expensive price. The blueprint
[`blueprints/automation/thermiq/price_preheat.yaml`](../blueprints/automation/thermiq/price_preheat.yaml)
does this by nudging the heat pump's indoor target up and down.

**It is only worth doing when the price gap is large enough.** This page
explains why, and how to pick the one number that decides it.

## Why a small price gap is not enough

Loading heat into the floor costs you twice:

1. **The heat pump works less efficiently at a higher supply temperature.**
   Each extra °C of supply water costs roughly 2–3% in efficiency (COP).
   Raising the supply from 30 °C to 34 °C makes every kWh of heat about 10%
   more expensive in electricity.
2. **A warmer house leaks more heat.** Heat loss grows with the gap between
   indoors and outdoors, and a slab on the ground also loses some downward.
   Count on 5–15% of the stored heat being lost before you get it back.

So shifting only pays when

> expensive price ÷ cheap price **>** break-even ratio ≈ 1.2–1.35

## Compare total prices, not spot prices

What you pay per kWh is the spot price **plus** fixed parts that are the same
every hour: energy tax, grid transfer fee, supplier markup, VAT. They make
the gap look smaller. In Sweden they are often around 1 SEK/kWh:

| Spot cheap → expensive | Total cheap → expensive | Ratio | Worth it at 1.3? |
|---|---|---|---|
| 0.30 → 0.60 SEK | 1.30 → 1.60 | 1.23 | no |
| 0.30 → 0.90 SEK | 1.30 → 1.90 | 1.46 | yes |
| 0.50 → 2.50 SEK | 1.50 → 3.50 | 2.33 | clearly |

Rule of thumb: with about 1 SEK/kWh in fixed costs, the spot price must swing
about 0.4 SEK or more. Many ordinary days do not qualify; winter price spikes
do. Enter your fixed costs in the blueprint's **Fixed cost per kWh** so it
compares the right numbers.

## What the blueprint does

Every 15 minutes, using your Nordpool (HACS) or Entso-e price sensor:

| Situation | Action |
|---|---|
| Within the look-ahead window, a price at least *break-even* × today's is coming | **Pre-heat:** target = normal + raise |
| Now costs at least *break-even* × the cheapest price in the window just passed | **Use stored heat:** target = normal − drop |
| Price now is zero or negative (after fees) | **Pre-heat** |
| Anything else, or no price data | **Normal** target |

It writes to the heat pump only when the target actually changes.

## Settings to start with

| Setting | Start at | Notes |
|---|---|---|
| Break-even ratio | 1.3 | Raise it if comfort suffers for little gain |
| Look-ahead | 6 h | About how long your floor holds heat |
| Pre-heat raise | 1 °C | Most people notice swings above ±1 °C |
| Expensive-hour drop | 1 °C | 0 = never go below normal |

## If you have a home battery

A battery that already runs the house from cheap night power does the same
job, at about the same loss. In a simulation of one such house (64 kWh
battery, 2023–2026 SE3 prices, time tariff) the floor added **0–50 kr per
winter** on top of the battery, against 400–650 kr without one. With a
battery this blueprint is not worth the comfort swing.

## Things to know

- **While enabled, the blueprint owns the indoor target.** Change the
  *normal indoor target* in the automation, not on the heat pump, or your
  change will be overwritten within 15 minutes. If you turn the automation
  off, set the target back by hand.
- **It works alongside the EVU automation** in the README. EVU blocks heating
  in expensive hours; this one fills the floor beforehand. Check that the two
  do not fight, for example EVU blocking the very hours this one picked to
  pre-heat.
- **A shunt group** gives the floor its own curve. Raising the heat pump's
  target may then warm radiators and a buffer tank rather than the slab.
- **On/off compressors in deep cold** may already run nearly all the time,
  which leaves no spare hours to move heat into.
- **Peak-power tariffs (effekttariff):** extra running in one hour can raise
  your peak charge and eat the saving.

This is experimental. Nobody has run it on real hardware yet, so watch the
indoor temperature for the first few days.
