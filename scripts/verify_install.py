#!/usr/bin/env python3
"""Check the working copy is complete and importable.

    python scripts/verify_install.py

Run this after extracting any delivered archive, and before spending time
debugging an error that might just be a missing file.

Written because incomplete extraction has cost this project three separate
debugging rounds: a double-wrapped tests folder, a phase1_sync.py whose
sync() was absent, and edgedesk/resolve/clans.py never landing. Each
presented as a different confusing error, and none of them was a code
problem.

Missing __init__.py deserves special mention: Python treats the directory as
a NAMESPACE PACKAGE and keeps working, so the gap stays invisible until some
later import fails with the unhelpful "(unknown location)".
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PACKAGES = [
    "edgedesk/__init__.py",
    "edgedesk/resolve/__init__.py",
    "edgedesk/sources/__init__.py",
    "edgedesk/stats/__init__.py",
    "edgedesk/web/__init__.py",
]

MODULES = [
    "edgedesk.db",
    "edgedesk.health",
    "edgedesk.notify",
    "edgedesk.sources.kalshi",
    "edgedesk.sources.bo3",
    "edgedesk.resolve.fixtures",
    "edgedesk.resolve.clans",
    "edgedesk.stats.core",
    "edgedesk.stats.team",
    "edgedesk.stats.maps",
    "edgedesk.stats.h2h",
    "edgedesk.queries",
    "edgedesk.stats.scoring",
    "edgedesk.stats.roster",
    "edgedesk.stats.backtest",
    "edgedesk.decisions",
    "edgedesk.web.app",
    "edgedesk.web.charts",
]

SCRIPTS = [
    "scripts/phase0_collect.py",
    "scripts/phase1_sync.py",
    "scripts/sync_maps.py",
    "scripts/harvest_aliases.py",
    "scripts/diagnose_maps.py",
    "scripts/healthcheck.py",
    "scripts/notify_failure.py",
    "scripts/dossier.py",
    "scripts/decide.py",
    "scripts/review.py",
    "scripts/sync_players.py",
    "scripts/sync_tournaments.py",
    "scripts/explore_bo3.py",
    "scripts/serve.py",
    "scripts/backtest.py",
    "scripts/vs_market.py",
    "scripts/link_market_teams.py",
    "scripts/fix_settled_times.py",
]

MIGRATIONS = [
    "migrations/001_init.sql",
    "migrations/002_price_detail.sql",
    "migrations/003_bo3.sql",
    "migrations/004_team_aliases.sql",
    "migrations/005_decisions.sql",
    "migrations/006_players.sql",
    "migrations/007_tournaments.sql",
    "migrations/008_resolve_history.sql",
    "migrations/009_expiration_time.sql",
    "migrations/run.py",
]

TEMPLATES = [
    "edgedesk/web/templates/base.html",
    "edgedesk/web/templates/_macros.html",
    "edgedesk/web/templates/_charts.html",
    "edgedesk/web/templates/slate.html",
    "edgedesk/web/templates/match.html",
    "edgedesk/web/templates/log.html",
    "edgedesk/web/templates/queue.html",
    "edgedesk/web/templates/health.html",
    "edgedesk/web/templates/missing.html",
]

TESTS = [
    "tests/conftest.py",
    "tests/test_parsers.py",
    "tests/test_config.py",
    "tests/test_resolve.py",
    "tests/test_clans.py",
    "tests/test_health.py",
    "tests/test_notify.py",
    "tests/test_phase1_sync.py",
    "tests/test_stats_core.py",
    "tests/test_stats_team.py",
    "tests/test_stats_maps.py",
    "tests/test_stats_h2h.py",
    "tests/test_dossier.py",
    "tests/test_scoring.py",
    "tests/test_review.py",
    "tests/test_decide.py",
    "tests/test_roster.py",
    "tests/test_web.py",
    "tests/test_backtest.py",
    "tests/test_charts.py",
    "tests/test_vs_market.py",
    "tests/test_collect_writes.py",
    "tests/test_h2h_dedup.py",
    "tests/fixtures/kalshi_markets.json",
]

# Functions that must exist. A file can be present and still be missing the
# thing that matters -- phase1_sync.py was complete apart from sync().
SYMBOLS = [
    ("scripts/phase1_sync.py", ["def sync(", "def resolve_all(", "def main("]),
    ("scripts/sync_maps.py", ["def main("]),
    ("edgedesk/resolve/clans.py", ["def assign(", "def rounds_for(",
                                   "def assign_side("]),
    ("edgedesk/resolve/fixtures.py", ["def resolve(", "def window_slice(",
                                      "def similarity("]),
    ("edgedesk/stats/core.py", ["class Stat", "def n_eff(", "def shrink("]),
    ("edgedesk/stats/team.py", ["def win_rate(", "def forfeit_rate("]),
    ("edgedesk/stats/maps.py", ["def round_win_pct(", "def map_pool("]),
    ("edgedesk/stats/h2h.py", ["def record(", "def common_opponents("]),
    ("edgedesk/queries.py", ["def dossier_rows(", "def slate("]),
    ("scripts/dossier.py", ["def show_match(", "def show_slate("]),
    ("edgedesk/stats/scoring.py", ["def brier(", "def skill_vs_market(",
                                   "def calibration("]),
    ("scripts/review.py", ["def do_score("]),
    ("scripts/decide.py", ["def main("]),
    ("edgedesk/stats/roster.py", ["def churn_term(", "def describe("]),
    ("scripts/sync_players.py", ["def main("]),
    ("scripts/sync_tournaments.py", ["def main("]),
    ("edgedesk/web/app.py", ["def create_app(", "def decide("]),
    ("edgedesk/web/charts.py", ["def diverging_bars(", "def strip(",
                                "def map_advantage("]),
    ("edgedesk/stats/backtest.py", ["def walk_forward(", "def summarise(",
                                    "random_control"]),
    ("scripts/vs_market.py", ["def build_events(", "def mid_prob("]),
    ("scripts/link_market_teams.py", ["def main(", "def aliases("]),
    ("scripts/phase0_collect.py", ["def write_markets(", "def collect("]),
    ("edgedesk/decisions.py", ["def log(", "def validate(",
                               "def market_prob_for("]),
]

problems: list[str] = []


def check_files(label, paths, hint=""):
    missing = [p for p in paths if not (ROOT / p).exists()]
    status = "ok" if not missing else "MISSING"
    print(f"[{status:>7}] {label:14} {len(paths) - len(missing)}/{len(paths)}")
    for p in missing:
        print(f"            - {p}")
        problems.append(f"missing file: {p}{hint}")


def main() -> int:
    print(f"checking {ROOT}\n")

    check_files("packages", PACKAGES,
                "  (empty file; create it -- without it Python silently uses "
                "a namespace package and later imports fail)")
    check_files("scripts", SCRIPTS)
    check_files("migrations", MIGRATIONS)
    check_files("templates", TEMPLATES)
    check_files("tests", TESTS)

    print()
    for mod in MODULES:
        try:
            importlib.import_module(mod)
            print(f"[     ok] import {mod}")
        except Exception as exc:                             # noqa: BLE001
            print(f"[ FAILED] import {mod}: {exc.__class__.__name__}: {exc}")
            problems.append(f"import failed: {mod}: {exc}")

    print()
    for path, symbols in SYMBOLS:
        f = ROOT / path
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        absent = [s for s in symbols if s not in text]
        if absent:
            print(f"[ FAILED] {path} lacks {', '.join(absent)}")
            problems.append(f"{path} lacks {absent}")
        else:
            print(f"[     ok] {path} defines {len(symbols)} expected symbols")

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):\n")
        for p in problems:
            print(f"  - {p}")
        print("\nMost of these mean an archive did not fully extract. "
              "Re-extract into the project root, overwriting.")
        return 1
    print("OK  working copy is complete and importable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
