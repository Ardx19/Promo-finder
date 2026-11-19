"""JSON shapes the LLM must return. Scores are clamped instead of rejected (models drift to 0-10)."""
from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field

HOOK_TYPES = {"violence", "humiliation", "betrayal", "reveal", "threat", "romance", "power_reversal", "shocking_line", "other"}


def _clamp(v, lo=0.0, hi=5.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return (lo + hi) / 2
    return max(lo, min(hi, v))


def _hook_type(v):
    v = str(v or "other").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in HOOK_TYPES else "other"


Score05 = Annotated[float, BeforeValidator(lambda v: _clamp(v, 0.0, 5.0))]
Score15 = Annotated[float, BeforeValidator(lambda v: _clamp(v, 1.0, 5.0))]
HookType = Annotated[str, BeforeValidator(_hook_type)]


class Character(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str = ""


class Reveal(BaseModel):
    episode: int | None = None
    reveal: str = ""


class BibleOut(BaseModel):
    premise: str = ""
    characters: list[Character] = Field(default_factory=list)
    major_reveals: list[Reveal] = Field(default_factory=list)
    tropes: list[str] = Field(default_factory=list)

    def alias_map(self) -> dict[str, str]:
        m = {}
        for c in self.characters:
            for a in c.aliases + [c.name]:
                m[a.strip().upper()] = c.name
        return m


class BeatTag(BaseModel):
    id: str = Field(validation_alias=AliasChoices("id", "beat_id"))
    hook: Score05 = 0.0
    hook_type: HookType = "other"
    cliffhanger: Score05 = 0.0


class PromoOut(BaseModel):
    hook_beat: str
    start_beat: str | None = None
    end_beat: str | None = None
    cliffhanger_beat: str
    hook_type: HookType = "other"
    why: str = ""
    hook: Score15 = 3.0
    cliffhanger: Score15 = 3.0
    appeal: Score15 = 3.0


class EpisodeOut(BaseModel):
    beats: list[BeatTag] = Field(default_factory=list)
    promos: list[PromoOut] = Field(default_factory=list)


class RepairOut(BaseModel):
    """Repair replies only need promos; anything else the model adds is ignored."""
    promos: list[PromoOut] = Field(default_factory=list)


class StitchPromo(BaseModel):
    hook_beat: str
    seg1_end_beat: str
    seg2_start_beat: str
    cliffhanger_beat: str
    hook_type: HookType = "other"
    why: str = ""
    hook: Score15 = 3.0
    cliffhanger: Score15 = 3.0
    appeal: Score15 = 3.0


class StitchOut(BaseModel):
    promo: StitchPromo | None = None


class JudgeResult(BaseModel):
    id: str
    summary: str = ""
    open_question: str = ""
    confusing_refs: list[str] = Field(default_factory=list)
    hook: Score15 = 3.0
    cliffhanger: Score15 = 3.0
    standalone: Score15 = 3.0
    appeal: Score15 = 3.0
    spoiler: bool = False


class JudgeOut(BaseModel):
    results: list[JudgeResult] = Field(default_factory=list)
