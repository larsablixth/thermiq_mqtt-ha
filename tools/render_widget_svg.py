#!/usr/bin/env python3
"""Render the widget template to the static SVG used in the README.

The widget is a Jinja2 template that Home Assistant renders as HTML: an SVG
schematic plus absolutely positioned HTML badges, a compressor and two pumps,
with flow arrows placed by CSS ``offset-path``. A README image therefore cannot
be produced by lifting the schematic <svg> out of the template - doing that
drops everything CSS positions.

This script instead composes a self-contained SVG:

  * the schematic <svg>, with every ``offset-path`` arrow resolved to a static
    transform sampled at the phase its animation-delay would put it in;
  * the compressor, both circulation pumps and the roof icon, inlined as nested
    <svg> elements at the coordinates their inline styles specify;
  * the value badges, redrawn as <rect> + <text> in light-theme colours.

Usage:
    python3 tools/render_widget_svg.py [--html OUT.html] [--svg OUT.svg]

Requires Jinja2. Regenerate whenever heatpump_widget.j2 changes.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys

from jinja2 import Environment

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(
    REPO, "custom_components", "thermiq_mqtt", "frontend", "heatpump_widget.j2"
)
DEFAULT_SVG = os.path.join(REPO, "docs", "heatpump_widget.svg")

CARD_W, CARD_H = 350, 343
# The template's schematic lives in <div style="position:absolute;left:15px">
# and the card is drawn with a small top inset so it is not flush to the frame.
SCHEM_X, SCHEM_Y = 15.0, 0.0
# Badges, pumps, compressor and the roof icon are nested inside that same
# left:15px container, so their inline left values are relative to it too.
OVERLAY_X = SCHEM_X
SCHEM_H = 300.0
SCHEM_VB_W, SCHEM_VB_H = 400.0, 368.0
SCHEM_W = SCHEM_H * SCHEM_VB_W / SCHEM_VB_H

# The badges inherit the card font size (16px); the unit span is 70% of it.
BADGE_FONT = float(os.environ.get("BADGE_FONT", 16.0))
UNIT_FONT = BADGE_FONT * 0.7
BASELINE = float(os.environ.get("BASELINE", 12.8))
# Light-theme colours from the template's <style> block. The dark-theme media
# query swaps these; the README image commits to the light rendering.
VAL_BG, VAL_FG = "#a0a0a0", "#ffffff"
VALD_BG, VALD_FG = "#ffffff", "#000000"
UNIT_FG = "#222222"

# mdi:home-roof, from Templarian/MaterialDesign (Apache-2.0), viewBox 0 0 24 24
MDI_HOME_ROOF = "M19 16H22L12 7L2 16H5L12 9.69L19 16M7 8.81V7H4V11.5L7 8.81Z"

# Demo values. These mirror the scenario the README image has always shown:
# compressor and both pumps running, no hot-water production, Optimum fitted so
# the pump-speed badges are visible.
STATES = {
    "input_boolean.hpviz_demo": "on",
    "binary_sensor.pool_heating_active": "off",
    "binary_sensor.thermiq_mqtt_vp1_alarm_indication_on": "off",
    "binary_sensor.thermiq_mqtt_vp1_boiler_3kw_on": "off",
    "binary_sensor.thermiq_mqtt_vp1_boiler_6kw_on": "off",
    "binary_sensor.thermiq_mqtt_vp1_brine_pump_on": "on",
    "binary_sensor.thermiq_mqtt_vp1_compressor_on": "on",
    "binary_sensor.thermiq_mqtt_vp1_hotwaterproduction_on": "off",
    "binary_sensor.thermiq_mqtt_vp1_opt_hgw_installed": "on",
    "binary_sensor.thermiq_mqtt_vp1_opt_optimum_installed": "on",
    "binary_sensor.thermiq_mqtt_vp1_supply_pump_on": "on",
    "number.thermiq_mqtt_vp1_integral2_curve_target": "28",
    "sensor.thermiq_mqtt_vp1_boiler_t": "52",
    "sensor.thermiq_mqtt_vp1_brine_in_t": "2",
    "sensor.thermiq_mqtt_vp1_brine_out_t": "-1",
    "sensor.thermiq_mqtt_vp1_brine_pump_speed": "65",
    "sensor.thermiq_mqtt_vp1_communication_status": "Ok",
    "sensor.thermiq_mqtt_vp1_heating_stop_t": "17",
    "sensor.thermiq_mqtt_vp1_hgw_water_t": "45",
    "sensor.thermiq_mqtt_vp1_indoor_t": "21",
    "sensor.thermiq_mqtt_vp1_outdoor_t": "-3",
    "sensor.thermiq_mqtt_vp1_pressurepipe_t": "78",
    "sensor.thermiq_mqtt_vp1_returnline_t": "32",
    "sensor.thermiq_mqtt_vp1_supply_pump_speed": "70",
    "sensor.thermiq_mqtt_vp1_supply_shunt_t": "38",
    "sensor.thermiq_mqtt_vp1_supplyline_t": "38",
    "sensor.thermiq_mqtt_vp1_time_str": "demo data",
    "switch.thermiq_mqtt_vp1_heatpump_evu_block": "off",
}


# --------------------------------------------------------------------------
# Template rendering
# --------------------------------------------------------------------------
def render_html() -> str:
    missing: set[str] = set()

    def states(entity_id):
        if entity_id not in STATES:
            missing.add(entity_id)
            return "unknown"
        return STATES[entity_id]

    def is_state(entity_id, value):
        return states(entity_id) == value

    env = Environment()
    env.globals.update(states=states, is_state=is_state)
    out = env.from_string(open(TEMPLATE).read()).render()
    if missing:
        print("WARNING: unstubbed entities: %s" % sorted(missing), file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# Path geometry - enough of the SVG path grammar for the offset-paths in use
# --------------------------------------------------------------------------
def flatten_path(d: str, steps: int = 64) -> list[tuple[float, float]]:
    """Flatten a path built from M/L/H/V/Q into a polyline."""
    tokens = re.findall(r"[MLHVQ]|-?\d*\.?\d+", d)
    pts: list[tuple[float, float]] = []
    x = y = 0.0
    cmd = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in "MLHVQ":
            cmd = t
            i += 1
            continue
        if cmd == "M":
            x, y = float(tokens[i]), float(tokens[i + 1])
            pts.append((x, y))
            i += 2
            cmd = "L"  # implicit lineto for subsequent pairs
        elif cmd == "L":
            x, y = float(tokens[i]), float(tokens[i + 1])
            pts.append((x, y))
            i += 2
        elif cmd == "H":
            x = float(tokens[i])
            pts.append((x, y))
            i += 1
        elif cmd == "V":
            y = float(tokens[i])
            pts.append((x, y))
            i += 1
        elif cmd == "Q":
            cx, cy = float(tokens[i]), float(tokens[i + 1])
            nx, ny = float(tokens[i + 2]), float(tokens[i + 3])
            x0, y0 = x, y
            for s in range(1, steps + 1):
                u = s / steps
                mu = 1 - u
                pts.append(
                    (
                        mu * mu * x0 + 2 * mu * u * cx + u * u * nx,
                        mu * mu * y0 + 2 * mu * u * cy + u * u * ny,
                    )
                )
            x, y = nx, ny
            i += 4
        else:
            raise ValueError("unsupported path command near %r in %r" % (t, d))
    return pts


def point_at(pts, fraction):
    """Return (x, y, angle_deg) at a fraction of the polyline's length."""
    seglen = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seglen)
    if total == 0:
        x, y = pts[0]
        return x, y, 0.0
    target = max(0.0, min(1.0, fraction)) * total
    run = 0.0
    for i, L in enumerate(seglen):
        if run + L >= target or i == len(seglen) - 1:
            u = (target - run) / L if L else 0.0
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            return (
                x0 + (x1 - x0) * u,
                y0 + (y1 - y0) * u,
                math.degrees(math.atan2(y1 - y0, x1 - x0)),
            )
        run += L
    raise AssertionError("unreachable")


