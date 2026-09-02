"""Chart geometry. Layout maths fails silently, so it gets tested."""
from __future__ import annotations

import pytest

from edgedesk.stats.core import Stat
from edgedesk.web.charts import NEG, POS, diverging_bars, map_advantage, strip


def item(label, value, n=5):
    return {"label": label, "value": value, "n": n}


# ------------------------------------------------------- diverging bars


def test_positive_bars_start_at_the_midline_and_extend_right():
    c = diverging_bars([item("Mirage", 4.0)])
    bar = c["bars"][0]
    assert bar["x"] == pytest.approx(c["mid"])
    assert bar["fill"] == POS


def test_negative_bars_end_at_the_midline_and_extend_left():
    c = diverging_bars([item("Nuke", -4.0)])
    bar = c["bars"][0]
    assert bar["x"] + bar["w"] == pytest.approx(c["mid"])
    assert bar["fill"] == NEG


def test_no_bar_overflows_the_canvas():
    """The failure that makes a chart lie: a bar longer than its box."""
    c = diverging_bars([item("A", 30.0), item("B", -30.0), item("C", 0.1)])
    for bar in c["bars"]:
        assert bar["x"] >= 0
        assert bar["x"] + bar["w"] <= c["width"]


def test_bar_length_is_proportional_to_value():
    c = diverging_bars([item("A", 10.0), item("B", 5.0)])
    a, b = c["bars"]
    assert a["w"] == pytest.approx(b["w"] * 2, rel=0.02)


def test_a_near_zero_value_still_renders_a_visible_mark():
    """Rounding a tiny value to zero width would silently drop a row."""
    c = diverging_bars([item("A", 0.01), item("B", 10.0)])
    assert c["bars"][0]["w"] >= 2


def test_axis_maximum_never_crops_the_data():
    c = diverging_bars([item("A", 7.3)])
    assert c["max"] >= 7.3


def test_labels_sit_outside_the_bar_on_the_correct_side():
    c = diverging_bars([item("A", 5.0), item("B", -5.0)])
    pos, neg = c["bars"]
    assert pos["text_x"] > pos["x"] + pos["w"] and pos["anchor"] == "start"
    assert neg["text_x"] < neg["x"] and neg["anchor"] == "end"


def test_empty_input_gives_an_empty_chart_not_a_crash():
    c = diverging_bars([])
    assert c["bars"] == [] and c["height"] == 0


def test_zero_tick_sits_on_the_midline():
    c = diverging_bars([item("A", 3.0)])
    zero = [t for t in c["ticks"] if t["v"] == 0][0]
    assert zero["x"] == pytest.approx(c["mid"])


# ------------------------------------------------------------- strip


def test_strip_places_zero_at_the_centre():
    s = strip([0.0])
    assert s["dots"][0]["cx"] == pytest.approx(s["mid"])


def test_strip_dots_stay_inside_the_canvas():
    s = strip([-13, -6, 0, 6, 13])
    for d in s["dots"]:
        assert d["cx"] - d["r"] >= 0
        assert d["cx"] + d["r"] <= s["width"]


def test_strip_colours_by_sign():
    s = strip([5, -5])
    assert s["dots"][0]["fill"] == POS and s["dots"][1]["fill"] == NEG


def test_strip_markers_meet_the_minimum_size():
    """The mark spec requires >=8px markers."""
    assert strip([1])["dots"][0]["r"] * 2 >= 8


def test_strip_ignores_missing_values():
    s = strip([3, None, -3])
    assert s["n"] == 2


def test_empty_strip_is_safe():
    assert strip([])["dots"] == []


# ----------------------------------------------------- map advantage


def pool(name, diff, played):
    return {"map": name, "played": played,
            "win_rate": Stat(0.5, played), "round_win_pct": Stat(0.5, played),
            "round_diff": Stat(diff, played)}


def test_advantage_is_a_minus_b():
    rows = map_advantage([pool("Mirage", 3.0, 10)], [pool("Mirage", -1.0, 10)],
                         "A", "B")
    assert rows[0]["value"] == pytest.approx(4.0)


def test_maps_only_one_side_has_played_are_excluded():
    """A map the opponent has never played is an unknown, not an advantage.
    Rendering it would invent the most decision-relevant number on the page.
    """
    rows = map_advantage([pool("Mirage", 3.0, 10), pool("Nuke", 5.0, 9)],
                         [pool("Mirage", -1.0, 10)], "A", "B")
    assert [r["label"] for r in rows] == ["Mirage"]


def test_thin_samples_are_excluded_by_the_threshold():
    rows = map_advantage([pool("Mirage", 3.0, 1)], [pool("Mirage", -1.0, 10)],
                         "A", "B", min_maps=2)
    assert rows == []


def test_rows_are_ordered_by_absolute_advantage():
    rows = map_advantage(
        [pool("Mirage", 1.0, 5), pool("Nuke", 6.0, 5), pool("Dust2", -4.0, 5)],
        [pool("Mirage", 0.0, 5), pool("Nuke", 0.0, 5), pool("Dust2", 0.0, 5)],
        "A", "B")
    assert [r["label"] for r in rows] == ["Nuke", "Dust2", "Mirage"]


def test_detail_names_both_sides_with_their_sample_sizes():
    rows = map_advantage([pool("Mirage", 3.0, 12)], [pool("Mirage", -1.0, 7)],
                         "Voca", "Marsborne")
    assert "Voca +3.0 (12)" in rows[0]["detail"]
    assert "Marsborne -1.0 (7)" in rows[0]["detail"]
    assert rows[0]["n"] == 7          # the smaller, honest sample size


def test_unresolved_round_diff_is_skipped():
    rows = map_advantage([pool("Mirage", None, 5)], [pool("Mirage", 1.0, 5)],
                         "A", "B")
    assert rows == []


# ------------------------------------------------------------ heatmap


def test_heatmap_includes_maps_only_one_side_has_played():
    """The complement to the advantage chart, which drops them. In a Bo3
    veto, '26 games on Inferno versus 3' is decision-relevant even though
    it is not an advantage figure."""
    from edgedesk.web.charts import heat_cells
    h = heat_cells([pool("Mirage", 3.0, 10), pool("Nuke", 2.0, 8)],
                   [pool("Mirage", -1.0, 10)], "A", "B")
    maps = [r["map"] for r in h["rows"]]
    assert "Nuke" in maps
    nuke = next(r for r in h["rows"] if r["map"] == "Nuke")
    assert nuke["cells"][1]["value"] is None
    assert nuke["cells"][1]["label"] == "—"


def test_unplayed_cells_are_not_coloured():
    """An unknown must not read as a value of zero."""
    from edgedesk.web.charts import heat_cells
    h = heat_cells([pool("Mirage", 3.0, 10)], [], "A", "B")
    assert h["rows"][0]["cells"][1]["fill"] is None
    assert h["rows"][0]["cells"][1]["alpha"] == 0


def test_cell_intensity_is_capped_for_text_contrast():
    """Full-opacity fills measured 2.99:1 (blue) and 2.66:1 (red) against
    the ink the cell value is printed in. The cap keeps both above 4.5."""
    from edgedesk.web.charts import CELL_ALPHA_MAX, heat_cells
    h = heat_cells([pool("Mirage", 99.0, 10)], [pool("Mirage", -99.0, 10)],
                   "A", "B")
    for c in h["rows"][0]["cells"]:
        assert c["alpha"] <= CELL_ALPHA_MAX


def test_heatmap_orders_by_total_games_played():
    from edgedesk.web.charts import heat_cells
    h = heat_cells([pool("Mirage", 1.0, 2), pool("Nuke", 1.0, 20)],
                   [pool("Mirage", 1.0, 2), pool("Nuke", 1.0, 20)], "A", "B")
    assert [r["map"] for r in h["rows"]] == ["Nuke", "Mirage"]


def test_empty_pools_give_an_empty_heatmap():
    from edgedesk.web.charts import heat_cells
    assert heat_cells([], [], "A", "B")["rows"] == []


# ---------------------------------------------------------- sparkline


def _pts(values, start=None):
    from datetime import datetime, timedelta, timezone
    start = start or datetime(2026, 9, 1, tzinfo=timezone.utc)
    return [{"at": start + timedelta(minutes=15 * i), "p": v}
            for i, v in enumerate(values)]


