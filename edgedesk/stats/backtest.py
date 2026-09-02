"""Walk-forward signal testing. Pure functions.

The question: does any figure in the dossier actually predict who wins?

LOOKAHEAD IS THE ONLY BUG THAT MATTERS HERE. Computing a team's win rate
over a window that includes the match being predicted produces a beautiful,
completely false result — and it looks exactly like a working signal. So
this module never receives the full history and filters it. Instead it walks
matches in chronological order, holding each team's PRIOR matches only, and
appends the current match to that history after scoring it. A signal
physically cannot see its own outcome.

Everything reports a sample size and a standard error, because 55% over 200
matches and 52% over 16,000 are opposite findings and the raw percentage
hides which one you have.
"""
from __future__ import annotations

import math
import random

MIN_HISTORY = 10        # prior matches each side needs before a match counts
GAP_BUCKETS = (0.02, 0.05, 0.10, 0.20)


def _finished(row) -> bool:
    return (row.get("status") == "finished"
            and not row.get("decided_by_default")
            and row.get("winner_team_id") is not None)


def _rate(hist, team_id):
    played = [r for r in hist if _finished(r)]
    if not played:
        return None
    w = sum(1 for r in played if r["winner_team_id"] == team_id)
    return w / len(played)


def _round_pct(map_hist, team_id):
    won = lost = 0
    for r in map_hist:
        if r.get("winner_team_id") is None or r.get("is_default"):
            continue
        a, b = r.get("team_a_rounds"), r.get("team_b_rounds")
        if a is None or b is None:
            continue
        if r.get("team_a_id") == team_id:
            won += a; lost += b
        elif r.get("team_b_id") == team_id:
            won += b; lost += a
    total = won + lost
    return (won / total) if total else None


def _round_diff(map_hist, team_id):
    diffs = []
    for r in map_hist:
        if r.get("winner_team_id") is None or r.get("is_default"):
            continue
        a, b = r.get("team_a_rounds"), r.get("team_b_rounds")
        if a is None or b is None:
            continue
        if r.get("team_a_id") == team_id:
            diffs.append(a - b)
        elif r.get("team_b_id") == team_id:
            diffs.append(b - a)
    return (sum(diffs) / len(diffs)) if diffs else None


def _form(hist, team_id, last=5):
    played = [r for r in hist if _finished(r)][-last:]
    if not played:
        return None
    return sum(1 for r in played if r["winner_team_id"] == team_id) / len(played)


def _h2h(hist, team_id, opponent):
    met = [r for r in hist if _finished(r)
           and {r.get("team_a_id"), r.get("team_b_id")} == {team_id, opponent}]
    if not met:
        return None
    return sum(1 for r in met if r["winner_team_id"] == team_id) / len(met)


# name -> (needs_maps, fn(hist, map_hist, team_id, opponent) -> float | None)
SIGNALS = {
    "win_rate":      (False, lambda h, mh, t, o: _rate(h, t)),
    "form_last5":    (False, lambda h, mh, t, o: _form(h, t, 5)),
    "h2h":           (False, lambda h, mh, t, o: _h2h(h, t, o)),
    "round_win_pct": (True,  lambda h, mh, t, o: _round_pct(mh, t)),
    "avg_round_diff": (True, lambda h, mh, t, o: _round_diff(mh, t)),
    # Control. A signal with no information must land at 50%. If it does
    # not, the harness is broken and every other number here is worthless.
    "random_control": (False, lambda h, mh, t, o: random.random()),
}


def walk_forward(matches, map_rows, min_history: int = MIN_HISTORY,
                 seed: int = 7) -> dict:
    """Score every signal on every match, using only prior data.

    `matches` and `map_rows` need not be sorted; they are sorted here, which
    is the step the whole method depends on.
    """
    random.seed(seed)
    matches = sorted((m for m in matches if m.get("scheduled_at")),
                     key=lambda m: m["scheduled_at"])
    maps_by_match: dict = {}
    for r in map_rows:
        maps_by_match.setdefault(r["match_id"], []).append(r)

    hist: dict[int, list] = {}
    map_hist: dict[int, list] = {}
    results = {name: [] for name in SIGNALS}
    skipped = 0

    for m in matches:
        a, b = m.get("team_a_id"), m.get("team_b_id")
        if a is None or b is None:
            continue

        if _finished(m):
            ha, hb = hist.get(a, []), hist.get(b, [])
            enough = (sum(1 for r in ha if _finished(r)) >= min_history
                      and sum(1 for r in hb if _finished(r)) >= min_history)
            if enough:
                a_won = m["winner_team_id"] == a
                for name, (needs_maps, fn) in SIGNALS.items():
                    va = fn(ha, map_hist.get(a, []), a, b)
                    vb = fn(hb, map_hist.get(b, []), b, a)
                    if va is None or vb is None:
                        continue
                    gap = va - vb
                    if gap == 0:
                        continue
                    results[name].append({
                        "gap": gap,
                        "correct": (gap > 0) == a_won,
                        "match_id": m.get("id"),
                        # Kept for reporting only. NEVER join on this: the
                        # match's start comes from bo3 while a Kalshi
                        # event's comes from expiration_time - 48h, and the
                        # two clocks are never bit-identical. Joining on it
                        # silently matches nothing.
                        "scheduled_at": m["scheduled_at"],
                    })
            else:
                skipped += 1

        # History is extended AFTER scoring. This ordering is the entire
        # guarantee against lookahead; do not move it.
        hist.setdefault(a, []).append(m)
        hist.setdefault(b, []).append(m)
        for r in maps_by_match.get(m["id"], []):
            map_hist.setdefault(a, []).append(r)
            map_hist.setdefault(b, []).append(r)

    return {"results": results, "skipped_insufficient_history": skipped,
            "matches_considered": sum(1 for m in matches if _finished(m))}