def resolve_arrows(svg_inner: str) -> str:
    """Replace CSS offset-path animation with a static transform.

    ``animation:vpoffs <dur>s linear infinite;animation-delay:-<delay>s`` places
    the arrow at phase (delay / dur) of the path, and offset-rotate:auto turns
    it along the tangent.
    """
    pattern = re.compile(
        r"style=\"offset-path:path\('([^']+)'\);offset-rotate:auto;"
        r"animation:vpoffs ([\d.]+)s linear infinite"
        r"(?:;animation-delay:(-?[\d.]+)s)?\""
    )

    def repl(m):
        d, dur, delay = m.group(1), float(m.group(2)), float(m.group(3) or 0.0)
        phase = ((-delay) / dur) % 1.0 if dur else 0.0
        x, y, ang = point_at(flatten_path(d), phase)
        return 'transform="translate(%.2f %.2f) rotate(%.2f)"' % (x, y, ang)

    out, n = pattern.subn(repl, svg_inner)
    print("  resolved %d flow arrows" % n)
    return out


# --------------------------------------------------------------------------
# HTML -> SVG composition
# --------------------------------------------------------------------------
def apply_static_transforms(scope: str, body: str) -> str:
    """Move ``.cls{transform:translate(x,y)}`` rules onto the elements.

    The template keeps a few resting-state transforms in <style> blocks that are
    dropped here, so they are re-applied as presentation attributes. GitHub
    strips <style> from inline SVG, so nothing may depend on it surviving.
    """
    for cls, x, y in re.findall(
        r"\.([\w-]+)\s*\{\s*transform\s*:\s*translate\("
        r"\s*(-?[\d.]+)(?:px)?\s*,\s*(-?[\d.]+)(?:px)?\s*\)",
        scope,
    ):
        body = body.replace(
            '<g class="%s">' % cls,
            '<g class="%s" transform="translate(%s %s)">' % (cls, x, y),
        )
    return body


