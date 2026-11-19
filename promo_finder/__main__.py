"""CLI: python -m promo_finder run --input <zip|folder> [--mode llm|rules|baseline] ..."""
from __future__ import annotations

import argparse
import sys

from .export import segments_str
from .llm import LLMError
from .parse import ParseError, load_show
from .pipeline import run


def _progress(stage: str, done: int, total: int) -> None:
    if total:
        print(f"\r  {stage:<10} {done}/{total}", end="" if done < total else "\n", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="promo_finder", description="Find and rank promos in microdrama screenplays.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the promo finder on one show")
    r.add_argument("--input", required=True, help="a .zip or a folder of episode .txt files")
    r.add_argument("--mode", choices=["llm", "rules", "baseline"], default="llm")
    r.add_argument("--provider", help="provider preset from config.yaml (default: config 'provider')")
    r.add_argument("--no-stitch", action="store_true", help="skip cross-episode promos")
    r.add_argument("--max-episodes", type=int, help="only the first N episodes (quick demos)")
    r.add_argument("--out", help="output root folder (default: ./outputs)")
    r.add_argument("--title", help="override the show title")
    args = ap.parse_args(argv)

    try:
        show = load_show(args.input, title=args.title)
    except (ParseError, OSError) as e:
        print(f"Could not read the show: {e}", file=sys.stderr)
        return 2
    print(f"{show.title}: {len(show.episodes)} episodes, mode={args.mode}")
    try:
        res = run(show, args.mode, provider=args.provider, stitch=False if args.no_stitch else None,
                  max_episodes=args.max_episodes, out_root=args.out, progress=_progress)
    except LLMError as e:
        print(f"\nLLM error: {e}", file=sys.stderr)
        return 3

    m = res.meta
    print(f"\nValid promos: {m['counts']['valid']}  ranked: {m['counts']['ranked']}  rejected: {m['counts']['rejected']} "
          f"{m['counts']['rejection_reasons']}")
    if m.get("llm"):
        L = m["llm"]
        print(f"LLM calls: {L['calls']} (cache hits {L['cache_hits']}, repairs {L['repairs']}, failures {L['failures']}), "
              f"tokens in/out {L['prompt_tokens']}/{L['completion_tokens']}")
    for w in res.warnings:
        print(f"warning: {w}")
    print("\nPost first:")
    for c in [c for c in res.ranked if c.rank][:5]:
        print(f"  {c.rank}. {segments_str(c.segments)} ({c.duration:.0f}s, score {c.score:.0f}) "
              f"[{c.hook_type}] {c.hook_text[:80]}")
    print(f"\nOutputs: {res.out_dir}  ({m['wall_seconds']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
