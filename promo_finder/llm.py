"""Provider-agnostic LLM adapter: OpenAI-compatible client + disk cache + rate limit + JSON repair.

Any OpenAI-compatible endpoint works (Gemini, Groq, OpenRouter, Anthropic's compat endpoint,
or an API Kuku provides): swap `base_url` + `model` + key in config.yaml or env.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import REPO_ROOT, ProviderSettings

PROMPT_DIR = Path(__file__).parent / "prompts"
T = TypeVar("T", bound=BaseModel)

SYSTEM = (
    "You are a senior promo editor for vertical microdrama shows. "
    "Follow the instructions exactly and reply with a single JSON object only, no prose, no markdown fences."
)


class LLMError(RuntimeError):
    """The model could not produce a valid response (after retry/repair)."""


class QuotaError(LLMError):
    """Provider quota or rate limit exhausted after backoff."""


@dataclass
class Meter:
    calls: int = 0
    cache_hits: int = 0
    repairs: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    by_prompt: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def load_prompt(name: str) -> tuple[str, str]:
    """Return (version_tag, template) for the newest prompts/<name>.vN.md."""
    files = sorted(PROMPT_DIR.glob(f"{name}.v*.md"), key=lambda p: int(re.search(r"\.v(\d+)\.md$", p.name).group(1)))
    if not files:
        raise FileNotFoundError(f"no prompt file for '{name}' in {PROMPT_DIR}")
    f = files[-1]
    return f.stem, f.read_text(encoding="utf-8")


def render(template: str, variables: dict[str, Any]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in variables:
            raise KeyError(f"prompt variable '{key}' not provided")
        v = variables[key]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


def extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


class LLMClient:
    def __init__(self, settings: ProviderSettings, llm_cfg: dict[str, Any] | None = None,
                 cache_dir: str | Path | None = None, client: Any = None):
        self.settings = settings
        self.cfg = llm_cfg or {}
        self.cache_dir = Path(cache_dir) if cache_dir else REPO_ROOT / ".cache" / "llm"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.meter = Meter()
        self.prompt_versions: dict[str, str] = {}
        self._client = client
        self._last_call = 0.0
        self._json_mode = True

    # ---- plumbing -------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            if not self.settings.api_key:
                raise LLMError(
                    f"No API key for provider '{self.settings.name}'. Set it in .env (see .env.example) "
                    "or run with --mode rules (no AI needed)."
                )
            from openai import OpenAI
            self._client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url,
                                  timeout=self.cfg.get("timeout_s", 120), max_retries=0)
        return self._client

    def model_for(self, role: str) -> str:
        if role == "judge" and self.settings.judge_model:
            return self.settings.judge_model
        if not self.settings.model:
            raise LLMError(f"No model configured for provider '{self.settings.name}'")
        return self.settings.model

    def _throttle(self):
        gap = 60.0 / max(self.settings.rpm, 0.1)
        wait = self._last_call + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _raw_call(self, model: str, messages: list[dict]) -> str:
        retries = int(self.cfg.get("max_retries", 3))
        delay = 4.0
        for attempt in range(retries + 1):
            self._throttle()
            t0 = time.monotonic()
            try:
                kwargs = dict(model=model, messages=messages, temperature=self.cfg.get("temperature", 0.3))
                if self._json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # provider errors vary; classify by status/message
                status = getattr(exc, "status_code", None)
                msg = str(exc)
                if status == 400 and self._json_mode and "response_format" in msg.lower():
                    self._json_mode = False
                    continue
                transient = status in (408, 409, 429, 500, 502, 503, 504) or exc.__class__.__name__ in (
                    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError")
                if transient and attempt < retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                self.meter.failures += 1
                if status == 429:
                    raise QuotaError(f"rate limit / quota exhausted for {model}: {msg[:300]}") from exc
                raise LLMError(f"{model} call failed: {msg[:300]}") from exc
            finally:
                self.meter.seconds += time.monotonic() - t0
            self.meter.calls += 1
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self.meter.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.meter.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            content = resp.choices[0].message.content or ""
            return content
        raise LLMError("unreachable")

    # ---- public ---------------------------------------------------------
    def complete_json(self, prompt_name: str, variables: dict[str, Any], schema: type[T], role: str = "main") -> T:
        version, template = load_prompt(prompt_name)
        self.prompt_versions[prompt_name] = version
        prompt = render(template, variables)
        model = self.model_for(role)
        key = hashlib.sha256(json.dumps([model, version, prompt], ensure_ascii=False).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                self.meter.cache_hits += 1
                return schema.model_validate(cached["parsed"])
            except (ValidationError, KeyError, json.JSONDecodeError):
                path.unlink(missing_ok=True)

        self.meter.by_prompt[prompt_name] = self.meter.by_prompt.get(prompt_name, 0) + 1
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        raw = self._raw_call(model, messages)
        try:
            parsed = schema.model_validate(extract_json(raw))
        except (ValidationError, json.JSONDecodeError, ValueError) as err:
            self.meter.repairs += 1
            messages += [
                {"role": "assistant", "content": raw[:6000]},
                {"role": "user", "content": f"That reply was not valid for the required JSON shape. Error: {str(err)[:800]}\n"
                                            "Reply again with the corrected JSON object only."},
            ]
            raw = self._raw_call(model, messages)
            try:
                parsed = schema.model_validate(extract_json(raw))
            except (ValidationError, json.JSONDecodeError, ValueError) as err2:
                self.meter.failures += 1
                raise LLMError(f"{prompt_name}: invalid JSON after repair: {str(err2)[:300]}") from err2
        path.write_text(json.dumps({"model": model, "prompt_version": version, "parsed": parsed.model_dump()},
                                   ensure_ascii=False), encoding="utf-8")
        return parsed
