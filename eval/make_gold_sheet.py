"""Make the human labelling kit: a readable script sheet for the gold episodes + a blank answer file.

    python eval/make_gold_sheet.py --input path/to/show.zip [--episodes 1 2 5 9 11 18 25 33 40 50]

Writes into data/gold/ (gitignored, because it contains script text):
    reading_sheet.md   dialogue / action / sound lines with timestamps, no camera noise, nothing from the tool
    gold.csv           3 empty rows per episode to fill in (never overwritten if it already exists)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from promo_finder.models import fmt_ts  # noqa: E402
from promo_finder.parse import load_show  # noqa: E402

DEFAULT_EPS = [1, 2, 5, 9, 11, 18, 25, 33, 40, 50]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--episodes", nargs="+", type=int, default=DEFAULT_EPS)
    ap.add_argument("--out", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--rows", type=int, default=3, help="blank rows per episode in gold.csv")
    a = ap.parse_args()

    show = load_show(a.input)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    lines = [f"# Reading sheet: {show.title}", "",
             "Read each episode and pick the 2-4 moments YOU would post as a 20-60 second promo.",
             "A good promo: a hook in the first 3 seconds, a cut on an unresolved moment, and it makes sense to a stranger.",
             "Write your picks in gold.csv (same folder). Times are approximate; camera shots are left out to keep this short.", ""]
    for n in a.episodes:
        ep = show.episode(n)
        if ep is None:
            print(f"episode {n} not in the show, skipped")
            continue
        lines += ["---", "", f"## Episode {n}  (length {fmt_ts(ep.duration)})", "", f"*{ep.logline}*", ""]
        for b in ep.beats:
            if b.kind == "CAMERA":
                continue
            if b.kind == "DIALOGUE":
                tags = f" ({', '.join(b.emotions)})" if b.emotions else ""
                lines.append(f"`{fmt_ts(b.start)}`  **{b.speaker}**{tags}: {b.text}  ")
            else:
                lines.append(f"`{fmt_ts(b.start)}`  _[{b.kind.lower()}] {b.text}_  ")
        lines.append("")
    (out / "reading_sheet.md").write_text("\n".join(lines), encoding="utf-8")

    gold = out / "gold.csv"
    if gold.exists():
        print(f"{gold} already exists, not overwritten")
    else:
        with open(gold, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "episode", "start", "end", "hook", "cliffhanger", "rating", "notes"])
            for n in a.episodes:
                for k in range(a.rows):
                    w.writerow([f"E{n:02d}-{'abcdefgh'[k]}", n, "", "", "", "", "", ""])
    print(f"Wrote {out / 'reading_sheet.md'} and {gold}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
