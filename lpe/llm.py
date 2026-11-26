"""Minimal chat-model clients with an on-disk cache.

Backends (selected by a `provider:model` string):
  anthropic:<model>   Anthropic Messages API, key in ANTHROPIC_API_KEY
  openai:<model>      any OpenAI-compatible chat endpoint, key in OPENAI_API_KEY,
                      base URL in OPENAI_BASE_URL (default https://api.openai.com/v1)
  mock:<name>         deterministic offline stub for tests

Every call is cached by a hash of (model, system, messages, temperature, sample index),
so re-running an experiment is free and reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cachefile import atomic_write_text


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LLM:
    def __init__(self, spec: str, cache_dir: Path | None = Path(".cache/llm"), max_tokens: int = 4096):
        if ":" not in spec:
            raise ValueError(f"model spec must look like provider:model, got {spec!r}")
        self.provider, self.model = spec.split(":", 1)
        self.spec = spec
        self.cache_dir = cache_dir
        self.max_tokens = max_tokens
        self.usage = Usage()
        self._lock = threading.Lock()  # usage counters are shared by worker threads (`run --jobs`)
        self._mock: Callable[[str, list[dict]], str] | None = None
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def set_mock(self, fn: Callable[[str, list[dict]], str]) -> None:
        self._mock = fn

    def chat(self, system: str, user: str, temperature: float = 0.0, sample: int = 0) -> str:
        messages = [{"role": "user", "content": user}]
        key = hashlib.sha256(
            json.dumps([self.spec, system, messages, temperature, sample, self.max_tokens], ensure_ascii=False).encode()
        ).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir and self.provider != "mock" else None
        if path and path.exists():
            with self._lock:
                self.usage.cached += 1
            return json.loads(path.read_text(encoding="utf-8"))["text"]

        text, tin, tout = self._call(system, messages, temperature)
        with self._lock:
            self.usage.calls += 1
            self.usage.input_tokens += tin
            self.usage.output_tokens += tout
        if path:
            atomic_write_text(path, json.dumps({"spec": self.spec, "text": text}, ensure_ascii=False))
        return text

    def _call(self, system: str, messages: list[dict], temperature: float) -> tuple[str, int, int]:
        if self.provider == "mock":
            if not self._mock:
                raise RuntimeError("mock backend needs set_mock()")
            return self._mock(system, messages), 0, 0
        if self.provider == "anthropic":
            body = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": messages,
                "temperature": temperature,
            }
            base = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com")
            headers = {
                "x-api-key": _need("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            data = _post(f"{base}/v1/messages", body, headers)
            text = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
            u = data.get("usage", {})
            return text, u.get("input_tokens", 0), u.get("output_tokens", 0)
        if self.provider == "openai":
            body = {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, *messages],
                "temperature": temperature,
                "max_tokens": self.max_tokens,
            }
            base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            headers = {"authorization": f"Bearer {_need('OPENAI_API_KEY')}", "content-type": "application/json"}
            data = _post(f"{base}/chat/completions", body, headers)
            u = data.get("usage", {})
            return data["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        raise ValueError(f"unknown provider {self.provider!r}")


def _need(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(f"set {var} to use this backend")
    return value


def _post(url: str, body: dict, headers: dict, retries: int = 5) -> dict:
    payload = json.dumps(body).encode()
    for attempt in range(retries):
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                time.sleep(2**attempt * 5)
                continue
            raise RuntimeError(f"{url}: HTTP {err.code}: {err.read()[:500]!r}") from err
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2**attempt * 5)
                continue
            raise
    raise RuntimeError("unreachable")