def summarise(rows, buckets: int = 5) -> dict:
    """Accuracy with a standard error, plus accuracy by gap size.

    Buckets are PERCENTILES of this signal's own gaps, not fixed cutoffs.
    Fixed probability-scaled edges (0.02/0.05/0.10/0.20) put 91% of
    avg_round_diff — which is measured in rounds, not probabilities — into
    one bucket and made its table unreadable. Percentiles give every signal
    equally-filled buckets on its own scale, which is the only way the
    shapes are comparable.

    A signal whose accuracy does not rise across its own buckets is almost
    certainly noise however good its headline number looks.
    """
    n = len(rows)
    if not n:
        return {"n": 0, "accuracy": None, "se": None, "z": None, "buckets": []}
    correct = sum(1 for r in rows if r["correct"])
    acc = correct / n
    se = math.sqrt(0.25 / n)             # SE of a proportion under H0=0.5
    z = (acc - 0.5) / se if se else 0.0

    ordered = sorted(rows, key=lambda r: abs(r["gap"]))
    out_buckets = []
    size = max(1, len(ordered) // buckets)
    for i in range(buckets):
        lo_i = i * size
        hi_i = len(ordered) if i == buckets - 1 else (i + 1) * size
        sel = ordered[lo_i:hi_i]
        if not sel:
            continue
        c = sum(1 for r in sel if r["correct"])
        out_buckets.append({
            "quantile": f"{i * 100 // buckets}-{(i + 1) * 100 // buckets}%",
            "gap_lo": round(abs(sel[0]["gap"]), 3),
            "gap_hi": round(abs(sel[-1]["gap"]), 3),
            "n": len(sel), "accuracy": round(c / len(sel), 4)})
    return {"n": n, "accuracy": round(acc, 4), "se": round(se, 5),
            "z": round(z, 2), "buckets": out_buckets}


def incremental(results, base: str = "win_rate",
                close_threshold: float = 0.05) -> dict:
    """Does a signal add anything WHEN THE BASE SIGNAL IS UNDECIDED?

    Every signal here measures roughly "is this team better", so they agree
    most of the time and each looks impressive alone. The question that
    changes a decision is narrower: on the matches where win rate says the
    sides are level, does anything else still know who wins?

    Keyed by match_id, not by timestamp: two sources' clocks do not agree
    to the second and a timestamp join silently matches nothing.
    """
    base_rows = {r["match_id"]: r for r in results.get(base, [])}
    close = {t for t, r in base_rows.items()
             if abs(r["gap"]) < close_threshold}
    out = {}
    for name, rows in results.items():
        if name == base:
            continue
        sel = [r for r in rows if r["match_id"] in close]
        if sel:
            out[name] = summarise(sel)
    if base_rows:
        out[f"{base} (itself, on these)"] = summarise(
            [r for t, r in base_rows.items() if t in close])
    return {"close_matches": len(close), "threshold": close_threshold,
            "signals": out}


def verdict(summary) -> str:
    """Plain words for whether a result is distinguishable from chance.

    z is against a null of 50%. Roughly: |z| under 2 is noise, 2-3 is
    suggestive, above 3 is hard to dismiss. This is a single unadjusted
    test, so treat borderline results as weaker than they look -- several
    signals are being examined at once.
    """
    if not summary["n"]:
        return "no data"
    z = abs(summary["z"] or 0)
    if summary["n"] < 200:
        return "sample too small to say"
    if z < 2:
        return "indistinguishable from chance"
    if z < 3:
        return "suggestive, not conclusive"
    return "clearly better than chance"
