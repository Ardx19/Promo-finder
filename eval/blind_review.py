"""Blind review of the top promos: rate them without seeing the tool's scores, then compare.

    python eval/blind_review.py export --run outputs/<show>/llm-...   (writes blind_review.csv + blind_key.json)
    # open blind_review.csv, fill rating_1_to_3 (3 = would post, 2 = maybe, 1 = no)
    python eval/blind_review.py score --rated blind_review.csv --key blind_key.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_finder.evaluation import blind_export, blind_score  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--run", required=True)
    e.add_argument("--n", type=int, default=20)
    e.add_argument("--csv", default="outputs/blind_review.csv")
    e.add_argument("--key", default="outputs/blind_key.json")
    s = sub.add_parser("score")
    s.add_argument("--rated", default="outputs/blind_review.csv")
    s.add_argument("--key", default="outputs/blind_key.json")
    a = ap.parse_args()
    if a.cmd == "export":
        n = blind_export(a.run, a.csv, a.key, n=a.n)
        print(f"Wrote {n} promos to {a.csv} (scores hidden). Key: {a.key}")
    else:
        print(json.dumps(blind_score(a.rated, a.key), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
