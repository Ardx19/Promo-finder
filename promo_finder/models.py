"""Core data types: beats, episodes, shows, promo candidates."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

BeatKind = Literal["CAMERA", "ACTION", "DIALOGUE", "SFX"]


def fmt_ts(seconds: float) -> str:
    """Seconds -> MM:SS.ss (the screenplay's own format)."""
    m, s = divmod(round(seconds, 2), 60)
    return f"{int(m):02d}:{s:05.2f}"


class Beat(BaseModel):
    id: str  # e.g. E07-B012
    episode: int
    scene: int
    kind: BeatKind
    start: float
    end: float
    speaker: str | None = None
    emotions: list[str] = Field(default_factory=list)
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class Episode(BaseModel):
    number: int
    source_name: str = ""
    logline: str = ""
    continuity: str = ""
    beats: list[Beat]
    duration: float

    _idx: dict[str, Beat] | None = PrivateAttr(default=None)

    def beat(self, beat_id: str) -> Beat | None:
        if self._idx is None:
            self._idx = {b.id: b for b in self.beats}
        return self._idx.get(beat_id)

    @property
    def shots(self) -> list[Beat]:
        return [b for b in self.beats if b.kind == "CAMERA"]

    @property
    def shot_starts(self) -> list[float]:
        return sorted({b.start for b in self.shots} | {0.0})

    @property
    def shot_ends(self) -> list[float]:
        return sorted({b.end for b in self.shots} | {self.duration})

    @property
    def dialogue(self) -> list[Beat]:
        return [b for b in self.beats if b.kind == "DIALOGUE"]

    def beats_between(self, start: float, end: float) -> list[Beat]:
        """Non-camera beats that overlap [start, end), in time order."""
        eps = 1e-6
        return sorted(
            (b for b in self.beats if b.kind != "CAMERA" and b.end > start + eps and b.start < end - eps),
            key=lambda b: (b.start, b.id),
        )


class Show(BaseModel):
    slug: str
    title: str
    episodes: list[Episode]

    def episode(self, number: int) -> Episode | None:
        for e in self.episodes:
            if e.number == number:
                return e
        return None

    def beat(self, beat_id: str) -> Beat | None:
        try:
            ep = int(beat_id.split("-")[0][1:])
        except (ValueError, IndexError):
            return None
        e = self.episode(ep)
        return e.beat(beat_id) if e else None


class Segment(BaseModel):
    episode: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def label(self) -> str:
        return f"E{self.episode:02d} {fmt_ts(self.start)}-{fmt_ts(self.end)}"


class Scores(BaseModel):
    hook: float = 3.0
    cliffhanger: float = 3.0
    standalone: float = 3.0
    appeal: float = 3.0
    spoiler: bool = False


class Proposal(BaseModel):
    """What a proposer (LLM or rules) suggests, expressed only as beat IDs."""

    hook_beat: str
    cliffhanger_beat: str
    segments: list[tuple[str, str]]  # (start_beat_id, end_beat_id) per segment, chronological
    hook_type: str = "other"
    why: str = ""
    source: str = "llm"  # llm | rules | baseline | stitch-llm | stitch-rules


class Candidate(BaseModel):
    id: str
    source: str
    episodes: list[int]
    segments: list[Segment]
    hook_beat: str
    cliffhanger_beat: str
    hook_type: str
    hook_text: str
    cliffhanger_text: str
    why: str
    proposer_scores: Scores = Field(default_factory=Scores)
    judge: dict | None = None  # summary, open_question, confusing_refs, scores
    scores: Scores = Field(default_factory=Scores)
    score: float = 0.0
    rank: int | None = None
    excluded_reason: str | None = None
    fallback: bool = False
    adjusted: str | None = None  # e.g. "extended from 14.8s": the validator re-picked the ending to fit 20-60 s

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)


class Rejection(BaseModel):
    source: str
    episodes: list[int]
    reason: str
    detail: str
    proposal: dict
