"""Render beats as compact text for prompts and human-readable excerpts."""
from __future__ import annotations

from .models import Beat, Candidate, Episode, Show, fmt_ts


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def beat_row(b: Beat) -> str:
    who = b.kind
    if b.kind == "DIALOGUE":
        who = f"{b.speaker} ({', '.join(b.emotions)})" if b.emotions else str(b.speaker)
    limit = {"CAMERA": 90, "ACTION": 170, "SFX": 110}.get(b.kind, 260)
    return f"{b.id} | {fmt_ts(b.start)}-{fmt_ts(b.end)} | {who} | {_short(b.text, limit)}"


def beat_table(beats: list[Beat]) -> str:
    return "\n".join(beat_row(b) for b in beats)


def episode_table(ep: Episode) -> str:
    return beat_table(ep.beats)


def excerpt(show: Show, cand: Candidate, include_camera: bool = True) -> list[str]:
    """What the promo contains, as lines with seconds from the promo start. Used by the judge and the viewer."""
    lines, offset = [], 0.0
    for i, seg in enumerate(cand.segments):
        ep = show.episode(seg.episode)
        if i:
            lines.append("[CUT]")
        beats = [b for b in ep.beats if b.end > seg.start + 0.01 and b.start < seg.end - 0.01]
        if not include_camera:
            beats = [b for b in beats if b.kind != "CAMERA"]
        for b in sorted(beats, key=lambda b: (b.start, b.kind != "CAMERA")):
            t = offset + max(0.0, b.start - seg.start)
            if b.kind == "DIALOGUE":
                tags = f" ({', '.join(b.emotions)})" if b.emotions else ""
                lines.append(f"{t:5.1f}s  {b.speaker}{tags}: {b.text}")
            elif b.kind == "CAMERA":
                lines.append(f"{t:5.1f}s  [shot] {_short(b.text, 120)}")
            else:
                lines.append(f"{t:5.1f}s  [{b.kind.lower()}] {b.text}")
        offset += seg.duration
    return lines
