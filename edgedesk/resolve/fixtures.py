"""Fixture-tuple resolution: link a Kalshi event to a bo3.gg match.

Matching on team NAMES alone is fragile — Kalshi's "usst esports" and bo3's
"UUST" score badly on any string metric. Matching on the TUPLE
(team A, team B, start time) is not, because the opponent and the timestamp
carry the pair even when one name is a poor match.

Start time comes from Kalshi's `close_time - 48h`, which is exact, rather than
from the ticker's Eastern-time HHMM field.

Pure functions — no I/O, no database. That is what makes this testable, and
this is the module where a wrong answer is most expensive: a bad link produces
a confident dossier about the wrong match.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

# Scoring weights. Team names dominate; the event name is a tiebreaker only,
# because Kalshi and bo3 name tournaments quite differently.
W_TEAM = 0.45
W_EVENT = 0.10

ACCEPT = 0.85          # auto-accept
FUZZY_ACCEPT = 0.92    # stage-3 fallback, both names must clear this
SOLE_FLOOR = 0.55       # min team score to accept an unopposed candidate
WINDOW = timedelta(minutes=30)
WIDE_WINDOW = timedelta(hours=3)

# Tokens that carry no identifying information for a CS2 org.
NOISE = {
    "esports", "esport", "gaming", "team", "club", "the", "cs", "cs2",
    "csgo", "academy", "juniors", "junior", "youngsters", "prospects",
}


def normalise(name: str | None) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(name: str | None) -> set[str]:
    """Significant tokens, noise words removed.

    Falls back to the full token set when a name is entirely noise, so that
    a team literally called "Team" still matches something.
    """
    raw = set(normalise(name).split())
    meaningful = raw - NOISE
    return meaningful or raw


def similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity tuned for esports org names.

    Combines token overlap (handles "ex-RUBY" vs "RUBY", word reordering,
    and dropped suffixes like "Esports") with a character-level ratio
    (handles "usst" vs "uust" typo-level drift).
    """
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    ta, tb = tokens(a), tokens(b)
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
        # containment: "ruby" inside "ex ruby" should score high
        containment = len(ta & tb) / min(len(ta), len(tb))
    else:
        jaccard = containment = 0.0

    # Compare MEANINGFUL tokens, not the raw string: "usst esports" vs "UUST"
    # must compare "usst"/"uust", not "usstesports"/"uust".
    ja, jb = "".join(sorted(ta)), "".join(sorted(tb))
    char = _ratio(ja, jb)
    token_score = max(jaccard, containment * 0.95)

    # Every token of the shorter name appears in the longer one
    # ("LFO UKRAINE" vs "LFO"). That is decisive on its own; blending in a
    # character ratio only lets a weak signal veto a certain match.
    if containment >= 1.0:
        return round(max(token_score, char), 4)

    # One name is a substring of the other ("RoundsGG" vs "ROUNDS"). Common
    # where orgs append GG / PL / a country tag. Length-guarded so short
    # fragments cannot match inside unrelated longer names.
    short, long_ = (ja, jb) if len(ja) <= len(jb) else (jb, ja)
    if len(short) >= 5 and short in long_:
        ratio = len(short) / len(long_)
        if ratio >= 0.6:
            return round(max(token_score, char, 0.85 + 0.15 * ratio), 4)

    return round(max(token_score, char) * 0.7 + min(token_score, char) * 0.3, 4)


def _ratio(a: str, b: str) -> float:
    """Character bigram Dice coefficient. Cheap, no dependency, and more
    stable than edit distance on short org names."""
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    ba = _bigrams(a)
    bb = _bigrams(b)
    if not ba or not bb:
        return 0.0
    overlap = sum((ba & bb).values())
    return 2 * overlap / (sum(ba.values()) + sum(bb.values()))


def _bigrams(s: str):
    from collections import Counter
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


def best_similarity(kalshi_name: str, *aliases) -> float:
    """Best score for one Kalshi name against a candidate's known aliases.

    Evaluated PER TEAM, not per pairing: "NAVI" matches the acronym while
    "TheMongolz" matches the registered name, and a whole-pairing choice
    between names-only and acronyms-only captures neither.
    """
    return max((similarity(kalshi_name, a) for a in aliases if a), default=0.0)


