"""Score runs against hand-picked gold promos, and run a blind human review of the top picks (plan U12)."""
from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

from .export import parse_segments_str
from .models import Segment


@dataclass
class Promo:
    id: str
    segments: list[Segment]
    score: float = 0.0
    rank: int | None = None

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode for s in self.segments})

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)


def seg_iou(a: list[Segment], b: list[Segment]) -> float:
    inter = sum(max(0.0, min(x.end, y.end) - max(x.start, y.start)) for x in a for y in b if x.episode == y.episode)
    union = sum(s.duration for s in a) + sum(s.duration for s in b) - inter
    return inter / union if union > 0 else 0.0


def _loose_ts(text: str) -> float:
    """Accept 0:11 / 00:11.0 / 1:23.5 / 11 / 11.5 (seconds)."""
    text = text.strip().replace(",", ".")
    if ":" in text:
        m, s = text.split(":", 1)
        return int(m) * 60 + float(s)
    return float(text)


def load_gold(path: str | Path) -> list[Promo]:
    """Gold rows are either episode,start,end (simple) or a `segments` string (for multi-episode promos).
    Rows whose id starts with '#' or that have no times filled in are skipped."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            rid = (row.get("id") or "").strip()
            if rid.startswith("#"):
                continue
            seg_text = (row.get("segments") or "").strip()
            if seg_text:
                segs = parse_segments_str(seg_text)
            elif (row.get("start") or "").strip() and (row.get("end") or "").strip() and (row.get("episode") or "").strip():
                try:
                    start, end = _loose_ts(row["start"]), _loose_ts(row["end"])
                except ValueError as e:
                    raise ValueError(f"row {rid or i}: can't read start/end ({row['start']!r}, {row['end']!r})") from e
                if end <= start:
                    raise ValueError(f"row {rid or i}: end {row['end']} is not after start {row['start']}")
                segs = [Segment(episode=int(row["episode"]), start=start, end=end)]
            else:
                continue
            out.append(Promo(id=rid or f"gold-{i}", segments=segs, score=float(row.get("rating") or 0)))
    return out


def load_run(run_dir: str | Path) -> tuple[str, list[Promo]]:
    data = json.loads((Path(run_dir) / "promos.json").read_text(encoding="utf-8"))
    meta_path = Path(run_dir) / "run_meta.json"
    mode = json.loads(meta_path.read_text(encoding="utf-8"))["mode"] if meta_path.exists() else Path(run_dir).name
    promos = [Promo(id=p["id"], segments=[Segment(**s) for s in p["segments"]], score=p["score"], rank=p.get("rank"))
              for p in data["promos"] if p.get("rank") is not None]
    return mode, promos


def evaluate(gold: list[Promo], promos: list[Promo], k: int = 4, hit_iou: float = 0.5) -> dict:
    """Per gold episode, compare the tool's top-k promos that touch that episode with the gold promos there."""
    gold_eps = sorted({g.episodes[0] for g in gold})
    rows, hits, matched_ious, tool_total, tool_good = [], 0, [], 0, 0
    for ep in gold_eps:
        g_ep = [g for g in gold if g.episodes[0] == ep]
        t_ep = sorted([p for p in promos if ep in p.episodes], key=lambda p: -p.score)[:k]
        ep_hits = 0
        for g in g_ep:
            best = max((seg_iou(g.segments, t.segments) for t in t_ep), default=0.0)
            if best >= hit_iou:
                ep_hits += 1
                matched_ious.append(best)
        tool_total += len(t_ep)
        good_tools = sum(1 for t in t_ep if max((seg_iou(g.segments, t.segments) for g in g_ep), default=0) >= hit_iou)
        tool_good += good_tools
        hits += ep_hits
        rows.append({"episode": ep, "gold": len(g_ep), "tool_topk": len(t_ep), "gold_hit": ep_hits, "tool_match": good_tools})
    return {
        "gold_promos": len(gold), "gold_episodes": len(gold_eps), "k": k, "hit_iou": hit_iou,
        "hit_rate": hits / len(gold) if gold else 0.0,
        "precision_at_k": tool_good / tool_total if tool_total else 0.0,
        "mean_iou_of_hits": sum(matched_ious) / len(matched_ious) if matched_ious else 0.0,
        "per_episode": rows,
    }


def report_md(results: dict[str, dict]) -> str:
    lines = ["| mode | gold promos | hit rate (IoU≥0.5 in top-4) | precision@4 | mean IoU of hits |", "|---|---|---|---|---|"]
    for mode, r in results.items():
        lines.append(f"| {mode} | {r['gold_promos']} | {r['hit_rate']:.0%} | {r['precision_at_k']:.0%} | {r['mean_iou_of_hits']:.2f} |")
    lines += ["", "Per episode (gold hits / gold promos):", "", "| episode | " + " | ".join(results) + " |",
              "|---|" + "---|" * len(results)]
    eps = [r["episode"] for r in next(iter(results.values()))["per_episode"]] if results else []
    for i, ep in enumerate(eps):
        cells = [f"{r['per_episode'][i]['gold_hit']}/{r['per_episode'][i]['gold']}" for r in results.values()]
        lines.append(f"| {ep} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---- blind review -------------------------------------------------------------
def blind_export(run_dir: str | Path, out_csv: str | Path, key_json: str | Path, n: int = 20, seed: int = 7) -> int:
    data = json.loads((Path(run_dir) / "promos.json").read_text(encoding="utf-8"))
    top = [p for p in data["promos"] if p.get("rank") is not None][:n]
    order = list(range(len(top)))
    random.Random(seed).shuffle(order)
    key = {}
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["blind_id", "segments", "duration_s", "script", "rating_1_to_3", "notes"])
        w.writeheader()
        for j, i in enumerate(order, 1):
            p = top[i]
            bid = f"P{j:02d}"
            key[bid] = {"id": p["id"], "score": p["score"], "rank": p["rank"],
                        "standalone": p["scores"]["standalone"], "judge": bool(p.get("judge"))}
            w.writerow({"blind_id": bid, "segments": p["segments_str"], "duration_s": p["duration"],
                        "script": "\n".join(p["excerpt"]), "rating_1_to_3": "", "notes": ""})
    Path(key_json).write_text(json.dumps(key, indent=1), encoding="utf-8")
    return len(top)


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else None


def blind_score(rated_csv: str | Path, key_json: str | Path) -> dict:
    key = json.loads(Path(key_json).read_text(encoding="utf-8"))
    ratings, scores = [], []
    with open(rated_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            r = (row.get("rating_1_to_3") or "").strip()
            if not r or row["blind_id"] not in key:
                continue
            ratings.append(float(r))
            scores.append(float(key[row["blind_id"]]["score"]))
    n = len(ratings)
    return {
        "rated": n,
        "share_post_or_maybe": sum(1 for r in ratings if r >= 2) / n if n else 0.0,
        "share_would_post": sum(1 for r in ratings if r >= 3) / n if n else 0.0,
        "spearman_rating_vs_tool_score": spearman(ratings, scores),
    }
