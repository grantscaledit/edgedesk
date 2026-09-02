"""Flask app. Server-rendered Jinja, no build step.

Every route reads through edgedesk.queries and computes through
edgedesk.stats -- the web layer adds no arithmetic of its own. If a number
appears here that is not a `Stat`, it bypassed the display contract and
should be treated as a bug.
"""
from __future__ import annotations

from flask import Flask, flash, redirect, render_template, request, url_for

from .. import db, decisions, queries
from ..stats import h2h, maps, roster as rstats, scoring
from ..stats import team as tstats


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "edgedesk-local"      # local tool, no auth
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    @app.route("/")
    def slate():
        with db.connect() as conn:
            rows = queries.slate(conn)
        return render_template("slate.html", rows=rows)

    @app.route("/match/<int:match_id>")
    def match(match_id):
        days = request.args.get("days", default=365, type=int)
        with db.connect() as conn:
            data = queries.dossier_rows(conn, match_id, days)
            if not data:
                return render_template("missing.html", match_id=match_id), 404
            ctx = _dossier_context(data, days)
            ctx["recent"] = _decisions_for(conn, match_id)
        return render_template("match.html", **ctx)

    @app.post("/match/<int:match_id>/decision")
    def decide(match_id):
        tags = request.form.getlist("tags")
        with db.connect() as conn:
            try:
                dec_id, market, age = decisions.log(
                    conn, match_id,
                    request.form.get("prob_a"),
                    request.form.get("action"),
                    request.form.get("price") or None,
                    request.form.get("size") or None,
                    tags)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("match", match_id=match_id))
        if market is None:
            flash(f"Logged #{dec_id}. No market price was captured, so this "
                  "one can never be compared to the market.", "warn")
        else:
            stale = "" if (age or 0) <= 90 else f" (quote {age/60:.1f}h old)"
            flash(f"Logged #{dec_id}. Market mid {market:.0%}{stale}.", "ok")
        return redirect(url_for("match", match_id=match_id))

    @app.route("/log")
    def log():
        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute(_LOG_SQL,
                                                  {"user": "default"}).fetchall()]
        scored = scoring.score_rows(
            [r for r in rows if r["result"] in ("team_a", "team_b")])
        return render_template(
            "log.html", rows=rows, scored=scored,
            mine=scoring.mean_brier(scored),
            market=scoring.mean_brier(scored, "market_brier"),
            edge=scoring.skill_vs_market(scored),
            calibration=scoring.calibration(scored, bins=5),
            tags=scoring.tag_performance(scored),
            fmp=[r for r in rows if r["result"] == "fmp"])

    @app.route("/queue")
    def queue():
        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute(_QUEUE_SQL).fetchall()]
        return render_template("queue.html", rows=rows)

    @app.route("/health")
    def health():
        from ..health import collection_gap, storage, worst
        with db.connect() as conn:
            # fetchone() returns None on an empty table, and MAX() over no
            # rows returns NULL. The health page is what you load WHEN
            # things are broken, so it must survive a database with nothing
            # in it -- crashing here would hide the very outage it exists
            # to report.
            row = conn.execute(
                "SELECT EXTRACT(EPOCH FROM (now() - MAX(captured_at)))/60 AS m "
                "FROM kalshi_price_snapshots").fetchone()
            gap = row["m"] if row else None
            row = conn.execute(
                "SELECT pg_database_size(current_database()) AS b").fetchone()
            size = row["b"] if row else None
            runs = [dict(r) for r in conn.execute(_RUNS_SQL).fetchall()]
        checks = [("collection", collection_gap(gap)), ("storage", storage(size))]
        return render_template("health.html", checks=checks, runs=runs,
                               overall=worst(*[c[1][0] for c in checks]))

    return app


def _dossier_context(data, days):
    a_id, b_id = data["match"]["team_a_id"], data["match"]["team_b_id"]
    pool = data["pool_forfeit"]

    def side(rows, map_rows, tid, roster_rows, changes):
        churn = rstats.roster_changes(changes or []) if roster_rows else None
        return {
            "win_rate": tstats.win_rate(rows, tid),
            "form": tstats.form(rows, tid, 5),
            "form_string": tstats.form_string(rows, tid, 8),
            "forfeit": tstats.forfeit_rate(rows, tid, pool_mean=pool),
            "no_show": tstats.no_show_risk(rows, tid, roster_changes_30d=churn,
                                           pool_mean=pool),
            "fatigue": tstats.fatigue(rows),
            "map_win": maps.map_win_rate(map_rows, tid),
            "round_pct": maps.round_win_pct(map_rows, tid),
            "round_diff": maps.avg_round_diff(map_rows, tid),
            "pool": maps.map_pool(map_rows, tid, min_maps=2),
            "roster": rstats.describe(roster_rows or [], changes or []),
            "played": len([r for r in rows if r.get("status") == "finished"]),
        }

    return {
        "m": data["match"],
        "a": data["team_a"], "b": data["team_b"],
        "sa": side(data["a_matches"], data["a_maps"], a_id,
                   data.get("a_roster"), data.get("a_roster_changes")),
        "sb": side(data["b_matches"], data["b_maps"], b_id,
                   data.get("b_roster"), data.get("b_roster_changes")),
        "h2h": h2h.record(data["all_matches"], a_id, b_id),
        "h2h_maps": h2h.map_record(data["all_maps"], a_id, b_id),
        "common": h2h.common_opponents(data["all_matches"], a_id, b_id),
        "tags": decisions.TAGS,
        "days": days,
    }


def _decisions_for(conn, match_id):
    return [dict(r) for r in conn.execute(
        "SELECT id, prob_team_a, market_prob_a, action, tags, result, "
        "created_at FROM decisions WHERE match_id = %s "
        "ORDER BY created_at DESC LIMIT 10", (match_id,)).fetchall()]


_LOG_SQL = """
SELECT d.*, ta.canonical_name AS team_a_name, tb.canonical_name AS team_b_name,
       m.status AS match_status, m.winner_team_id AS match_winner
FROM decisions d
LEFT JOIN teams ta ON ta.id = d.team_a_id
LEFT JOIN teams tb ON tb.id = d.team_b_id
LEFT JOIN matches m ON m.id = d.match_id
WHERE d.user_id = %(user)s ORDER BY d.created_at DESC LIMIT 200;
"""

_QUEUE_SQL = """
SELECT kalshi_event_ticker, candidates, status
FROM resolution_queue WHERE status = 'open'
ORDER BY kalshi_event_ticker LIMIT 100;
"""

_RUNS_SQL = """
SELECT source, status, started_at, rows_written, error
FROM collection_runs ORDER BY started_at DESC LIMIT 15;
"""