def pair_score(kalshi_a: str, kalshi_b: str,
               cand_a, cand_b) -> tuple[float, bool]:
    """Best team-pair score across both orientations.

    cand_a / cand_b may be a plain name or a tuple of aliases
    (name, acronym). Kalshi's market order and bo3's team1/team2 order are
    unrelated, so both assignments must be tried. Returns (score, swapped).
    """
    a = cand_a if isinstance(cand_a, (tuple, list)) else (cand_a,)
    b = cand_b if isinstance(cand_b, (tuple, list)) else (cand_b,)
    direct = (best_similarity(kalshi_a, *a) + best_similarity(kalshi_b, *b)) / 2
    swapped = (best_similarity(kalshi_a, *b) + best_similarity(kalshi_b, *a)) / 2
    return (direct, False) if direct >= swapped else (swapped, True)


def score_candidate(kalshi_a: str, kalshi_b: str, kalshi_event: str | None,
                    candidate: dict) -> dict:
    """Score one bo3 match as a candidate for a Kalshi event.

    `candidate` needs: team_a_name, team_b_name, event_name (optional),
    plus whatever identifiers the caller wants echoed back.
    """
    # Each team carries its registered name and bo3's acronym; the best
    # alias per team wins. This is what rescues "Natus Vincere" vs "NAVI".
    team_score, swapped = pair_score(
        kalshi_a, kalshi_b,
        (candidate.get("team_a_name"), candidate.get("team_a_acronym")),
        (candidate.get("team_b_name"), candidate.get("team_b_acronym")),
    )
    event_score = similarity(kalshi_event, candidate.get("event_name"))
    total = round(team_score * (W_TEAM * 2) + event_score * W_EVENT, 4)
    return {
        **candidate,
        "team_score": round(team_score, 4),
        "event_score": round(event_score, 4),
        "score": total,
        "swapped": swapped,
    }


def rank_candidates(kalshi_a: str, kalshi_b: str, kalshi_event: str | None,
                    candidates: list[dict]) -> list[dict]:
    scored = [score_candidate(kalshi_a, kalshi_b, kalshi_event, c)
              for c in candidates]
    return sorted(scored, key=lambda c: c["score"], reverse=True)


def resolve(kalshi_a: str, kalshi_b: str, kalshi_event: str | None,
            candidates: list[dict]) -> dict:
    """Return a decision dict.

    verdict is one of: 'accept' | 'fuzzy' | 'queue'
    """
    if not candidates:
        return {"verdict": "queue", "reason": "no candidates in window",
                "best": None, "ranked": []}

    ranked = rank_candidates(kalshi_a, kalshi_b, kalshi_event, candidates)
    best = ranked[0]

    if best["score"] >= ACCEPT:
        return {"verdict": "accept", "reason": "tuple match",
                "best": best, "ranked": ranked[:5]}

    # Sole-candidate rule. The time window has already done heavy filtering;
    # if exactly one CS2 match sits within +/-30min AND both teams are at
    # least plausible, that is strong evidence. The floor is what stops an
    # unrelated match in the same window from being adopted.
    if len(ranked) == 1 and best["team_score"] >= SOLE_FLOOR:
        return {"verdict": "accept", "reason": "sole candidate in window",
                "best": best, "ranked": ranked}

    # Stage 3: both individual names must be strong, even if the combined
    # score missed. Guards against one great match carrying one bad one.
    alias_a = (best.get("team_a_name"), best.get("team_a_acronym"))
    alias_b = (best.get("team_b_name"), best.get("team_b_acronym"))
    sim_direct = min(best_similarity(kalshi_a, *alias_a),
                     best_similarity(kalshi_b, *alias_b))
    sim_swapped = min(best_similarity(kalshi_a, *alias_b),
                      best_similarity(kalshi_b, *alias_a))
    if max(sim_direct, sim_swapped) >= FUZZY_ACCEPT:
        return {"verdict": "fuzzy", "reason": "both names strong",
                "best": best, "ranked": ranked[:5]}

    return {"verdict": "queue", "reason": f"best score {best['score']:.2f}",
            "best": best, "ranked": ranked[:5]}


def in_window(kalshi_start, candidate_start, wide: bool = False) -> bool:
    if kalshi_start is None or candidate_start is None:
        return False
    span = WIDE_WINDOW if wide else WINDOW
    return abs(candidate_start - kalshi_start) <= span


def extract_event_name(rules_primary: str | None) -> str | None:
    """Pull the tournament name out of Kalshi's rules text.

    Example input:
        "If BESTIA Academy wins the Gamers Club Liga Serie A 2026: BESTIA
         Academy vs. underw0rld CS2 match originally scheduled for ..."
    yields "Gamers Club Liga Serie A 2026".
    """
    if not rules_primary:
        return None
    m = re.search(r"\bwins the (.+?):", rules_primary)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bthe (.+?):", rules_primary)
    return m.group(1).strip() if m else None
