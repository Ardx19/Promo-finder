"""The four LLM passes: show bible, per-episode proposals, cross-episode stitching, cold-viewer judge.

Every pass returns beat IDs or scores only; validate.py turns IDs into timestamps.
"""
from __future__ import annotations

import re
from collections import Counter

from .heuristics import APPEAL, hook_type_of
from .llm import LLMClient, LLMError
from .models import Candidate, Episode, Proposal, Scores, Show
from .render import beat_table, episode_table, excerpt
from .schemas import BibleOut, EpisodeOut, JudgeOut, JudgeResult, RepairOut, StitchOut


# ---- bible ------------------------------------------------------------------
def run_bible(llm: LLMClient, show: Show) -> BibleOut:
    eps = "\n".join(f"E{e.number:02d}: {e.logline} | {e.continuity}" for e in show.episodes)
    counts = Counter(b.speaker for e in show.episodes for b in e.dialogue if b.speaker)
    speakers = "\n".join(f"{s}: {n}" for s, n in counts.most_common(60))
    return llm.complete_json("bible", {"title": show.title, "episodes": eps, "speakers": speakers}, BibleOut)


def bible_text(bible: BibleOut | None) -> str:
    if bible is None or not bible.premise:
        return "(not available)"
    chars = "; ".join(f"{c.name} ({c.role}) aka {', '.join(c.aliases) or '-'}" for c in bible.characters[:12])
    return f"Premise: {bible.premise}\nCharacters: {chars}\nTropes: {', '.join(bible.tropes[:8])}"


# ---- episode pass ------------------------------------------------------------
def run_episode(llm: LLMClient, show: Show, ep: Episode, bible: BibleOut | None, max_promos: int
                ) -> tuple[list[Proposal], dict[str, Scores], dict[str, float]]:
    """Returns proposals, proposer scores keyed by proposal index, and LLM (hook, cliffhanger) tags by beat id."""
    out = llm.complete_json("episode", {
        "title": show.title, "episode": str(ep.number), "total": str(len(show.episodes)),
        "bible": bible_text(bible), "logline": ep.logline or "(none)", "beats": episode_table(ep),
        "max_promos": str(max_promos),
    }, EpisodeOut)
    proposals, scores = [], {}
    for i, p in enumerate(out.promos[:max_promos]):
        start = p.start_beat or p.hook_beat
        end = p.cliffhanger_beat
        proposals.append(Proposal(hook_beat=p.hook_beat, cliffhanger_beat=p.cliffhanger_beat,
                                  segments=[(start, end)], hook_type=p.hook_type, why=p.why, source="llm"))
        scores[str(i)] = Scores(hook=p.hook, cliffhanger=p.cliffhanger, appeal=p.appeal)
    tags = {t.id: (t.hook, t.cliffhanger) for t in out.beats}
    return proposals, scores, tags


REPAIRABLE = {"bad_duration", "hook_late", "resolved_ending", "unknown_beat", "bad_segments"}


def run_repair(llm: LLMClient, show: Show, ep: Episode, rejected: list, max_promos: int
               ) -> tuple[list[Proposal], dict[str, Scores]]:
    """Feed the validator's reasons back to the model once, so it can fix its own proposals."""
    lines = []
    for r in rejected:
        p = r.proposal
        start = p["segments"][0][0] if p.get("segments") else p.get("hook_beat")
        lines.append(f"- hook_beat {p.get('hook_beat')}, start_beat {start}, cliffhanger_beat {p.get('cliffhanger_beat')}"
                     f" ({p.get('hook_type', 'other')}): REJECTED, {r.reason}: {r.detail}")
    out = llm.complete_json("repair", {"episode": str(ep.number), "title": show.title,
                                       "failures": "\n".join(lines), "beats": episode_table(ep)}, RepairOut)
    proposals, scores = [], {}
    for i, p in enumerate(out.promos[:max_promos]):
        proposals.append(Proposal(hook_beat=p.hook_beat, cliffhanger_beat=p.cliffhanger_beat,
                                  segments=[(p.start_beat or p.hook_beat, p.cliffhanger_beat)],
                                  hook_type=p.hook_type, why=p.why, source="llm-repair"))
        scores[str(i)] = Scores(hook=p.hook, cliffhanger=p.cliffhanger, appeal=p.appeal)
    return proposals, scores


