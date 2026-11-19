"""No-AI scoring: keyword + emotion-tag heuristics for hooks and cliffhangers, plus the tail baseline.

Used for --mode rules (works offline, instant), as the fallback when an LLM call fails,
and as a comparison point in evaluation. Deliberately simple and explainable.
"""
from __future__ import annotations

import re

from .models import Beat, Episode, Proposal, Scores

HOOK_LEXICON: dict[str, list[str]] = {
    "violence": [r"\bslap", r"\bstrike", r"\bstruck\b", r"\bpunch", r"\bshove", r"\bgrab", r"\bchok", r"\bkick",
                 r"\bthrows?\b", r"\bhits?\b", r"\bblood", r"\bknife", r"\bgun\b", r"\bstab", r"\bwrist"],
    "humiliation": [r"\b(pours?|empties|dumps?|throws?)\b.{0,40}\b(over|on|onto|in)\s+(her|his|him|their|the)\b", r"\bspill", r"\bkneel", r"\blaugh(s|ing)? at", r"\bservant", r"\bmaid\b", r"\bbeg",
                    r"\btrash\b", r"\bworthless", r"\bplain(ness)?\b", r"\bpathetic", r"\bhumiliat", r"\bmock"],
    "betrayal": [r"\bbetray", r"\bcheat", r"\baffair", r"\babandon", r"\bjilt", r"\bleft me", r"\bbehind my back",
                 r"\bmarry (her|him) instead", r"\bwith my sister"],
    "reveal": [r"\btruth\b", r"\bsecret", r"\bheir(ess)?\b", r"\breal (father|mother|name)", r"\bpregnant", r"\bidentity",
               r"\bduke\b", r"\bking\b", r"\bprince\b", r"\bbillionaire", r"\bceo\b", r"\bwho you (really )?are",
               r"\bdid not die", r"\bisn'?t (dead|who)", r"\bwill\b.*\bto\b", r"\breveal"],
    "threat": [r"\bkill", r"\bdestroy", r"\bruin", r"\bregret", r"\byou'?ll pay", r"\bdead\b", r"\bdie\b", r"\barrest",
               r"\bprison", r"\bthreat", r"\bfire\b", r"\bflames?\b", r"\bpoison"],
    "romance": [r"\bkiss", r"\bembrace", r"\bmarry me", r"\bi love you", r"\bring\b", r"\bwedding"],
    "power_reversal": [r"never again", r"on your knees", r"my house", r"how dare", r"\bkneel before", r"\bfired\b",
                       r"\bget out\b", r"\bnot anymore", r"\bthe sole heir", r"\bit isn'?t\b"],
}
TYPE_WEIGHT = {"violence": 1.2, "betrayal": 1.1, "reveal": 1.1, "power_reversal": 1.1, "humiliation": 1.0,
               "threat": 0.9, "romance": 0.8}
APPEAL = {"violence": 4.5, "humiliation": 4.2, "betrayal": 4.5, "reveal": 4.3, "threat": 3.8,
          "power_reversal": 4.4, "romance": 3.6, "shocking_line": 3.5, "other": 3.0}

HIGH_EMOTION = {"furious", "enraged", "shocked", "screaming", "shouting", "desperate", "cold", "cruel", "mocking",
                "venomous", "menacing", "livid", "sobbing", "horrified", "stunned", "triumphant", "defiant",
                "threatening", "shrill", "contemptuous", "seething", "hysterical", "blunt", "unforgiving", "smug"}
LOW_EMOTION = {"gentle", "soft", "warm", "calm", "quiet", "tender", "polite", "formal", "kind", "relieved"}

CLIFF_SFX = re.compile(r"cliffhanger|sting|swell|drone|peak|thud|boom|gasp|crash|shatter|heartbeat|silence", re.I)
CLIFF_TEXT = re.compile(r"\?\s*\"?$|\.\.\.\"?$|\bwho\b|\bwhat\b|\bsecret|\btruth|\bdid not die|\bmother\?|\bfather\?", re.I)

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in HOOK_LEXICON.items()}


def hook_type_of(text: str) -> tuple[str, int]:
    best, hits = "other", 0
    for k, pats in _COMPILED.items():
        n = sum(1 for p in pats if p.search(text))
        if n > hits:
            best, hits = k, n
    return best, hits