def strip_animations(s: str) -> str:
    """Drop CSS animation declarations; keep static presentation intact."""
    s = re.sub(r"animation\s*:[^;\"]*;?", "", s)
    # Whatever CSS is left only drove animation, and GitHub strips <style>
    # from inline SVG anyway, so it must not be relied on.
    s = re.sub(r"<style[^>]*>.*?</style>", "", s, flags=re.S)
    s = re.sub(r"var\(--card-background-color,\s*([^)]*)\)", r"\1", s)
    s = re.sub(r"var\(--primary-text-color,\s*([^)]*)\)", r"\1", s)
    return s


def inner_svg(html: str, start_tag_re: str) -> tuple[str, str]:
    """Return (open_tag, inner_markup) for the first <svg> matching a pattern."""
    m = re.search(start_tag_re, html)
    if not m:
        raise SystemExit("could not locate svg matching %r" % start_tag_re)
    start = m.end()
    depth = 1
    i = start
    while depth:
        nxt = re.search(r"<svg\b|</svg>", html[i:])
        if not nxt:
            raise SystemExit("unbalanced <svg> in rendered html")
        if nxt.group(0) == "<svg":
            depth += 1
        else:
            depth -= 1
        i += nxt.end()
    return m.group(0), html[start : i - len("</svg>")]


def px(style: str, prop: str) -> float:
    m = re.search(prop + r"\s*:\s*(-?[\d.]+)px", style)
    if not m:
        raise SystemExit("no %s in style %r" % (prop, style))
    return float(m.group(1))


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_badges(html: str) -> list[tuple[int, str]]:
    out = []
    for m in re.finditer(
        r'<div class="hpwidget-(val|vald)"[^>]*style="([^"]*)"[^>]*>(.*?)</div>',
        html,
        re.S,
    ):
        kind, style, inner = m.groups()
        if "display:none" in style.replace(" ", ""):
            continue
        left, top = px(style, "left") + OVERLAY_X, px(style, "top")
        w, h = (35.0, 16.0) if kind == "val" else (45.0, 16.0)
        # an inline width overrides the class default (the timestamp badge)
        wm = re.search(r"(?<!-)\bwidth\s*:\s*([\d.]+)px", style)
        if wm:
            w = float(wm.group(1))
        bg, fg = (VAL_BG, VAL_FG) if kind == "val" else (VALD_BG, VALD_FG)
        z = (
            int(re.search(r"z-index\s*:\s*(\d+)", style).group(1))
            if "z-index" in style
            else 0
        )

        text = re.sub(
            r"<span class=\"hpwidget-unit-spand?\">(.*?)</span>", "\x00" + r"\1", inner
        )
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", "\u00a0").replace("&deg;", "°")
        value, _, unit = text.partition("\x00")
        value, unit = value.strip(), unit.strip("\r\n\t ")

        cx = left + w / 2
        out.append(
            (
                z,
                '<rect x="%.1f" y="%.1f" width="%g" height="%g" rx="4" fill="%s"/>'
                '<text x="%.1f" y="%.1f" font-size="%g" fill="%s" text-anchor="middle">'
                '%s<tspan font-size="%g" fill="%s">%s</tspan></text>'
                % (
                    left,
                    top,
                    w,
                    h,
                    bg,
                    cx,
                    top + BASELINE,
                    BADGE_FONT,
                    fg,
                    esc(value),
                    UNIT_FONT,
                    UNIT_FG,
                    esc(unit),
                ),
            )
        )
    return out


