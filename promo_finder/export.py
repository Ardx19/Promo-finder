"""Write run outputs: promos.json, promos.csv, top_picks.md, rejected.csv, run_meta.json."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .models import Candidate, Rejection, Segment, Show, fmt_ts
from .rank import post_first_reason
from .render import excerpt


def segments_str(segs: list[Segment]) -> str:
    return "; ".join(f"E{s.episode:02d} {fmt_ts(s.start)}-{fmt_ts(s.end)}" for s in segs)


def parse_segments_str(text: str) -> list[Segment]:
    out = []
    for part in text.split(";"):
        m = re.match(r"\s*E(\d+)\s+(\d+):(\d+(?:\.\d+)?)-(\d+):(\d+(?:\.\d+)?)\s*$", part)
        if not m:
            raise ValueError(f"bad segment string: {part!r}")
        ep, m1, s1, m2, s2 = m.groups()
        out.append(Segment(episode=int(ep), start=int(m1) * 60 + float(s1), end=int(m2) * 60 + float(s2)))
    return out


CSV_COLS = ["rank", "id", "score", "episodes", "segments", "duration_s", "hook_type", "hook_text", "cliffhanger_text",
            "why", "hook", "cliffhanger", "standalone", "appeal", "spoiler", "judge_summary", "open_question",
            "confusing_refs", "source", "fallback", "excluded_reason"]


def candidate_row(c: Candidate) -> dict:
    j = c.judge or {}
    return {
        "rank": c.rank if c.rank is not None else "", "id": c.id, "score": c.score,
        "episodes": " ".join(str(e) for e in c.episodes), "segments": segments_str(c.segments),
        "duration_s": round(c.duration, 2), "hook_type": c.hook_type, "hook_text": c.hook_text,
        "cliffhanger_text": c.cliffhanger_text, "why": c.why,
        "hook": round(c.scores.hook, 2), "cliffhanger": round(c.scores.cliffhanger, 2),
        "standalone": round(c.scores.standalone, 2), "appeal": round(c.scores.appeal, 2), "spoiler": c.scores.spoiler,
        "judge_summary": j.get("summary", ""), "open_question": j.get("open_question", ""),
        "confusing_refs": "; ".join(j.get("confusing_refs", []) or []), "source": c.source,
        "fallback": c.fallback, "excluded_reason": c.excluded_reason or "",
    }


def write_outputs(out_dir: Path, show: Show, ranked: list[Candidate], rejected: list[Rejection], meta: dict,
                  post_first: int = 5) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    promos = []
    for c in ranked:
        d = c.model_dump()
        d["duration"] = round(c.duration, 2)
        d["segments_str"] = segments_str(c.segments)
        d["excerpt"] = excerpt(show, c)
        promos.append(d)
    (out_dir / "promos.json").write_text(json.dumps({"show": show.title, "slug": show.slug, "promos": promos},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out_dir / "promos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for c in ranked:
            w.writerow(candidate_row(c))
    with open(out_dir / "rejected.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source", "episodes", "reason", "detail", "proposal"])
        w.writeheader()
        for r in rejected:
            w.writerow({"source": r.source, "episodes": " ".join(map(str, r.episodes)), "reason": r.reason,
                        "detail": r.detail, "proposal": json.dumps(r.proposal, ensure_ascii=False)})
    (out_dir / "top_picks.md").write_text(top_picks_md(show, ranked, meta, post_first), encoding="utf-8")
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return out_dir


def top_picks_md(show: Show, ranked: list[Candidate], meta: dict, n: int) -> str:
    lines = [f"# Promo picks: {show.title}", "",
             f"Mode: `{meta.get('mode')}` · model: `{meta.get('model') or 'none'}` · "
             f"{meta.get('counts', {}).get('valid', 0)} valid promos from {len(show.episodes)} episodes", "",
             f"## Post first (top {n})", ""]
    ranked_only = [c for c in ranked if c.rank is not None]
    for c in ranked_only[:n]:
        lines += [f"### {c.rank}. {segments_str(c.segments)} ({c.duration:.0f}s) — score {c.score:.0f}",
                  f"- **Hook ({c.hook_type.replace('_', ' ')}):** {c.hook_text}",
                  f"- **Cliffhanger:** {c.cliffhanger_text}",
                  f"- **Why it works:** {c.why or '-'}",
                  f"- **Why post it first:** {post_first_reason(c)}"]
        if c.judge and c.judge.get("summary"):
            lines.append(f"- **Cold viewer read it as:** {c.judge['summary']}")
        lines.append("")
    if len(ranked_only) > n:
        lines += ["## Next up", ""]
        for c in ranked_only[n:n + 10]:
            lines.append(f"{c.rank}. {segments_str(c.segments)} ({c.duration:.0f}s, score {c.score:.0f}) — "
                         f"{c.hook_type.replace('_', ' ')}: {c.hook_text[:90]}")
        lines.append("")
    return "\n".join(lines)
