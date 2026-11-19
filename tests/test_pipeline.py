import json
import re

import pytest

from promo_finder.config import ProviderSettings
from promo_finder.export import parse_segments_str, segments_str
from promo_finder.llm import LLMClient
from promo_finder.models import Candidate, Scores, Segment
from promo_finder.pipeline import run
from promo_finder.rank import composite, iou, rank

from .fakes import FakeClient, FakeStatusError

ID_RE = re.compile(r"^(E\d{2}-B\d{3}) \| (\d\d:\d\d\.\d\d)-(\d\d:\d\d\.\d\d) \| ([^|]+) \| (.*)$", re.M)


def _secs(t):
    m, s = t.split(":")
    return int(m) * 60 + float(s)


def smart_responder(messages):
    """Answers like a reasonable model, using only IDs present in the prompt."""
    prompt = messages[-1]["content"] if messages[-1]["role"] == "user" else messages[1]["content"]
    if "show bible" in prompt.lower() and "SPEAKER LABELS" in prompt:
        return {"premise": "A maid is the true heiress.", "characters": [
            {"name": "Maya", "aliases": ["MAYA"], "role": "heroine"},
            {"name": "Victoria", "aliases": ["VICTORIA"], "role": "villain sister"}],
            "major_reveals": [{"episode": 1, "reveal": "Maya is the heiress"}], "tropes": ["hidden heiress"]}
    if prompt.startswith("Find the best social-media promos"):
        rows = [(m.group(1), _secs(m.group(2)), _secs(m.group(3)), m.group(4), m.group(5)) for m in ID_RE.finditer(prompt)]
        content = [r for r in rows if r[3].strip() != "CAMERA"]
        hook = next((r for r in content if re.search(r"slap|wine|doors|curtains|ring", r[4], re.I)), content[0])
        cliffs = [r for r in content if hook[1] + 20 <= r[2] <= hook[1] + 55]
        cliff = max(cliffs, key=lambda r: ("cliffhanger" in r[4], r[2])) if cliffs else content[-1]
        return {"beats": [{"id": cliff[0], "hook": 1, "hook_type": "other", "cliffhanger": 5}],
                "promos": [{"hook_beat": hook[0], "start_beat": hook[0], "end_beat": cliff[0],
                            "cliffhanger_beat": cliff[0], "hook_type": "humiliation", "why": "Cruelty then a twist.",
                            "hook": 4, "cliffhanger": 5, "appeal": 4},
                           {"hook_beat": "E99-B999", "start_beat": "E99-B999", "end_beat": "E99-B999",
                            "cliffhanger_beat": "E99-B999", "why": "invented"}]}
    if "automatic checker rejected" in prompt:
        return {"beats": [], "promos": []}  # drop the bad proposals
    if "joins the end of episode" in prompt:
        return {"promo": None}
    if "never seen this show" in prompt.lower():
        ids = re.findall(r"=== CLIP (\S+) ", prompt)
        return {"results": [{"id": i, "summary": "A woman is humiliated.", "open_question": "Who is she really?",
                             "confusing_refs": [], "hook": 4, "cliffhanger": 4, "standalone": 4, "appeal": 4,
                             "spoiler": False} for i in reversed(ids)]}
    raise AssertionError("unexpected prompt: " + prompt[:80])


def llm_client(tmp_path, fake):
    s = ProviderSettings(name="fake", base_url=None, model="fake-model", api_key="k", rpm=100000, judge_model=None)
    return LLMClient(s, {"max_retries": 1}, cache_dir=tmp_path / "cache", client=fake)


EXPECTED_FILES = {"promos.json", "promos.csv", "top_picks.md", "rejected.csv", "run_meta.json"}


