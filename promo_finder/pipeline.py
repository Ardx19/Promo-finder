"""Orchestrates a run: parse -> propose (LLM or rules) -> validate -> stitch -> judge -> rank -> export."""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .config import REPO_ROOT, load_config, provider_settings
from .export import write_outputs
from .heuristics import baseline_proposal, propose_rules, rules_scores, score_episode
from .llm import LLMClient, LLMError, QuotaError
from .models import Candidate, Rejection, Scores, Show
from .passes import REPAIRABLE, rules_stitch, run_bible, run_episode, run_judge, run_repair, run_stitch, stitch_pairs
from .rank import rank
from .schemas import BibleOut
from .validate import Validator

Progress = Callable[[str, int, int], None]


@dataclass
class RunResult:
    out_dir: Path | None
    ranked: list[Candidate]
    rejected: list[Rejection]
    meta: dict
    bible: BibleOut | None = None
    warnings: list[str] = field(default_factory=list)


def _noop(stage: str, done: int, total: int) -> None:
    pass


def _cut_key(c: Candidate) -> tuple:
    return tuple((s.episode, round(s.start, 2), round(s.end, 2)) for s in c.segments)


def run(show: Show, mode: str = "llm", *, cfg: dict | None = None, provider: str | None = None,
        api_key: str | None = None, stitch: bool | None = None, max_episodes: int | None = None,
        out_root: str | Path | None = None, write: bool = True, llm_client: LLMClient | None = None,
        progress: Progress = _noop) -> RunResult:
    t0 = time.monotonic()
    cfg = cfg or load_config()
    P, R, J, S = cfg["promo"], cfg["rank"], cfg["judge"], cfg["stitch"]
    if max_episodes:
        show = Show(slug=show.slug, title=show.title, episodes=show.episodes[:max_episodes])
    do_stitch = S.get("enabled", True) if stitch is None else stitch
    warnings: list[str] = []

    # heuristic scores are always computed: rules mode uses them, LLM mode falls back to them
    heur = {e.number: score_episode(e) for e in show.episodes}
    cliff_scores = {bid: s for _, (_, cl) in heur.items() for bid, s in cl.items()}
    hook_scores = {bid: hs for _, (hk, _) in heur.items() for bid, (hs, _) in hk.items()}

    llm = None
    settings = None
    if mode == "llm":
        settings = provider_settings(cfg, provider, api_key)
        llm = llm_client or LLMClient(settings, cfg.get("llm", {}), cache_dir=REPO_ROOT / ".cache" / "llm")
        _ = llm.client  # fail fast with a clear message if there is no key

    validator = Validator(show, P, cliff_scores=cliff_scores, hook_scores=hook_scores)
    candidates: list[Candidate] = []
    rejected: list[Rejection] = []
    bible: BibleOut | None = None
    quota_dead = False
    fallback_eps: list[int] = []
    repaired = 0

    if mode == "baseline":
        for i, ep in enumerate(show.episodes, 1):
            progress("baseline", i, len(show.episodes))
            p = baseline_proposal(ep, P)
            if p is None:
                rejected.append(Rejection(source="baseline", episodes=[ep.number], reason="bad_duration",
                                          detail="episode shorter than the minimum promo length", proposal={}))
                continue
            good, bad = validator.validate([p], f"base-E{ep.number:02d}")
            if not good:  # the baseline is a naive cut: keep it even when the hook rule fails
                c = validator.tail(ep, P["tail_baseline_s"], f"base-E{ep.number:02d}-1")
                good = [c] if c else []
                rejected += [] if c else bad
            hk, cl = heur[ep.number]
            for c in good:
                c.proposer_scores = rules_scores(p, hk, cl)
            candidates += good
    else:
        if mode == "llm":
            progress("bible", 0, 1)
            try:
                bible = run_bible(llm, show)
            except LLMError as e:
                warnings.append(f"show bible failed ({e}); continuing without it")
            progress("bible", 1, 1)

        for i, ep in enumerate(show.episodes, 1):
            progress("episodes", i - 1, len(show.episodes))
            hk, cl = heur[ep.number]
            proposals, pscores, used_fallback = None, {}, False
            if mode == "llm" and not quota_dead:
                try:
                    proposals, pscores, tags = run_episode(llm, show, ep, bible, P["proposals_per_episode"])
                    for bid, (hs, cs) in tags.items():
                        if ep.beat(bid) is not None:  # ignore tags on beats that don't exist
                            hook_scores[bid] = max(hook_scores.get(bid, 0.0), hs)
                            cliff_scores[bid] = max(cliff_scores.get(bid, 0.0), cs)
                except QuotaError as e:
                    quota_dead = True
                    warnings.append(f"quota exhausted at episode {ep.number}: {e}. Remaining episodes use rules mode.")
                except LLMError as e:
                    warnings.append(f"episode {ep.number}: LLM failed ({e}); used rules mode for this episode")
            if proposals is None:
                proposals = propose_rules(ep, P, k=P["proposals_per_episode"])
                used_fallback = mode == "llm"
                if used_fallback:
                    fallback_eps.append(ep.number)
            good, bad = validator.validate(proposals, f"{'llm' if mode == 'llm' and not used_fallback else 'rules'}-E{ep.number:02d}")
            for c in good:
                idx = int(c.id.rsplit("-", 1)[1]) - 1
                prop = proposals[idx]
                c.proposer_scores = pscores.get(str(idx)) or rules_scores(prop, hk, cl)
                c.fallback = used_fallback
            candidates += good
            rejected += bad

            # one repair round: feed the validator's reasons back to the model
            fixable = [r for r in bad if r.reason in REPAIRABLE]
            if mode == "llm" and not used_fallback and not quota_dead and fixable and P.get("repair", True):
                try:
                    rprops, rscores = run_repair(llm, show, ep, fixable, P["proposals_per_episode"])
                except QuotaError as e:
                    quota_dead = True
                    warnings.append(f"quota exhausted during repair of episode {ep.number}: {e}")
                    rprops = []
                except LLMError as e:
                    warnings.append(f"episode {ep.number}: repair call failed ({e})")
                    rprops = []
                if rprops:
                    rgood, rbad = validator.validate(rprops, f"repair-E{ep.number:02d}")
                    have = {_cut_key(c) for c in good}
                    for c in rgood:
                        if _cut_key(c) in have:
                            continue
                        idx = int(c.id.rsplit("-", 1)[1]) - 1
                        c.proposer_scores = rscores.get(str(idx)) or rules_scores(rprops[idx], hk, cl)
                        candidates.append(c)
                        repaired += 1
                    rejected += rbad
        progress("episodes", len(show.episodes), len(show.episodes))

        if do_stitch:
            ending = {}
            for ep in show.episodes:
                tail_ids = [b.id for b in ep.beats if b.kind != "CAMERA" and ep.duration - b.end <= 12.0]
                ending[ep.number] = max((cliff_scores.get(b, 0.0) for b in tail_ids), default=0.0)
            pairs = stitch_pairs(show, ending, S)
            for i, (a, b) in enumerate(pairs, 1):
                progress("stitching", i - 1, len(pairs))
                prop, sc = None, None
                if mode == "llm" and not quota_dead:
                    try:
                        prop, sc = run_stitch(llm, show, a, b, bible)
                    except QuotaError as e:
                        quota_dead = True
                        warnings.append(f"quota exhausted during stitching: {e}")
                    except LLMError as e:
                        warnings.append(f"stitch E{a.number}-E{b.number}: LLM failed ({e})")
                if mode == "rules":
                    prop = rules_stitch(show, a, b, heur[a.number][0], heur[b.number][1], P)
                if prop is None:
                    continue
                good, bad = validator.validate([prop], f"stitch-E{a.number:02d}E{b.number:02d}")
                for c in good:
                    c.proposer_scores = sc or rules_scores(prop, heur[a.number][0], heur[b.number][1])
                candidates += good
                rejected += bad
            progress("stitching", len(pairs), len(pairs))

        if mode == "llm" and candidates and not quota_dead:
            progress("judging", 0, len(candidates))
            try:
                results = run_judge(llm, show, candidates, J["batch_size"])
                for c in candidates:
                    r = results.get(c.id)
                    if r:
                        c.judge = r.model_dump()
                missing = sum(1 for c in candidates if not c.judge)
                if missing:
                    warnings.append(f"judge returned no result for {missing} promos; they keep proposer scores")
            except QuotaError as e:
                warnings.append(f"quota exhausted during judging: {e}. Ranking uses proposer scores only.")
            except LLMError as e:
                warnings.append(f"judge failed ({e}); ranking uses proposer scores only")
            progress("judging", len(candidates), len(candidates))

    ranked = rank(candidates, R, J)
    meta = {
        "tool_version": __version__, "mode": mode, "show": show.title, "episodes": len(show.episodes),
        "created": datetime.now().isoformat(timespec="seconds"),
        "provider": settings.name if settings else None, "model": settings.model if settings else None,
        "judge_model": (settings.judge_model or settings.model) if settings else None,
        "prompt_versions": dict(llm.prompt_versions) if llm else {},
        "llm": llm.meter.as_dict() if llm else None,
        "stitch": do_stitch, "fallback_episodes": fallback_eps, "repaired_promos": repaired,
        "counts": {
            "valid": len(candidates), "ranked": sum(1 for c in ranked if c.rank is not None),
            "excluded": sum(1 for c in ranked if c.excluded_reason), "rejected": len(rejected),
            "by_source": dict(Counter(c.source for c in candidates)),
            "rejection_reasons": dict(Counter(r.reason for r in rejected)),
            "episodes_without_promo": [e.number for e in show.episodes
                                       if not any(e.number in c.episodes for c in candidates)],
        },
        "warnings": warnings,
        "wall_seconds": round(time.monotonic() - t0, 1),
        "config": {"promo": P, "rank": R},
    }
    out_dir = None
    if write:
        root = Path(out_root) if out_root else REPO_ROOT / "outputs"
        out_dir = root / show.slug / f"{mode}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        write_outputs(out_dir, show, ranked, rejected, meta, R.get("post_first", 5))
    return RunResult(out_dir=out_dir, ranked=ranked, rejected=rejected, meta=meta, bible=bible, warnings=warnings)
