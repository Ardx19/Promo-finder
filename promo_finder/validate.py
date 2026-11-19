"""Turn beat-ID proposals into validated promo candidates. Pure code: the only place timestamps are made.

Rules (see plan R3-R7):
  - every ID must exist; timestamps come only from beats/shots in the script
  - 1-3 chronological segments, each >= min_segment_s, spanning at most two consecutive episodes
  - the hook beat starts <= hook_window_s after the promo start
  - total 20-60 s; a short promo may be extended to the next strong cliffhanger beat
  - the promo ends at the first safe cut after its cliffhanger beat (within end_tolerance_s)
  - every cut is on a CAMERA shot boundary and never inside a line of dialogue
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Beat, Candidate, Episode, Proposal, Rejection, Segment, Show

EPS = 0.01


class Reject(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass
class Validator:
    show: Show
    promo_cfg: dict
    cliff_scores: dict[str, float] = field(default_factory=dict)  # beat_id -> 0-5, used to extend short promos
    hook_scores: dict[str, float] = field(default_factory=dict)  # beat_id -> 0-5, used to add setup to short promos
    _cuts: dict[int, list[float]] = field(default_factory=dict)
    _opens: dict[int, list[float]] = field(default_factory=dict)

    # ---- cut points ------------------------------------------------------
    def safe_cuts(self, ep: Episode) -> list[float]:
        if ep.number not in self._cuts:
            points = set(ep.shot_starts) | set(ep.shot_ends)
            dlg = ep.dialogue
            safe = [t for t in points if not any(d.start + EPS < t < d.end - EPS for d in dlg)]
            self._cuts[ep.number] = sorted(safe)
        return self._cuts[ep.number]

    def opening_points(self, ep: Episode) -> list[float]:
        """Where a promo may START. Nothing plays before a promo's first frame, so with open_on_any_beat it may open
        on any beat start (not only a shot boundary), as long as it doesn't begin in the middle of a spoken line."""
        if not self.promo_cfg.get("open_on_any_beat"):
            return self.safe_cuts(ep)
        if ep.number not in self._opens:
            dlg = ep.dialogue
            points = set(self.safe_cuts(ep)) | {b.start for b in ep.beats}
            self._opens[ep.number] = sorted(t for t in points if not any(d.start + EPS < t < d.end - EPS for d in dlg))
        return self._opens[ep.number]

    def floor_cut(self, ep: Episode, t: float) -> float | None:
        c = [x for x in self.safe_cuts(ep) if x <= t + EPS]
        return c[-1] if c else None

    def ceil_cut(self, ep: Episode, t: float) -> float | None:
        c = [x for x in self.safe_cuts(ep) if x >= t - EPS]
        return c[0] if c else None

    # ---- main ------------------------------------------------------------
    def _beat(self, beat_id: str) -> Beat:
        b = self.show.beat(beat_id)
        if b is None:
            raise Reject("unknown_beat", f"beat {beat_id} does not exist")
        return b

    def build(self, p: Proposal, cand_id: str) -> Candidate:
        cfg = self.promo_cfg
        hook = self._beat(p.hook_beat)
        cliff = self._beat(p.cliffhanger_beat)
        if not p.segments:
            raise Reject("bad_segments", "no segments")
        if len(p.segments) > cfg["max_segments"]:
            raise Reject("bad_segments", f"{len(p.segments)} segments > {cfg['max_segments']}")
        seg_beats = []
        for a, b in p.segments:
            ba, bb = self._beat(a), self._beat(b)
            if ba.episode != bb.episode:
                raise Reject("bad_segments", f"segment {a}..{b} crosses episodes; split it into two segments")
            seg_beats.append((ba, bb))
        eps_used = sorted({ba.episode for ba, _ in seg_beats})
        if eps_used[-1] - eps_used[0] > 1:
            raise Reject("bad_segments", f"episodes {eps_used} are not consecutive")
        if hook.episode != seg_beats[0][0].episode:
            raise Reject("bad_segments", "hook beat is not in the first segment's episode")
        if cliff.episode != seg_beats[-1][0].episode:
            raise Reject("bad_segments", "cliffhanger beat is not in the last segment's episode")

        segments: list[Segment] = []
        n = len(seg_beats)
        for i, (ba, bb) in enumerate(seg_beats):
            ep = self.show.episode(ba.episode)
            if i == 0:
                start = self._hook_start(ep, hook, ba)
            else:
                start = self.floor_cut(ep, ba.start)
            if i == n - 1:
                end = self.ceil_cut(ep, cliff.end)
            else:
                end = self.ceil_cut(ep, bb.end)
            if start is None or end is None:
                raise Reject("bad_segments", f"no safe cut for segment {ba.id}..{bb.id}")
            segments.append(Segment(episode=ep.number, start=start, end=end))

        # chronology and minimum length
        for s in segments:
            if s.duration < cfg["min_segment_s"] - EPS:
                raise Reject("bad_segments", f"segment {s.label()} shorter than {cfg['min_segment_s']}s")
        for a, b in zip(segments, segments[1:]):
            if (b.episode, b.start) < (a.episode, a.end - EPS):
                raise Reject("bad_segments", f"segments overlap or are out of order: {a.label()} then {b.label()}")
        if not (segments[0].start - EPS <= hook.start < segments[0].end):
            raise Reject("bad_segments", "hook beat falls outside the first segment")
        if cliff.start < segments[-1].start - EPS:
            raise Reject("resolved_ending", "cliffhanger beat comes before the last segment starts")

        # duration: a promo of the wrong length is moved to the best unresolved beat that fits (fit_length),
        # or (legacy) only extended to the next strong cliffhanger
        total = sum(s.duration for s in segments)
        adjusted = None
        too_short = total < cfg["min_duration_s"] - EPS
        too_long = total > cfg["max_duration_s"] + EPS
        if (too_short or too_long) and cfg.get("fit_length"):
            fitted = self._fit(hook, cliff, segments, cfg, too_short)
            if fitted:
                adjusted = f"{'extended' if too_short else 'trimmed'} from {total:.1f}s"
                cliff, segments = fitted
            elif too_short and cfg.get("fit_backward") and len(segments) == 1:
                earlier = self._fit_backward(hook, segments[0], cfg)
                if earlier:
                    adjusted = f"started earlier (from {total:.1f}s)"
                    hook, segments = earlier
        elif too_short:
            cliff, segments = self._extend(cliff, segments, cfg)
        total = sum(s.duration for s in segments)
        if total < cfg["min_duration_s"] - EPS:
            raise Reject("bad_duration", f"{total:.1f}s is shorter than {cfg['min_duration_s']}s")
        if total > cfg["max_duration_s"] + EPS:
            raise Reject("bad_duration", f"{total:.1f}s is longer than {cfg['max_duration_s']}s")
        if segments[-1].end - cliff.end > cfg["end_tolerance_s"] + EPS:
            raise Reject("resolved_ending", f"first safe cut after the cliffhanger is {segments[-1].end - cliff.end:.1f}s later")

        return Candidate(
            id=cand_id, source=p.source, episodes=sorted({s.episode for s in segments}), segments=segments,
            hook_beat=hook.id, cliffhanger_beat=cliff.id, hook_type=p.hook_type,
            hook_text=beat_line(hook), cliffhanger_text=beat_line(cliff), why=p.why, adjusted=adjusted,
        )

    def _fit(self, hook: Beat, cliff: Beat, segments: list[Segment], cfg: dict, too_short: bool):
        """Re-pick the ending: the most unresolved beat in the last segment's episode that makes the promo fit.

        Too short: look later, up to fit_target_max_s total (keeps promos punchy rather than maxing out at 60 s).
        Too long: look earlier, but never before the hook. Ties go to the beat closest to the model's own ending."""
        last = segments[-1]
        ep = self.show.episode(last.episode)
        others = sum(s.duration for s in segments[:-1])
        lo = cfg["min_duration_s"]
        hi = cfg.get("fit_target_max_s", 45.0) if too_short else cfg["max_duration_s"]
        options = []
        for b in ep.beats:
            if b.kind == "CAMERA" or b.start < last.start - EPS:
                continue
            if len(segments) == 1 and b.end <= hook.end + EPS:
                continue
            if too_short and b.end <= cliff.end + EPS:
                continue
            end = self.ceil_cut(ep, b.end)
            if end is None or end - b.end > cfg["end_tolerance_s"] + EPS:
                continue
            total = others + (end - last.start)
            if lo - EPS <= total <= hi + EPS:
                options.append((self.cliff_scores.get(b.id, 0.0), -abs(b.end - cliff.end), b, end))
        if not options:
            return None
        score, _, b, end = max(options, key=lambda o: (o[0], o[1]))
        if score < cfg.get("fit_min_cliff", 1.0):
            return None
        return b, segments[:-1] + [Segment(episode=last.episode, start=last.start, end=end)]

    def _fit_backward(self, hook: Beat, seg: Segment, cfg: dict):
        """Too short and nothing later fits (usually the episode is ending): open instead on the strongest
        earlier hook so the clip gains setup, keeping the same cliffhanger ending."""
        ep = self.show.episode(seg.episode)
        lo, hi = cfg["min_duration_s"], cfg.get("fit_target_max_s", 45.0)
        options = []
        for b in ep.beats:
            if b.start >= hook.start - EPS:
                break
            score = self.hook_scores.get(b.id, 0.0)
            if score < cfg.get("fit_min_hook", 2.5):
                continue
            try:
                start = self._hook_start(ep, b, b)
            except Reject:
                continue
            if lo - EPS <= seg.end - start <= hi + EPS:
                options.append((score, b.start, b, start))
        if not options:
            return None
        _, _, b, start = max(options, key=lambda o: (o[0], o[1]))  # strongest, then the latest (least setup)
        return b, [Segment(episode=seg.episode, start=start, end=seg.end)]

    def _hook_start(self, ep: Episode, hook: Beat, first_beat: Beat) -> float:
        window = self.promo_cfg["hook_window_s"]
        options = [c for c in self.opening_points(ep) if hook.start - window - EPS <= c <= hook.start + EPS]
        if not options:
            raise Reject("hook_late", f"no clean cut within {window}s before hook {hook.id} at {hook.start:.2f}s")
        # stay as close as possible to where the proposer wanted to start, without breaking the hook window
        return min(options, key=lambda c: (abs(c - min(first_beat.start, hook.start)), -c))

    def _extend(self, cliff: Beat, segments: list[Segment], cfg: dict):
        last = segments[-1]
        ep = self.show.episode(last.episode)
        others = sum(s.duration for s in segments[:-1])
        for b in ep.beats:
            if b.kind == "CAMERA" or b.end <= cliff.end + EPS:
                continue
            if self.cliff_scores.get(b.id, 0.0) < cfg["extend_min_cliff"]:
                continue
            end = self.ceil_cut(ep, b.end)
            if end is None:
                continue
            total = others + (end - last.start)
            if total > cfg["max_duration_s"] + EPS:
                break
            if total >= cfg["min_duration_s"] - EPS:
                return b, segments[:-1] + [Segment(episode=last.episode, start=last.start, end=end)]
        return cliff, segments

    def validate(self, proposals: list[Proposal], id_prefix: str) -> tuple[list[Candidate], list[Rejection]]:
        good, bad = [], []
        seen = set()
        for i, p in enumerate(proposals, 1):
            try:
                c = self.build(p, f"{id_prefix}-{i}")
            except Reject as r:
                eps = sorted({int(x.split('-')[0][1:]) for x in [p.hook_beat] if x[:1] == "E" and x.split('-')[0][1:].isdigit()})
                bad.append(Rejection(source=p.source, episodes=eps, reason=r.reason, detail=r.detail, proposal=p.model_dump()))
                continue
            key = tuple((s.episode, round(s.start, 2), round(s.end, 2)) for s in c.segments)
            if key in seen:
                bad.append(Rejection(source=p.source, episodes=c.episodes, reason="duplicate", detail="same cut as an earlier proposal", proposal=p.model_dump()))
                continue
            seen.add(key)
            good.append(c)
        return good, bad

    def tail(self, ep: Episode, seconds: float, cand_id: str) -> Candidate | None:
        """Naive baseline cut: the last `seconds` of the episode, snapped to a safe cut. No hook rule."""
        start = self.floor_cut(ep, ep.duration - seconds)
        end = self.ceil_cut(ep, ep.duration)
        if start is None or end is None or end - start < self.promo_cfg["min_duration_s"] - EPS:
            return None
        content = [b for b in ep.beats if b.kind != "CAMERA" and b.start >= start - EPS]
        if not content:
            return None
        hook, cliff = content[0], content[-1]
        return Candidate(id=cand_id, source="baseline", episodes=[ep.number],
                         segments=[Segment(episode=ep.number, start=start, end=end)], hook_beat=hook.id,
                         cliffhanger_beat=cliff.id, hook_type="other", hook_text=beat_line(hook),
                         cliffhanger_text=beat_line(cliff), why="Baseline: the final seconds of the episode.")


def beat_line(b: Beat) -> str:
    if b.kind == "DIALOGUE":
        return f"{b.speaker}: {b.text}"
    return f"[{b.kind}] {b.text}"