def test_rules_mode_writes_all_outputs(show, cfg, tmp_path):
    res = run(show, "rules", cfg=cfg, out_root=tmp_path)
    assert {p.name for p in res.out_dir.iterdir()} == EXPECTED_FILES
    meta = json.loads((res.out_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "rules" and meta["llm"] is None
    assert meta["counts"]["valid"] > 0


def test_llm_mode_end_to_end_with_fake(show, cfg, tmp_path):
    fake = FakeClient(responder=smart_responder)
    res = run(show, "llm", cfg=cfg, out_root=tmp_path, llm_client=llm_client(tmp_path, fake))
    m = res.meta
    assert m["mode"] == "llm" and not res.warnings
    by_prompt = res.meta["llm"]["by_prompt"]
    assert by_prompt["bible"] == 1 and by_prompt["episode"] == 3
    assert "unknown_beat" in m["counts"]["rejection_reasons"]  # the invented ID was caught, not used
    llm_cands = [c for c in res.ranked if c.source == "llm"]
    assert llm_cands and all(c.judge for c in llm_cands)
    times = {(e.number, t) for e in show.episodes for b in e.beats for t in (b.start, b.end)} | \
            {(e.number, e.duration) for e in show.episodes} | {(e.number, 0.0) for e in show.episodes}
    for c in res.ranked:
        for s in c.segments:
            assert (s.episode, s.start) in times and (s.episode, s.end) in times


def test_repair_round_fixes_a_rejected_proposal(show, cfg, tmp_path):
    """A too-short proposal is sent back with its reason; the model's fix is validated and kept."""
    def responder(messages):
        p = messages[1]["content"]
        if p.startswith("Find the best") and "episode 1 of" in p:
            return {"beats": [], "promos": [{"hook_beat": "E01-B004", "start_beat": "E01-B004", "end_beat": "E01-B006",
                                             "cliffhanger_beat": "E01-B006", "hook_type": "humiliation", "why": "too short"}]}
        if "automatic checker rejected" in p and "episode 1 " in p:
            assert "bad_duration" in p  # the reason is fed back
            hook = next(b for b in show.episode(1).beats if "empties her wine glass" in b.text).id
            cliff = next(b for b in show.episode(1).beats if "ominous cliffhanger peak" in b.text).id
            return {"beats": [], "promos": [{"hook_beat": hook, "start_beat": hook, "end_beat": cliff,
                                             "cliffhanger_beat": cliff, "hook_type": "humiliation", "why": "fixed",
                                             "hook": 5, "cliffhanger": 5, "appeal": 5}]}
        return smart_responder(messages)
    cfg = {**cfg, "promo": {**cfg["promo"], "fit_length": False, "fit_backward": False}}  # make code-side fixing fail
    res = run(show, "llm", cfg=cfg, write=False, max_episodes=1, stitch=False,
              llm_client=llm_client(tmp_path, FakeClient(responder=responder)))
    fixed = [c for c in res.ranked if c.source == "llm-repair"]
    assert len(fixed) == 1 and fixed[0].why == "fixed" and 20 <= fixed[0].duration <= 60
    assert res.meta["repaired_promos"] == 1 and res.meta["llm"]["by_prompt"]["repair"] == 1


def test_repair_disabled_by_config(show, cfg, tmp_path):
    cfg = {**cfg, "promo": {**cfg["promo"], "repair": False}}
    res = run(show, "llm", cfg=cfg, write=False, llm_client=llm_client(tmp_path, FakeClient(responder=smart_responder)))
    assert "repair" not in res.meta["llm"]["by_prompt"]


def test_second_run_uses_cache(show, cfg, tmp_path):
    run(show, "llm", cfg=cfg, write=False, llm_client=llm_client(tmp_path, FakeClient(responder=smart_responder)))
    again = llm_client(tmp_path, FakeClient([]))
    res = run(show, "llm", cfg=cfg, write=False, llm_client=again)
    assert res.meta["llm"]["calls"] == 0 and res.meta["llm"]["cache_hits"] > 0


def test_episode_failure_falls_back_to_rules(show, cfg, tmp_path):
    def flaky(messages):
        p = messages[1]["content"]
        if p.startswith("Find the best") and "episode 2 of" in p:
            return "garbage"
        return smart_responder(messages)
    res = run(show, "llm", cfg=cfg, write=False, llm_client=llm_client(tmp_path, FakeClient(responder=flaky)))
    assert 2 in res.meta["fallback_episodes"]
    assert any(c.fallback and c.episodes == [2] for c in res.ranked)


def test_quota_exhaustion_degrades_to_rules(show, cfg, tmp_path, monkeypatch):
    import promo_finder.llm as llm_mod
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)

    def quota(messages):
        if messages[1]["content"].startswith("Find the best"):
            return FakeStatusError(429, "quota")
        return smart_responder(messages)

    class Raising(FakeClient):
        def _create(self, **kw):
            r = quota(kw["messages"])
            if isinstance(r, Exception):
                raise r
            return super()._create(**kw)

    fake = Raising(responder=smart_responder)
    res = run(show, "llm", cfg=cfg, write=False, llm_client=llm_client(tmp_path, fake))
    assert res.meta["fallback_episodes"] == [1, 2, 3]
    assert any("quota" in w for w in res.warnings)


def test_max_episodes(show, cfg):
    res = run(show, "rules", cfg=cfg, write=False, max_episodes=2)
    assert res.meta["episodes"] == 2 and all(e in (1, 2) for c in res.ranked for e in c.episodes)


def test_missing_key_message(show, cfg, monkeypatch):
    from promo_finder.llm import LLMError
    for k in ("LLM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("promo_finder.config.load_dotenv", None, raising=False)
    with pytest.raises(LLMError, match="--mode rules"):
        run(show, "llm", cfg=cfg, provider="groq-gpt-oss", write=False)


# ---- ranking -------------------------------------------------------------------
def cand(cid, ep, start, end, s=(4, 4, 4, 4), source="llm", judge=None):
    return Candidate(id=cid, source=source, episodes=[ep], segments=[Segment(episode=ep, start=start, end=end)],
                     hook_beat="x", cliffhanger_beat="y", hook_type="reveal", hook_text="h", cliffhanger_text="c",
                     why="w", proposer_scores=Scores(hook=s[0], cliffhanger=s[1], standalone=s[2], appeal=s[3]),
                     judge=judge)


def test_overlap_keeps_higher(cfg):
    a = cand("a", 1, 0, 30, (5, 5, 5, 5))
    b = cand("b", 1, 10, 40, (4, 4, 4, 4))  # IoU 0.5 with a
    out = rank([a, b], cfg["rank"], cfg["judge"])
    assert out[0].id == "a"
    assert iou(a, b) == pytest.approx(0.5)
    assert out[1].id == "b"  # still listed, but after the top picks


def test_max_two_per_episode_in_top(cfg):
    cs = [cand(f"c{i}", 1, i * 40, i * 40 + 30, (5, 5, 5, 5)) for i in range(3)] + [cand("d", 2, 0, 30, (3, 3, 3, 3))]
    out = rank(cs, cfg["rank"], cfg["judge"])
    assert [c.id for c in out[:3]] == ["c0", "c1", "d"]


def test_position_factor_prefers_early(cfg):
    early, late = cand("early", 3, 0, 30), cand("late", 40, 0, 30)
    out = rank([late, early], cfg["rank"], cfg["judge"])
    assert out[0].id == "early"


def test_spoiler_penalty(cfg):
    s = Scores(hook=4, cliffhanger=4, standalone=4, appeal=4)
    s2 = Scores(hook=4, cliffhanger=4, standalone=4, appeal=4, spoiler=True)
    assert composite(s, 1, cfg["rank"]) - composite(s2, 1, cfg["rank"]) == pytest.approx(10)


def test_stitched_not_standalone_excluded(cfg):
    """Covers AE4."""
    j = {"hook": 4, "cliffhanger": 4, "standalone": 2, "appeal": 4, "spoiler": False}
    c = cand("s", 1, 0, 30, source="stitch-llm", judge=j)
    out = rank([c], cfg["rank"], cfg["judge"])
    assert out[0].excluded_reason == "not_standalone" and out[0].rank is None


def test_segment_string_round_trip():
    segs = [Segment(episode=1, start=12.3, end=41.8), Segment(episode=2, start=0.0, end=9.5)]
    s = segments_str(segs)
    assert s == "E01 00:12.30-00:41.80; E02 00:00.00-00:09.50"
    assert [x.model_dump() for x in parse_segments_str(s)] == [x.model_dump() for x in segs]
