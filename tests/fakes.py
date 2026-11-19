"""A fake OpenAI-compatible client for tests: returns scripted replies, records calls."""
from __future__ import annotations

import json
from types import SimpleNamespace


class FakeStatusError(Exception):
    def __init__(self, status_code, msg="error"):
        super().__init__(msg)
        self.status_code = status_code


class FakeClient:
    def __init__(self, replies=None, responder=None):
        """replies: list of str | dict | Exception consumed in order; responder: fn(messages)->reply."""
        self.replies = list(replies or [])
        self.responder = responder
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.responder is not None:
            reply = self.responder(kwargs["messages"])
        else:
            reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if not isinstance(reply, str):
            reply = json.dumps(reply)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )
