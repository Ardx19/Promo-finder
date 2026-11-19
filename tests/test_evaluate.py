import csv
import json

import pytest

from promo_finder.evaluation import Promo, blind_export, blind_score, evaluate, load_gold, seg_iou, spearman
from promo_finder.models import Segment


def P(pid, ep, a, b, score=50.0):
    return Promo(id=pid, segments=[Segment(episode=ep, start=a, end=b)], score=score)


def test_identical_is_full_hit():
    g = [P("g", 1, 10, 40)]
    r = evaluate(g, [P("t", 1, 10, 40)])
    assert r["hit_rate"] == 1.0 and r["mean_iou_of_hits"] == pytest.approx(1.0)


def test_half_shift_is_a_miss():
    a, b = P("g", 1, 0, 30), P("t", 1, 15, 45)
    assert seg_iou(a.segments, b.segments) == pytest.approx(15 / 45)
    assert evaluate([a], [b])["hit_rate"] == 0.0


def test_episode_with_no_tool_promos_counts_as_miss():
    r = evaluate([P("g1", 1, 0, 30), P("g2", 5, 0, 30)], [P("t", 1, 0, 30)])
    assert r["hit_rate"] == 0.5
    assert [row["gold_hit"] for row in r["per_episode"]] == [1, 0]


def test_only_top_k_per_episode_count():
    gold = [P("g", 1, 100, 130)]
    tools = [P(f"t{i}", 1, i * 10, i * 10 + 25, score=90 - i) for i in range(4)] + [P("late", 1, 100, 130, score=10)]
    assert evaluate(gold, tools, k=4)["hit_rate"] == 0.0
    assert evaluate(gold, tools, k=5)["hit_rate"] == 1.0


def test_load_gold_skips_comments(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_text("id,segments,hook,cliffhanger,rating,notes\n# a comment,,,,,\n"
                 "g1,E01 00:10.00-00:40.00,h,c,3,\ng2,E09 01:40.00-01:55.50; E10 00:00.00-00:20.00,h,c,2,\n",
                 encoding="utf-8")
    gold = load_gold(p)
    assert [g.id for g in gold] == ["g1", "g2"] and gold[1].episodes == [9, 10]


def test_load_gold_simple_columns_and_blank_rows(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_text("id,episode,start,end,hook,cliffhanger,rating,notes\n"
                 "E01-a,1,0:11,0:45.5,h,c,3,\n"
                 "E01-b,1,,,,,,\n"            # left blank: skipped
                 "E05-a,5,83,110,h,c,2,\n", encoding="utf-8")
    gold = load_gold(p)
    assert [(g.id, g.segments[0].start, g.segments[0].end) for g in gold] == [("E01-a", 11.0, 45.5), ("E05-a", 83.0, 110.0)]


def test_load_gold_rejects_end_before_start(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_text("id,episode,start,end,rating\nE01-a,1,0:40,0:20,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="E01-a"):
        load_gold(p)


def test_blind_round_trip(tmp_path):
    promos = [{"id": f"p{i}", "rank": i + 1, "score": 90 - i, "scores": {"standalone": 3}, "segments_str": "E01 00:00.00-00:30.00",
               "duration": 30, "excerpt": ["0.0s line"]} for i in range(5)]
    run = tmp_path / "run"
    run.mkdir()
    (run / "promos.json").write_text(json.dumps({"promos": promos}), encoding="utf-8")
    blind_export(run, tmp_path / "b.csv", tmp_path / "k.json", n=5)
    key = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader(open(tmp_path / "b.csv", encoding="utf-8")))
    assert "score" not in rows[0]
    for r in rows:  # the reviewer agrees perfectly with the tool's order
        r["rating_1_to_3"] = "3" if key[r["blind_id"]]["rank"] <= 2 else ("2" if key[r["blind_id"]]["rank"] <= 4 else "1")
    with open(tmp_path / "b.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    s = blind_score(tmp_path / "b.csv", tmp_path / "k.json")
    assert s["rated"] == 5 and s["share_post_or_maybe"] == pytest.approx(0.8)
    assert s["spearman_rating_vs_tool_score"] > 0.8


def test_spearman_basic():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
