"""Chart geometry. Pure functions — no I/O, no rendering.

Layout maths is exactly the kind of thing that goes wrong silently: a bar
that overflows its box, a zero line in the wrong place, a scale that lies
about magnitude. Computing it here means it can be tested; the templates
only turn these numbers into SVG elements.

Colour roles (validated against the app surface #171a21 with the dataviz
validator — all six checks pass):
  diverging pos  #3987e5  blue   — favours team A
  diverging neg  #e66767  red    — favours team B
  midpoint       #383835  gray   — zero, reads as "nothing"
Diverging rather than categorical because the reader's job here is polarity:
which side does this map favour, and by how much.
"""
from __future__ import annotations

POS = "#3987e5"
NEG = "#e66767"
ZERO = "#383835"

# Heatmap cells carry their own value as text, and text wears ink tokens
# rather than the series colour. Full-opacity fills fail contrast against
# that ink (2.99 blue / 2.66 red, measured against the panel surface), so
# the ramp is capped where both poles still clear 4.5:1.
CELL_ALPHA_MAX = 0.62

BAR_H = 18
BAR_GAP = 8
ROUND = 4          # rounded data-end, per the mark spec
MIN_LEN = 2        # so a near-zero value still shows as a mark


def _nice_max(value: float, floor: float = 1.0) -> float:
    """A rounded axis maximum that never crops the data."""
    v = max(abs(value), floor)
    for step in (1, 2, 2.5, 5, 10, 15, 20, 25, 30):
        if v <= step:
            return float(step)
    return float(int(v / 10 + 1) * 10)


def diverging_bars(items, width: int = 460, label_w: int = 96,
                   value_w: int = 74) -> dict:
    """Horizontal bars diverging from a centred zero.

    `items`: [{"label", "value", "n", "detail"}]. Positive values extend
    right in the positive hue, negative left in the negative hue.

    Returns geometry only. A caller that wants a legend or a title adds it;
    a single-series chart needs no legend box because the title names it.
    """
    items = list(items)
    if not items:
        return {"bars": [], "width": width, "height": 0, "mid": 0,
                "max": 0, "ticks": []}

    plot_w = width - label_w - value_w
    mid = label_w + plot_w / 2
    half = plot_w / 2 - 6
    vmax = _nice_max(max(abs(i["value"]) for i in items))

    bars = []
    y = 0
    for it in items:
        v = it["value"]
        length = max(MIN_LEN, abs(v) / vmax * half)
        bars.append({
            "label": it["label"],
            "value": v,
            "n": it.get("n"),
            "detail": it.get("detail", ""),
            "x": mid if v >= 0 else mid - length,
            "y": y,
            "w": length,
            "h": BAR_H,
            "fill": POS if v >= 0 else NEG,
            "text_x": mid + length + 8 if v >= 0 else mid - length - 8,
            "anchor": "start" if v >= 0 else "end",
            "label_y": y + BAR_H * 0.72,
        })
        y += BAR_H + BAR_GAP

    ticks = [{"v": -vmax, "x": mid - half}, {"v": 0.0, "x": mid},
             {"v": vmax, "x": mid + half}]
    return {"bars": bars, "width": width, "height": max(0, y - BAR_GAP),
            "mid": mid, "max": vmax, "ticks": ticks, "label_w": label_w}


def strip(values, width: int = 460, height: int = 44, radius: int = 5,
          pad: int = 10) -> dict:
    """A one-row dot strip: distribution of signed values around zero.

    Shows consistency, which an average hides. Two teams with the same mean
    round differential are different propositions if one is +2 every map and
    the other alternates +12 and −8.
    """
    values = [v for v in values if v is not None]
    if not values:
        return {"dots": [], "width": width, "height": height, "mid": 0,
                "max": 0, "n": 0}
    vmax = _nice_max(max(abs(v) for v in values), floor=5.0)
    usable = width - pad * 2
    mid = pad + usable / 2
    cy = height / 2
    dots = []
    for v in values:
        dots.append({
            "cx": mid + (v / vmax) * (usable / 2 - radius),
            "cy": cy,
            "r": radius,
            "fill": POS if v >= 0 else NEG,
            "value": v,
        })
    return {"dots": dots, "width": width, "height": height, "mid": mid,
            "max": vmax, "n": len(values), "cy": cy, "pad": pad}


def map_advantage(pool_a, pool_b, name_a: str, name_b: str,
                  min_maps: int = 2) -> list[dict]:
    """Per-map round-differential advantage of A over B.

    Only maps BOTH teams have played at least `min_maps` times. A map one
    side has never played is not an advantage, it is an unknown, and
    rendering it as a long bar would invent the most decision-relevant
    number on the page.
    """
    a = {p["map"]: p for p in pool_a}
    b = {p["map"]: p for p in pool_b}
    rows = []
    for name in sorted(set(a) & set(b)):
        pa, pb = a[name], b[name]
        va = pa["round_diff"].value
        vb = pb["round_diff"].value
        if va is None or vb is None:
            continue
        if pa["played"] < min_maps or pb["played"] < min_maps:
            continue
        rows.append({
            "label": name,
            "value": round(va - vb, 2),
            "n": min(pa["played"], pb["played"]),
            "detail": f"{name_a} {va:+.1f} ({pa['played']}) · "
                      f"{name_b} {vb:+.1f} ({pb['played']})",
        })
    return sorted(rows, key=lambda r: -abs(r["value"]))


