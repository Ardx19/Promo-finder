"""Compare runs against your hand-picked gold promos.

    python eval/evaluate.py --runs outputs/<show>/llm-* outputs/<show>/rules-* outputs/<show>/baseline-*
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_finder.evaluation import evaluate, load_gold, load_run, report_md  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold/gold.csv")
    ap.add_argument("--runs", nargs="+", required=True, help="run folders (each has promos.json)")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--out", help="write the markdown report here too")
    ap.add_argument("--episodes", nargs="+", type=int, help="only score gold picks from these episodes (e.g. a held-out split)")
    a = ap.parse_args()
    gold = load_gold(a.gold)
    if a.episodes:
        gold = [g for g in gold if g.episodes[0] in set(a.episodes)]
    if not gold:
        print("No gold promos found in", a.gold)
        return 1
    results = {}
    for run_dir in a.runs:
        mode, promos = load_run(run_dir)
        label = mode if mode not in results else f"{mode} ({Path(run_dir).name})"
        results[label] = evaluate(gold, promos, k=a.k, hit_iou=a.iou)
    md = report_md(results)
    print(md)
    if a.out:
        Path(a.out).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