def hook_score(b: Beat) -> tuple[float, str]:
    """0-5: how likely this beat stops a scrolling viewer in the first 3 seconds."""
    if b.kind == "CAMERA":
        return 0.0, "other"
    t, hits = hook_type_of(b.text)
    s = min(2.5, 1.2 * hits) * TYPE_WEIGHT.get(t, 1.0)
    emos = {e.lower() for e in b.emotions}
    s += 0.6 * len(emos & HIGH_EMOTION) - 0.5 * len(emos & LOW_EMOTION)
    if b.kind == "DIALOGUE" and "!" in b.text:
        s += 0.6
        if t == "other":
            t = "shocking_line"
    if b.kind == "ACTION" and hits:
        s += 0.8  # visible physical action is the strongest stop-scroll signal
    if b.kind == "SFX" and re.search(r"slap|crack|scream|gasp|shatter", b.text, re.I):
        s += 0.8
    return max(0.0, min(5.0, s)), t


def cliff_score(b: Beat, ep: Episode) -> float:
    """0-5: how unresolved / must-know-next the moment is if the promo cuts right after it."""
    if b.kind == "CAMERA":
        return 0.0
    s = 0.0
    if b.kind == "SFX" and CLIFF_SFX.search(b.text):
        s += 2.5 if re.search(r"cliffhanger|sting|peak", b.text, re.I) else 1.2
    if CLIFF_TEXT.search(b.text):
        s += 1.5
    t, hits = hook_type_of(b.text)
    if t in ("reveal", "threat", "betrayal", "power_reversal") and hits:
        s += 1.0
    if {e.lower() for e in b.emotions} & {"shocked", "stunned", "breathless", "trembling", "horrified", "whispering"}:
        s += 0.6
    if ep.duration - b.end <= 10.0:
        s += 1.5  # episodes are written to end on a cliffhanger
    return max(0.0, min(5.0, s))


def score_episode(ep: Episode) -> tuple[dict[str, tuple[float, str]], dict[str, float]]:
    hooks = {b.id: hook_score(b) for b in ep.beats if b.kind != "CAMERA"}
    cliffs = {b.id: cliff_score(b, ep) for b in ep.beats if b.kind != "CAMERA"}
    return hooks, cliffs


def propose_rules(ep: Episode, promo_cfg: dict, k: int = 4) -> list[Proposal]:
    """Windows that open on a top hook beat and close on the strongest cliffhanger 20-60 s later."""
    hooks, cliffs = score_episode(ep)
    beats = {b.id: b for b in ep.beats}
    ranked_hooks = sorted(hooks.items(), key=lambda kv: (-kv[1][0], beats[kv[0]].start))
    out, used_cliffs = [], set()
    lo, hi = promo_cfg["min_duration_s"], promo_cfg["max_duration_s"]
    for hid, (hs, htype) in ranked_hooks:
        if hs < 1.5 or len(out) >= k:
            break
        h = beats[hid]
        window = [(cid, cs) for cid, cs in cliffs.items()
                  if h.start + lo - 2.0 <= beats[cid].end <= h.start + hi - 3.0 and cs > 0]
        if not window:
            continue
        cid, cs = max(window, key=lambda kv: (kv[1], beats[kv[0]].end))
        if cid in used_cliffs and len(window) > 1:
            alt = [w for w in window if w[0] not in used_cliffs]
            if alt:
                cid, cs = max(alt, key=lambda kv: (kv[1], beats[kv[0]].end))
        used_cliffs.add(cid)
        out.append(Proposal(hook_beat=hid, cliffhanger_beat=cid, segments=[(hid, cid)], hook_type=htype,
                            why=f"Rules: opens on a {htype.replace('_', ' ')} beat and cuts on an unresolved moment.",
                            source="rules"))
    return out


def rules_scores(p: Proposal, ep_hooks: dict[str, tuple[float, str]], ep_cliffs: dict[str, float]) -> Scores:
    hs = ep_hooks.get(p.hook_beat, (2.5, "other"))[0]
    cs = ep_cliffs.get(p.cliffhanger_beat, 2.5)
    return Scores(hook=max(1.0, hs), cliffhanger=max(1.0, cs), standalone=3.0, appeal=APPEAL.get(p.hook_type, 3.0))


def baseline_proposal(ep: Episode, promo_cfg: dict) -> Proposal | None:
    """Tail baseline: the last N seconds of the episode (episodes already end on cliffhangers)."""
    tail = promo_cfg.get("tail_baseline_s", 45.0)
    if ep.duration < promo_cfg["min_duration_s"]:
        return None
    start_t = max(0.0, ep.duration - tail)
    content = [b for b in ep.beats if b.kind != "CAMERA"]
    first = next((b for b in content if b.start >= start_t - 0.01), None)
    if first is None:
        return None
    last = content[-1]
    return Proposal(hook_beat=first.id, cliffhanger_beat=last.id, segments=[(first.id, last.id)],
                    hook_type="other", why="Baseline: the final seconds of the episode.", source="baseline")
