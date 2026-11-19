"""Composite scoring, diversity-aware ranking and the 'post first' picks (plan KTD7, R10)."""
from __future__ import annotations

from .models import Candidate, Scores


def position_factor(episode: int, factors: list[list[float]]) -> float:
    for last_ep, f in factors:
        if episode <= last_ep:
            return float(f)
    return float(factors[-1][1])


def merge_scores(c: Candidate) -> Scores:
    """Proposer (has show context) and cold-viewer judge are averaged; standalone comes from the judge only."""
    p = c.proposer_scores
    j = c.judge
    if not j:
        return Scores(hook=p.hook, cliffhanger=p.cliffhanger, standalone=p.standalone, appeal=p.appeal, spoiler=p.spoiler)
    return Scores(
        hook=(p.hook + j["hook"]) / 2, cliffhanger=(p.cliffhanger + j["cliffhanger"]) / 2,
        standalone=j["standalone"], appeal=(p.appeal + j["appeal"]) / 2, spoiler=bool(j.get("spoiler")),
    )


def composite(s: Scores, first_episode: int, rank_cfg: dict) -> float:
    w = rank_cfg["weights"]
    base = 100.0 * (w["hook"] * s.hook + w["cliffhanger"] * s.cliffhanger + w["standalone"] * s.standalone
                    + w["appeal"] * s.appeal) / 5.0
    score = base * position_factor(first_episode, rank_cfg["position_factors"])
    if s.spoiler:
        score -= rank_cfg["spoiler_penalty"]
    return round(score, 2)


def iou(a: Candidate, b: Candidate) -> float:
    inter = 0.0
    for sa in a.segments:
        for sb in b.segments:
            if sa.episode == sb.episode:
                inter += max(0.0, min(sa.end, sb.end) - max(sa.start, sb.start))
    union = a.duration + b.duration - inter
    return inter / union if union > 0 else 0.0


def rank(cands: list[Candidate], rank_cfg: dict, judge_cfg: dict) -> list[Candidate]:
    for c in cands:
        c.scores = merge_scores(c)
        c.score = composite(c.scores, c.episodes[0], rank_cfg)
        if c.source.startswith("stitch") and c.judge and c.scores.standalone < judge_cfg.get("min_standalone_stitched", 3):
            c.excluded_reason = "not_standalone"
    eligible = sorted((c for c in cands if not c.excluded_reason), key=lambda c: (-c.score, c.episodes[0], c.id))
    top: list[Candidate] = []
    per_ep: dict[int, int] = {}
    for c in eligible:
        if len(top) >= rank_cfg["top_n"]:
            break
        if any(iou(c, t) > rank_cfg["overlap_iou"] for t in top):
            continue
        if any(per_ep.get(e, 0) >= rank_cfg["max_per_episode_in_top"] for e in c.episodes):
            continue
        top.append(c)
        for e in c.episodes:
            per_ep[e] = per_ep.get(e, 0) + 1
    rest = [c for c in eligible if c not in top]
    ordered = top + rest
    for i, c in enumerate(ordered, 1):
        c.rank = i
    excluded = sorted((c for c in cands if c.excluded_reason), key=lambda c: -c.score)
    for c in excluded:
        c.rank = None
    return ordered + excluded


def post_first_reason(c: Candidate) -> str:
    bits = [f"{c.hook_type.replace('_', ' ')} hook in the first 3 s"]
    if c.episodes[0] <= 10:
        bits.append(f"from episode {c.episodes[0]}, so new viewers land near the start")
    if c.judge and c.judge.get("open_question"):
        bits.append(f"leaves a cold viewer asking: \"{c.judge['open_question']}\"")
    elif c.cliffhanger_text:
        bits.append("cuts on an unresolved beat")
    return "; ".join(bits) + "."