def test_sparkline_maps_high_probability_to_the_top():
    from edgedesk.web.charts import sparkline
    s = sparkline(_pts([0.2, 0.8]))
    assert s["dots"][0]["cy"] > s["dots"][1]["cy"]


def test_sparkline_reports_the_move():
    """A flat line and one that moved twenty points are different stories."""
    from edgedesk.web.charts import sparkline
    assert sparkline(_pts([0.50, 0.70]))["move"] == pytest.approx(0.20)
    assert sparkline(_pts([0.70, 0.50]))["move"] == pytest.approx(-0.20)


def test_sparkline_stays_inside_its_canvas():
    from edgedesk.web.charts import sparkline
    s = sparkline(_pts([0.0, 1.0, 0.5, 0.99, 0.01]))
    for d in s["dots"]:
        assert 0 <= d["cx"] <= s["width"]
        assert 0 <= d["cy"] <= s["height"]


def test_sparkline_needs_two_points():
    from edgedesk.web.charts import sparkline
    assert sparkline(_pts([0.5]))["path"] == ""
    assert sparkline([])["n"] == 0


def test_sparkline_ignores_missing_probabilities():
    from edgedesk.web.charts import sparkline
    pts = _pts([0.5, 0.6, 0.7])
    pts[1]["p"] = None
    assert sparkline(pts)["n"] == 2


# ---------------------------------------------------------- hour bars


def test_hour_bars_mark_gaps_distinctly():
    """An absent hour and a zero-height bar look identical if both are
    drawn, and spotting when collection stopped is the entire purpose."""
    from edgedesk.web.charts import hour_bars
    h = hour_bars([{"hour": 1, "rows": 400}, {"hour": 2, "rows": 0},
                   {"hour": 3, "rows": 380}])
    assert [b["gap"] for b in h["bars"]] == [False, True, False]


def test_hour_bars_scale_to_the_busiest_hour():
    from edgedesk.web.charts import hour_bars
    h = hour_bars([{"hour": 1, "rows": 100}, {"hour": 2, "rows": 200}])
    assert h["bars"][1]["h"] > h["bars"][0]["h"]
    assert h["max"] == 200


def test_hour_bars_never_overflow():
    from edgedesk.web.charts import hour_bars
    h = hour_bars([{"hour": i, "rows": i * 37} for i in range(48)])
    for b in h["bars"]:
        assert b["x"] >= 0 and b["x"] + b["w"] <= h["width"] + 1
        assert b["y"] >= 0 and b["y"] + b["h"] <= h["height"]


def test_empty_history_is_safe():
    from edgedesk.web.charts import hour_bars
    assert hour_bars([])["bars"] == []


# -------------------------------------------------------- calibration


def bucket(lo, hi, n, forecast, actual):
    """Mirrors scoring.calibration's real shape: an empty bucket carries no
    mean, no actual and NO 'gap' key at all."""
    if not n:
        return {"lo": lo, "hi": hi, "n": 0, "mean_forecast": None,
                "actual": None}
    return {"lo": lo, "hi": hi, "n": n, "mean_forecast": forecast,
            "actual": actual, "gap": round(actual - forecast, 4)}


def test_calibration_pairs_claim_and_observation():
    from edgedesk.web.charts import calibration_rows
    c = calibration_rows([bucket(0.6, 0.8, 20, 0.70, 0.60)])
    row = c["rows"][0]
    assert row["fx"] > row["ax"]            # claimed more than happened
    assert row["x1"] == row["ax"] and row["x2"] == row["fx"]


def test_well_calibrated_rows_are_flagged():
    from edgedesk.web.charts import calibration_rows
    rows = calibration_rows([bucket(0.6, 0.8, 20, 0.70, 0.68),
                             bucket(0.8, 1.0, 20, 0.90, 0.60)])["rows"]
    assert rows[0]["good"] is True and rows[1]["good"] is False


def test_empty_buckets_are_dropped_from_the_chart():
    """An empty bucket has no mean and no 'gap' key; the chart must skip it
    before touching either."""
    from edgedesk.web.charts import calibration_rows
    out = calibration_rows([bucket(0.0, 0.2, 0, None, None),
                            bucket(0.6, 0.8, 12, 0.70, 0.66)])
    assert [r["label"] for r in out["rows"]] == ["60%-80%"]