def heat_cells(pool_a, pool_b, name_a: str, name_b: str) -> dict:
    """Both teams across EVERY map, including the ones only one side plays.

    The advantage chart deliberately drops maps a team has never played,
    because an unknown is not an edge. But in a Bo3 veto, "they have 26
    games on Inferno and we have 3" is one of the most decision-relevant
    facts available — it just is not an advantage figure. This shows the
    shape of both pools, holes included, with unplayed cells marked rather
    than coloured.

    Intensity is capped at CELL_ALPHA_MAX so the value printed inside each
    cell stays readable; colour is a diverging cue, the number is the fact.
    """
    a = {p["map"]: p for p in pool_a}
    b = {p["map"]: p for p in pool_b}
    names = sorted(set(a) | set(b))
    if not names:
        return {"rows": [], "teams": [name_a, name_b], "max": 0}

    values = [p["round_diff"].value for p in list(a.values()) + list(b.values())
              if p["round_diff"].value is not None]
    vmax = _nice_max(max((abs(v) for v in values), default=1.0), floor=1.0)

    rows = []
    for name in names:
        cells = []
        for pool in (a, b):
            p = pool.get(name)
            if not p or p["round_diff"].value is None:
                cells.append({"played": 0, "value": None, "fill": None,
                              "alpha": 0, "label": "—", "detail": "never played"})
                continue
            v = p["round_diff"].value
            cells.append({
                "played": p["played"],
                "value": v,
                "fill": POS if v >= 0 else NEG,
                "alpha": round(min(abs(v) / vmax, 1.0) * CELL_ALPHA_MAX, 3),
                "label": f"{v:+.1f}",
                "detail": f"{p['win_rate'].raw} over {p['played']} maps",
            })
        rows.append({"map": name, "cells": cells,
                     "total": sum(c["played"] for c in cells)})
    return {"rows": sorted(rows, key=lambda r: -r["total"]),
            "teams": [name_a, name_b], "max": vmax}


def sparkline(points, width: int = 300, height: int = 54, pad: int = 6) -> dict:
    """Implied probability over time. One series, so no legend — the caption
    names it.

    `points`: [{"at": datetime, "p": 0..1}] in time order. A flat line and a
    line that moved twenty points tell completely different stories about
    what the market learned between listing and kickoff, and neither is
    visible anywhere else in the tool.
    """
    points = [pt for pt in points if pt.get("p") is not None]
    if len(points) < 2:
        return {"path": "", "dots": [], "n": len(points), "width": width,
                "height": height, "first": None, "last": None, "move": None,
                "mid_y": height / 2}
    t0 = points[0]["at"]
    span = (points[-1]["at"] - t0).total_seconds() or 1.0
    usable_w = width - pad * 2
    usable_h = height - pad * 2

    def xy(pt):
        x = pad + (pt["at"] - t0).total_seconds() / span * usable_w
        y = pad + (1 - pt["p"]) * usable_h        # 100% at the top
        return x, y

    coords = [xy(pt) for pt in points]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    first, last = points[0]["p"], points[-1]["p"]
    move = last - first
    return {
        "path": path,
        "dots": [{"cx": round(x, 1), "cy": round(y, 1), "p": pt["p"],
                  "at": pt["at"]} for (x, y), pt in zip(coords, points)],
        "n": len(points), "width": width, "height": height,
        "first": first, "last": last, "move": round(move, 4),
        "fill": POS if move >= 0 else NEG,
        "mid_y": pad + 0.5 * usable_h,
    }


def hour_bars(rows, width: int = 620, height: int = 90, pad: int = 4) -> dict:
    """Writes per hour. Bar height carries magnitude, so colour does not.

    A missing hour must render as a GAP, not as a zero-height bar sitting on
    the axis — the whole point is spotting when collection stopped, and an
    absent bar and a bar of zero look identical if both are drawn.
    """
    rows = list(rows)
    if not rows:
        return {"bars": [], "width": width, "height": height, "max": 0, "n": 0}
    vmax = max(r["rows"] for r in rows) or 1
    slot = width / max(len(rows), 1)
    bw = max(2.0, slot - 2)
    bars = []
    for i, r in enumerate(rows):
        h = (r["rows"] / vmax) * (height - pad * 2)
        bars.append({
            "x": round(i * slot, 1), "w": round(bw, 1),
            "y": round(height - pad - h, 1), "h": round(max(h, 1.0), 1),
            "rows": r["rows"], "hour": r["hour"],
            "gap": r["rows"] == 0,
        })
    return {"bars": bars, "width": width, "height": height, "max": vmax,
            "n": len(rows)}


def calibration_rows(buckets, width: int = 380, label_w: int = 74) -> dict:
    """Forecast versus reality per bucket, as a dumbbell.

    Two paired values per row is a before/after shape, which is what a
    dumbbell is for — two bars would imply the pair are independent
    quantities rather than the same thing claimed and observed.
    """
    out = []
    plot_w = width - label_w - 40
    for b in buckets:
        if not b["n"]:
            continue
        fx = label_w + b["mean_forecast"] * plot_w
        ax = label_w + b["actual"] * plot_w
        out.append({
            "label": f"{b['lo']:.0%}-{b['hi']:.0%}",
            "n": b["n"],
            "forecast": b["mean_forecast"], "actual": b["actual"],
            "gap": b["gap"],
            "x1": round(min(fx, ax), 1), "x2": round(max(fx, ax), 1),
            "fx": round(fx, 1), "ax": round(ax, 1),
            "good": abs(b["gap"]) <= 0.05,
        })
    return {"rows": out, "width": width, "label_w": label_w,
            "plot_w": plot_w, "ticks": [0.0, 0.25, 0.5, 0.75, 1.0]}
