# Pre-heating the floor in cheap hours (beta)

A concrete floor slab can store heat. Warm it a little while electricity is
cheap, let it give the heat back while electricity is expensive, and you buy
less power at the expensive price. The integration ships a blueprint (a
ready-made automation) that does this by nudging the heat pump's **indoor
target** up and down.

**It is only worth doing when the price gap is large enough.** This page
explains why, and how to set the one number that decides it.

## Turning it on

1. Update the integration to 3.5.12-beta.2 or later (HACS: *Show beta
   versions*) and restart Home Assistant.
2. **Settings → Automations & scenes → Blueprints**: *ThermIQ – pre-heat the
   floor in cheap hours (beta)* is already there. Nothing runs until you
   create an automation from it.
3. *Create automation*: pick your price source, set your **normal indoor
   target**, and enter your fees (below). Save.

To stop: disable or delete that automation, then set the indoor target on the
heat pump back to what it was.

## How much a slab holds

Concrete stores about 0.88 kJ per kg per °C. A slab of 8 × 13 m, 15 cm thick
is 15.6 m³, about 36 tonnes, and holds **about 8.8 kWh of heat per °C**. Work
out your own: *length × width × thickness (m) × 0.56* = kWh per °C.

A heated room below the slab (a basement) helps: heat that leaks downward
warms a space that needs heating anyway, so it is not lost.

## Why a small price gap is not enough

Loading heat into the slab costs you twice:

1. **The heat pump works less efficiently at a higher supply temperature.**
   Each extra °C of supply water costs roughly 2–3% in efficiency (COP).
2. **A warmer house leaks more heat** until the stored heat is used.

So shifting only pays when

> what you pay in the expensive hours ÷ what you pay in the cheap hours
> **>** break-even ratio (1.3 to start with)

## Compare what you actually pay

What you pay per kWh is the price **plus** energy tax, grid fee and, for spot
prices, VAT and markup. A fee that is the same every hour makes the gap look
smaller; a grid fee that is higher in the day makes it larger. Enter both:

| Setting | What to enter | Vattenfall time tariff |
|---|---|---|
| Other costs per kWh | Energy tax + off-peak grid fee + markup | 0.45 + 0.305 = **0.755** |
| Extra grid fee in peak hours | Peak minus off-peak grid fee | **0.46** |
| Peak hours / months | When the peak fee applies | 06–22, weekdays, Nov–Mar |

Tibber's price already includes VAT and Tibber's markup, but not the grid fee
or energy tax. Nordpool and Entso-e sensors may or may not include VAT,
depending on how they are set up. Use the same unit as the price (SEK/kWh,
not öre).

With Vattenfall's time tariff the day/night fee alone clears 1.3 on most
winter weekdays: night is cheap even when the spot price is flat.

## What it is worth

A simulation of a ground-source heat pump house (heat loss 0.16 kW/°C) on
SE3 prices 2023–2026 with Vattenfall's time tariff, with each day planned
from its known prices:

| Slab warmed by | Heat stored (8 × 13 m × 15 cm) | Saved per winter |
|---|---|---|
| 1 °C | 8.8 kWh | 300–480 kr |
| 1.5 °C | 13 kWh | 370–610 kr |
| 2 °C | 18 kWh | 400–660 kr |

Beyond about 1.5 °C the extra saving is small and the temperature swing is
felt. The limit is the lost efficiency, not how much the slab can hold. The
[hot water function](hotwater_cheap_hours.md) is worth more (about 750–950 kr
per winter) and costs no efficiency, so turn that on first.

## What the blueprint does

Every 15 minutes, using all published prices (tomorrow's are out around
13:00):

| Situation | Action |
|---|---|
| Within the look-ahead, an hour costing at least *break-even* × now is coming | **Pre-heat:** target = normal + raise |
| Now costs at least *break-even* × the cheapest hour of the look-ahead just passed | **Give back:** target = normal − drop |
| What you pay now is zero or negative | **Pre-heat** |
| Anything else, or no prices | **Normal** target |

It writes to the heat pump only when the target actually changes.

## Settings to start with

| Setting | Start at | Notes |
|---|---|---|
| Break-even ratio | 1.3 | Raise it if comfort suffers for little gain |
| Look-ahead | 8 h | About how long the slab holds its heat |
| Pre-heat raise | 1 °C | Most people notice swings above ±1 °C |
| Expensive-hour drop | 1 °C | 0 = never go below normal |

## Things to know

- **While enabled, the blueprint owns the indoor target.** Change the
  *normal indoor target* in the automation, not on the heat pump, or your
  change is overwritten within 15 minutes.
- **It works alongside the EVU automation** in the README. EVU blocks
  heating in expensive hours; this one fills the slab beforehand. Check that
  the two do not fight, for example EVU blocking the very hours this one
  picked to pre-heat.
- **A shunt group** gives the floor its own curve. Raising the heat pump's
  target may then warm radiators and a buffer tank rather than the slab.
- **In deep cold** an on/off compressor may already run nearly all the time,
  leaving no spare hours to store heat in. The blueprint then raises the
  target without effect; nothing breaks.
- Public holidays are not recognised; they count as weekdays.
- Beta: nobody has run it on real hardware yet. Watch the indoor
  temperature for the first days.