def build_nested(html: str, div_id: str, vb: str, size: tuple[float, float]) -> str:
    """Inline a positioned <div><svg>..</svg></div> as a nested <svg>."""
    m = re.search(r'<div id="%s" style="([^"]*)"[^>]*>' % div_id, html)
    if not m:
        print("  note: %s not present in this scenario" % div_id)
        return ""
    style = m.group(1)
    tail = html[m.end() :]
    open_tag, inner = inner_svg(tail, r"<svg\b[^>]*>")
    # The div's own <style> holds resting-state transforms for the nested svg
    # (the compressor's orbiting scroll), so read it before it is discarded.
    scope = tail[: tail.find(open_tag)]
    inner = strip_animations(apply_static_transforms(scope, inner))
    w, h = size
    return '<svg x="%.1f" y="%.1f" width="%g" height="%g" viewBox="%s">%s</svg>' % (
        px(style, "left") + OVERLAY_X,
        px(style, "top"),
        w,
        h,
        vb,
        inner,
    )


def build_roof(html: str) -> str:
    m = re.search(r'<ha-icon icon="mdi:home-roof"[^>]*style="([^"]*)"', html)
    if not m:
        return ""
    style = m.group(1)
    size = 65.0
    sm = re.search(r"--mdc-icon-size:\s*([\d.]+)px", style)
    if sm:
        size = float(sm.group(1))
    return (
        '<svg x="%.1f" y="%.1f" width="%g" height="%g" viewBox="0 0 24 24">'
        '<path d="%s" fill="grey"/></svg>'
        % (px(style, "left") + OVERLAY_X, px(style, "top"), size, size, MDI_HOME_ROOF)
    )


def compose(html: str) -> str:
    _, schem_inner = inner_svg(
        html, r'<svg style="height:300px;overflow:visible;"[^>]*>'
    )
    # Arrows must be resolved before animations are stripped - their position
    # is encoded in the very animation declaration strip_animations removes.
    schem_inner = strip_animations(resolve_arrows(schem_inner))

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="Segoe UI, Roboto, Helvetica, Arial, sans-serif">'
        % (CARD_W, CARD_H, CARD_W, CARD_H),
        "<title>ThermIQ heat pump widget</title>",
        "<desc>Static rendering of the animated ThermIQ Lovelace widget with demo "
        "values: pipe colours track temperature, and the scroll compressor and both "
        "circulation pumps are shown running.</desc>",
        '<rect x="0" y="0" width="%d" height="%d" rx="10" fill="#ffffff" '
        'stroke="#e3e5e8"/>' % (CARD_W, CARD_H),
        '<svg x="%g" y="%g" width="%.2f" height="%g" viewBox="0 0 %g %g">%s</svg>'
        % (SCHEM_X, SCHEM_Y, SCHEM_W, SCHEM_H, SCHEM_VB_W, SCHEM_VB_H, schem_inner),
    ]

    # Everything the template positions absolutely is painted in z-index order,
    # so the roof icon (z-index 4) lands on top of the badges as it does in HA.
    layers: list[tuple[int, str]] = []

    compressor = build_nested(html, "hpwidget-KOMPR", "0 0 40 58", (44, 62))
    if compressor:
        print("  inlined scroll compressor")
        layers.append((2, compressor))
    for pid in ("hpwidget-BRINESPIN", "hpwidget-CIRKPSPIN"):
        nested = build_nested(html, pid, "0 0 26 26", (29, 29))
        if nested:
            print("  inlined pump %s" % pid)
            layers.append((2, nested))
    roof = build_roof(html)
    if roof:
        print("  inlined roof icon")
        layers.append((4, roof))

    badges = build_badges(html)
    print("  drew %d badges" % len(badges))
    layers.extend(badges)

    layers.sort(key=lambda item: item[0])
    parts.extend(markup for _, markup in layers)
    parts.append("</svg>")
    return "\n".join(p for p in parts if p) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", default=DEFAULT_SVG)
    ap.add_argument("--html", help="also write the intermediate HTML render")
    args = ap.parse_args()

    html = render_html()
    if args.html:
        open(args.html, "w").write(
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            "html,body{margin:0;padding:0;background:#fff}"
            "#card{position:relative;width:%dpx;height:%dpx;background:#fff;"
            "border-radius:10px;overflow:hidden;"
            'font-family:"Segoe UI",Roboto,Helvetica,Arial,sans-serif}'
            '</style></head><body><div id="card">%s</div></body></html>'
            % (CARD_W, CARD_H, html)
        )
        print("wrote %s" % args.html)

    svg = compose(html)
    open(args.svg, "w").write(svg)
    print("wrote %s (%d bytes)" % (args.svg, len(svg)))


if __name__ == "__main__":
    main()
