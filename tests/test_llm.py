import pytest
from pydantic import BaseModel

import promo_finder.llm as llm
from promo_finder.config import ProviderSettings, provider_settings
from promo_finder.llm import LLMClient, LLMError, QuotaError

from .fakes import FakeClient, FakeStatusError


class Answer(BaseModel):
    value: int


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "demo.v1.md").write_text("Return value for {{x}}", encoding="utf-8")
    monkeypatch.setattr(llm, "PROMPT_DIR", d)
    return d


def make(tmp_path, fake, model="m1", judge=None):
    s = ProviderSettings(name="fake", base_url=None, model=model, api_key="k", rpm=100000, judge_model=judge)
    return LLMClient(s, {"max_retries": 2}, cache_dir=tmp_path / "cache", client=fake)


def test_cache_hit_makes_no_call(tmp_path, prompts):
    fake = FakeClient([{"value": 1}])
    c = make(tmp_path, fake)
    assert c.complete_json("demo", {"x": "a"}, Answer).value == 1
    c2 = make(tmp_path, FakeClient([]))
    assert c2.complete_json("demo", {"x": "a"}, Answer).value == 1
    assert c2.meter.calls == 0 and c2.meter.cache_hits == 1


def test_prompt_version_or_model_changes_cache_key(tmp_path, prompts):
    make(tmp_path, FakeClient([{"value": 1}])).complete_json("demo", {"x": "a"}, Answer)
    other_model = make(tmp_path, FakeClient([{"value": 2}]), model="m2")
    assert other_model.complete_json("demo", {"x": "a"}, Answer).value == 2
    (prompts / "demo.v2.md").write_text("New wording for {{x}}", encoding="utf-8")
    new_version = make(tmp_path, FakeClient([{"value": 3}]))
    assert new_version.complete_json("demo", {"x": "a"}, Answer).value == 3
    assert new_version.prompt_versions["demo"] == "demo.v2"


def test_invalid_json_repaired_on_retry(tmp_path, prompts):
    fake = FakeClient(["not json at all", '```json\n{"value": 7}\n```'])
    c = make(tmp_path, fake)
    assert c.complete_json("demo", {"x": "a"}, Answer).value == 7
    assert c.meter.calls == 2 and c.meter.repairs == 1


def test_invalid_twice_raises(tmp_path, prompts):
    c = make(tmp_path, FakeClient(["nope", '{"value": "x"}']))
    with pytest.raises(LLMError):
        c.complete_json("demo", {"x": "a"}, Answer)


def test_429_backs_off_then_succeeds(tmp_path, prompts, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = make(tmp_path, FakeClient([FakeStatusError(429), {"value": 5}]))
    assert c.complete_json("demo", {"x": "a"}, Answer).value == 5


def test_persistent_429_raises_quota_error(tmp_path, prompts, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = make(tmp_path, FakeClient([FakeStatusError(429)] * 3))
    with pytest.raises(QuotaError):
        c.complete_json("demo", {"x": "a"}, Answer)


def test_judge_role_uses_judge_model(tmp_path, prompts):
    fake = FakeClient([{"value": 1}])
    c = make(tmp_path, fake, judge="judge-model")
    c.complete_json("demo", {"x": "a"}, Answer, role="judge")
    assert fake.calls[0]["model"] == "judge-model"


def test_meter_totals(tmp_path, prompts):
    c = make(tmp_path, FakeClient([{"value": 1}, {"value": 2}]))
    c.complete_json("demo", {"x": "a"}, Answer)
    c.complete_json("demo", {"x": "b"}, Answer)
    assert c.meter.calls == 2 and c.meter.prompt_tokens == 200 and c.meter.by_prompt["demo"] == 2


def test_missing_key_message_suggests_rules_mode(tmp_path, prompts):
    s = ProviderSettings(name="fake", base_url=None, model="m", api_key=None, rpm=1000, judge_model=None)
    c = LLMClient(s, {}, cache_dir=tmp_path / "cache")
    with pytest.raises(LLMError, match="--mode rules"):
        c.complete_json("demo", {"x": "a"}, Answer)


def test_env_overrides_config(cfg, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "override-model")
    monkeypatch.setenv("LLM_API_KEY", "abc")
    s = provider_settings(cfg)
    assert s.model == "override-model" and s.api_key == "abc"
