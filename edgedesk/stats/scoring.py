"""Forecast scoring and calibration. Pure functions.

A Brier score alone says very little. Predicting a heavy favourite at 0.90
and being right scores 0.01, which looks excellent and required no skill:
the market was offering the same number. So everything here is built around
comparing a forecast to the price it was made against.

Kalshi's fee is part of the picture too. A call that is 2 points better than
the market is not profitable if crossing the spread costs 3.
"""
from __future__ import annotations

from .core import Stat

# Verified against Kalshi's published schedule, August 2026.
TAKER_FEE_COEFF = 0.07
MAKER_FEE_MULTIPLIER = 0.25


def brier(forecast: float, outcome: int) -> float:
    """(forecast − outcome)². Lower is better; 0.25 is a coin flip.

    `outcome` is 1 when the forecast team won, 0 when it lost.
    """
    return round((forecast - outcome) ** 2, 5)


def taker_fee(price_cents: int | float) -> float:
    """0.07 × C × (1−C), in cents per contract, C in dollars.

    Peaks near 1.75c at 50c and falls toward the wings. This is why a
    1-cent theoretical edge on a coin-flip market is a losing trade.
    """
    c = float(price_cents) / 100.0
    return round(100 * TAKER_FEE_COEFF * c * (1 - c), 4)


def maker_fee(price_cents: int | float) -> float:
    return round(taker_fee(price_cents) * MAKER_FEE_MULTIPLIER, 4)


def breakeven_edge_cents(price_cents: int | float) -> float:
    """How many cents of edge a taker needs before the trade is worth doing.

    Retrospective use only: this says whether a logged call had enough edge
    to survive costs, not what to bet.
    """
    return taker_fee(price_cents)


def score_rows(rows) -> list[dict]:
    """Attach brier / market_brier to decision rows that have an outcome.

    Rows need: prob_team_a, market_prob_a, result ('team_a' | 'team_b').
    Anything else is skipped -- 'fmp' resolves at market price, so it is
    not a forecast that came true or false.
    """
    out = []
    for r in rows:
        result = r.get("result")
        if result not in ("team_a", "team_b"):
            continue
        p = r.get("prob_team_a")
        if p is None:
            continue
        outcome = 1 if result == "team_a" else 0
        row = dict(r)
        row["brier"] = brier(float(p), outcome)
        mp = r.get("market_prob_a")
        row["market_brier"] = (brier(float(mp), outcome)
                               if mp is not None else None)
        if row["market_brier"] is not None:
            row["brier_edge"] = round(row["market_brier"] - row["brier"], 5)
        else:
            row["brier_edge"] = None
        out.append(row)
    return out


def mean_brier(rows, field: str = "brier") -> Stat:
    vals = [r[field] for r in rows
            if r.get(field) is not None]
    if not vals:
        return Stat.unavailable("no scored decisions yet")
    mean = sum(vals) / len(vals)
    return Stat(value=round(mean, 5), n=len(vals), n_eff=float(len(vals)),
                raw=f"over {len(vals)} scored")


def skill_vs_market(rows) -> Stat:
    """Mean (market_brier − brier). Positive means you beat the price.

    This is the only number here worth acting on, and it needs a lot of
    decisions before it means anything: single-event Brier differences are
    dominated by which way one coin flip landed.
    """
    vals = [r["brier_edge"] for r in rows if r.get("brier_edge") is not None]
    if not vals:
        return Stat.unavailable("no decisions with a market price recorded")
    mean = sum(vals) / len(vals)
    note = None
    if len(vals) < 30:
        note = (f"{len(vals)} decisions — far too few to distinguish skill "
                "from luck; treat as bookkeeping, not evidence")
    return Stat(value=round(mean, 5), n=len(vals), n_eff=float(len(vals)),
                raw=f"over {len(vals)} scored", note=note)


def calibration(rows, bins: int = 5) -> list[dict]:
    """Forecast bucket vs realised frequency.

    Well calibrated means that of the calls made at ~70%, about 70% happen.
    Deliberately few buckets: at realistic sample sizes ten deciles produce
    ten meaningless rows, which reads as detail rather than as noise.
    """
    buckets: list[dict] = []
    width = 1.0 / bins
    for i in range(bins):
        lo, hi = i * width, (i + 1) * width
        picked = []
        for r in rows:
            p = r.get("prob_team_a")
            if p is None or r.get("result") not in ("team_a", "team_b"):
                continue
            p = float(p)
            if lo <= p < hi or (i == bins - 1 and p == 1.0):
                picked.append(r)
        if not picked:
            buckets.append({"lo": lo, "hi": hi, "n": 0,
                            "mean_forecast": None, "actual": None})
            continue
        mean_f = sum(float(r["prob_team_a"]) for r in picked) / len(picked)
        hits = sum(1 for r in picked if r["result"] == "team_a")
        buckets.append({
            "lo": lo, "hi": hi, "n": len(picked),
            "mean_forecast": round(mean_f, 4),
            "actual": round(hits / len(picked), 4),
            "gap": round(hits / len(picked) - mean_f, 4),
        })
    return buckets


def tag_performance(rows) -> list[dict]:
    """Skill-vs-market grouped by tag.

    Which reasons actually preceded good calls. Every count here is small
    by construction, so the n is what matters on each row -- a tag used
    three times has told you nothing.
    """
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        for tag in (r.get("tags") or []):
            by_tag.setdefault(tag, []).append(r)
    out = []
    for tag, rs in by_tag.items():
        edges = [r["brier_edge"] for r in rs if r.get("brier_edge") is not None]
        out.append({
            "tag": tag,
            "n": len(rs),
            "mean_edge": round(sum(edges) / len(edges), 5) if edges else None,
        })
    return sorted(out, key=lambda t: -t["n"])
