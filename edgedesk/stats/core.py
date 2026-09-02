"""Sample weighting, shrinkage, and the Stat value object.

Pure functions. Rows in, values out. No database, no network, no globals.

The central idea here is that a bare number is not a statistic. "100% win
rate" over two matches and over forty are the same float and completely
different facts, and a dossier showing only the float manufactures
confidence at exactly the moment money is involved. So every figure this
package produces is a `Stat`, which cannot be constructed without its sample
size — the display contract is enforced by the type, not by remembering.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

HALF_LIFE_DAYS = 90.0
SHRINK_K = 10.0

# Below this many effective observations, a rate is too thin to read as a
# rate at all and the UI should lead with the raw record.
THIN_N_EFF = 3.0


@dataclass(frozen=True)
class Stat:
    """A number that carries its own provenance.

    `value` is None whenever the figure could not be computed. That is a
    first-class outcome, not an error: a partial dossier is correct
    behaviour, and `note` says why so the UI can render an explicit gap
    instead of a silent omission or a zero.
    """
    value: float | None
    n: int = 0
    n_eff: float = 0.0
    staleness_days: float | None = None
    raw: str = ""
    shrunk: bool = False
    note: str | None = None

    @classmethod
    def unavailable(cls, note: str) -> "Stat":
        return cls(value=None, note=note)

    @property
    def is_thin(self) -> bool:
        """True when the sample is too small to lead with the rate."""
        return self.value is not None and self.n_eff < THIN_N_EFF

    def render(self, pct: bool = True, places: int | None = None) -> str:
        """One-line rendering that always shows the evidence.

        Examples:
            '62% (13-8, n=21, n_eff=14.2, 6d old)'
            '31% adj. (0-6, n=6, n_eff=5.8, 3d old)'
            'no data — no completed matches in window'
        """
        if self.value is None:
            return f"no data — {self.note or 'unavailable'}"
        # Percentages default to whole numbers; raw values to 2dp. A count
        # (fatigue: "2 matches in 48h") passes places=0 explicitly, because
        # rendering it as 2.00 implies a precision it does not have.
        dp = places if places is not None else (0 if pct else 2)
        shown = (f"{self.value * 100:.{dp}f}%" if pct
                 else f"{self.value:.{dp}f}")
        bits = []
        if self.raw:
            bits.append(self.raw)
        bits.append(f"n={self.n}")
        bits.append(f"n_eff={self.n_eff:.1f}")
        if self.staleness_days is not None:
            bits.append(f"{self.staleness_days:.0f}d old")
        adj = " adj." if self.shrunk else ""
        return f"{shown}{adj} ({', '.join(bits)})"


def _as_utc(value) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def age_days(when, now=None) -> float | None:
    when = _as_utc(when)
    if when is None:
        return None
    now = _as_utc(now) or datetime.now(timezone.utc)
    return (now - when).total_seconds() / 86400.0


def decay_weight(when, now=None, half_life: float = HALF_LIFE_DAYS) -> float:
    """0.5 ** (age / half_life), clamped to [0, 1].

    A match from today counts 1.0; one from 90 days ago counts 0.5. Future
    dates clamp to 1.0 rather than exceeding it — a scheduled match should
    never outweigh a played one.
    """
    age = age_days(when, now)
    if age is None:
        return 0.0
    if age <= 0:
        return 1.0
    return 0.5 ** (age / half_life)


def n_eff(rows, key="scheduled_at", now=None,
          half_life: float = HALF_LIFE_DAYS) -> float:
    """Effective sample size: Σ 0.5^(age_days / half_life).

    Twenty matches all six months old is n=20 but n_eff≈5 — the figure the
    reader actually needs in order to know how much the number is worth.
    """
    return round(sum(decay_weight(r.get(key), now, half_life) for r in rows), 4)


def staleness(rows, key="scheduled_at", now=None) -> float | None:
    """Days since the most recent row. None when there are none."""
    ages = [age_days(r.get(key), now) for r in rows]
    ages = [a for a in ages if a is not None]
    return round(min(ages), 2) if ages else None


def shrink(wins: float, n: float, pool_mean: float,
           k: float = SHRINK_K) -> float:
    """(w + k·mean) / (n + k) — pull small samples toward the pool.

    A 0-6 team is not a 0% team. With k=10 and a pool mean of 0.5 it reads
    31%, which is a defensible prior rather than a claim the sample cannot
    support. The raw record travels alongside so the reader sees both.
    """
    if n + k <= 0:
        return pool_mean
    return (wins + k * pool_mean) / (n + k)


def rate(wins: int, losses: int, rows=None, pool_mean: float | None = None,
         key="scheduled_at", now=None, k: float = SHRINK_K) -> Stat:
    """Build a Stat for a win rate, shrinking when a pool mean is given.

    `rows` is the underlying sample, used only for n_eff and staleness --
    pass it whenever you have it, because a rate without those is exactly
    the half-told number this package exists to prevent.
    """
    n = wins + losses
    if n == 0:
        return Stat.unavailable("no completed matches in window")
    raw_value = wins / n
    value = raw_value if pool_mean is None else shrink(wins, n, pool_mean, k)
    rows = list(rows or [])
    return Stat(
        value=round(value, 4),
        n=n,
        n_eff=n_eff(rows, key, now) if rows else float(n),
        staleness_days=staleness(rows, key, now) if rows else None,
        raw=f"{wins}-{losses}",
        shrunk=pool_mean is not None,
    )