# ---- stitching ---------------------------------------------------------------
def stitch_pairs(show: Show, ending_cliff: dict[int, float], cfg: dict) -> list[tuple[Episode, Episode]]:
    phrases = [p.lower() for p in cfg.get("pickup_phrases", [])]
    pairs = []
    for a, b in zip(show.episodes, show.episodes[1:]):
        if b.number != a.number + 1:
            continue
        if ending_cliff.get(a.number, 0.0) < cfg.get("min_cliffhanger", 4):
            continue
        if not any(p in b.continuity.lower() for p in phrases):
            continue
        pairs.append((a, b))
    return pairs


def run_stitch(llm: LLMClient, show: Show, a: Episode, b: Episode, bible: BibleOut | None
               ) -> tuple[Proposal | None, Scores | None]:
    tail = [x for x in a.beats if x.start >= a.duration - 60.0]
    head = [x for x in b.beats if x.start <= 60.0]
    out = llm.complete_json("stitch", {
        "ep_a": str(a.number), "ep_b": str(b.number), "title": show.title, "bible": bible_text(bible),
        "beats_a": beat_table(tail), "beats_b": beat_table(head),
    }, StitchOut)
    p = out.promo
    if p is None:
        return None, None
    prop = Proposal(hook_beat=p.hook_beat, cliffhanger_beat=p.cliffhanger_beat,
                    segments=[(p.hook_beat, p.seg1_end_beat), (p.seg2_start_beat, p.cliffhanger_beat)],
                    hook_type=p.hook_type, why=p.why, source="stitch-llm")
    return prop, Scores(hook=p.hook, cliffhanger=p.cliffhanger, appeal=p.appeal)


def rules_stitch(show: Show, a: Episode, b: Episode, hooks: dict, cliffs: dict, promo_cfg: dict) -> Proposal | None:
    """No-AI stitch: strongest hook in the last 25 s of A, run to A's end, then B's opening up to its best cliffhanger."""
    tail = [x for x in a.beats if x.kind != "CAMERA" and x.start >= a.duration - 25.0]
    if not tail:
        return None
    h = max(tail, key=lambda x: hooks.get(x.id, (0, ""))[0])
    seg1 = a.duration - h.start
    budget = promo_cfg["max_duration_s"] - seg1 - 1.0
    head = [x for x in b.beats if x.kind != "CAMERA" and x.end <= budget and x.end >= promo_cfg["min_segment_s"]]
    if not head:
        return None
    c = max(head, key=lambda x: (cliffs.get(x.id, 0.0), x.end))
    first_b = next(x for x in b.beats if x.kind != "CAMERA")
    last_a = [x for x in a.beats if x.kind != "CAMERA"][-1]
    htype = hooks.get(h.id, (0, "other"))[1]
    return Proposal(hook_beat=h.id, cliffhanger_beat=c.id, segments=[(h.id, last_a.id), (first_b.id, c.id)],
                    hook_type=htype, why="Rules: joins the end of one episode to the escalation that opens the next.",
                    source="stitch-rules")


# ---- judge ------------------------------------------------------------------
def run_judge(llm: LLMClient, show: Show, cands: list[Candidate], batch_size: int) -> dict[str, JudgeResult]:
    results: dict[str, JudgeResult] = {}
    for i in range(0, len(cands), batch_size):
        batch = cands[i:i + batch_size]
        results.update(_judge_batch(llm, show, batch))
    missing = [c for c in cands if c.id not in results]
    for c in missing:  # one retry alone; otherwise caller keeps neutral scores
        try:
            results.update(_judge_batch(llm, show, [c]))
        except LLMError:
            pass
    return results


def _judge_batch(llm: LLMClient, show: Show, batch: list[Candidate]) -> dict[str, JudgeResult]:
    clips = []
    for c in batch:
        body = "\n".join(excerpt(show, c))
        clips.append(f"=== CLIP {c.id} ({c.duration:.0f}s) ===\n{body}")
    out = llm.complete_json("judge", {"count": str(len(batch)), "clips": "\n\n".join(clips)}, JudgeOut, role="judge")
    ids = {c.id for c in batch}
    return {r.id: r for r in out.results if r.id in ids}


def guess_hook_type(text: str) -> str:
    t, hits = hook_type_of(text)
    return t if hits else ("shocking_line" if re.search(r"!", text) else "other")


__all__ = ["run_bible", "bible_text", "run_episode", "run_repair", "REPAIRABLE", "stitch_pairs", "run_stitch", "rules_stitch", "run_judge",
           "guess_hook_type", "APPEAL"]
