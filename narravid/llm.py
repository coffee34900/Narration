"""Single adapter for local LLMs. Switching backend is a config change.

backend: ollama  -> POST {base_url}/api/chat with `format` = JSON schema (structured outputs)
backend: openai  -> POST {base_url}/v1/chat/completions with response_format json_schema
                    (LM Studio, llama.cpp server, any OpenAI-compatible local server)
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from jsonschema import Draft202012Validator


class LLMError(RuntimeError):
    pass


def ensure_local(url: str) -> str:
    """Refuse any LLM endpoint that is not on this machine or the local network.

    The pipeline must never send script text to a cloud API, so a remote base_url is
    an error rather than a setting.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host in ("localhost", ""):
        return url
    try:
        addrs = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addrs = {ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None)}
        except OSError as e:
            raise LLMError(f"Cannot resolve LLM host '{host}'") from e
    if not all(a.is_loopback or a.is_private or a.is_link_local for a in addrs):
        raise LLMError(f"LLM base_url {url} is not a local address. Only local models are allowed "
                       "(Ollama / LM Studio / llama.cpp on this machine or your LAN).")
    return url


class LLM:
    def __init__(self, cfg: dict):
        c = cfg["llm"]
        self.backend = c["backend"]
        self.model = c["model"]
        self.base_url = ensure_local(c["base_url"].rstrip("/"))
        self.temperature = float(c.get("temperature", 0.2))
        self.timeout = float(c.get("timeout_s", 600))
        self.think = bool(c.get("think", False))
        self.num_ctx = int(c.get("num_ctx", 4096))
        # layers to place on the GPU (Ollama). 99 = all: on the 4 GB RTX 3050 Ti this makes
        # qwen3.5:4b run 100% on the GPU, ~4x faster than Ollama's default 50/50 split.
        self.num_gpu = c.get("num_gpu", 99)
        self.max_retries = int(c.get("max_retries", 3))
        if not self.model:
            raise LLMError("No LLM model configured (llm.model in config.yaml). Open the Models panel.")

    def with_model(self, model: str) -> "LLM":
        """Same settings, different local model (used to escalate hard scenes)."""
        import copy
        other = copy.copy(self)
        other.model = model
        return other

    # ------------------------------------------------------------------ transport
    def _post(self, url: str, payload: dict) -> dict:
        ensure_local(url)
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"{self.backend} HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from e
        except urllib.error.URLError as e:
            hint = "Is Ollama running? (start the Ollama app)" if self.backend == "ollama" else \
                "Is the LM Studio / llama.cpp server running?"
            raise LLMError(f"Cannot reach {url}: {e.reason}. {hint}") from e

    def chat(self, messages: list[dict], schema: dict | None = None, temperature: float | None = None) -> str:
        t = self.temperature if temperature is None else temperature
        if self.backend == "ollama":
            payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False,
                                       "options": {"temperature": t, "num_ctx": self.num_ctx}}
            if not self.think:
                payload["think"] = False
            if schema:
                payload["format"] = schema
            if self.num_gpu not in (None, "", "auto"):
                payload["options"]["num_gpu"] = int(self.num_gpu)
            try:
                out = self._post(f"{self.base_url}/api/chat", payload)
            except LLMError as e:
                if "num_gpu" not in payload["options"]:
                    raise
                # GPU out of memory (or similar): fall back to Ollama's own CPU/GPU split, for good
                self.num_gpu = None
                del payload["options"]["num_gpu"]
                out = self._post(f"{self.base_url}/api/chat", payload)
            return out.get("message", {}).get("content", "")
        if self.backend == "openai":
            payload = {"model": self.model, "messages": messages, "temperature": t, "stream": False}
            if schema:
                payload["response_format"] = {"type": "json_schema",
                                              "json_schema": {"name": "output", "schema": schema, "strict": True}}
            out = self._post(f"{self.base_url}/v1/chat/completions", payload)
            return out["choices"][0]["message"]["content"]
        raise LLMError(f"Unknown llm.backend '{self.backend}' (ollama | openai)")

    # ------------------------------------------------------------------ structured
    def json(self, messages: list[dict], schema: dict, validate=None, log=None) -> dict:
        """Ask for JSON matching `schema`; on invalid output, feed the errors back and retry.

        `validate(obj) -> list[str]` adds semantic checks beyond the JSON schema.
        """
        validator = Draft202012Validator(schema)
        msgs = list(messages)
        last_errors: list[str] = []
        for attempt in range(1, self.max_retries + 2):
            raw = self.chat(msgs, schema=schema)
            obj, errors = None, []
            try:
                obj = json.loads(_strip_fences(raw))
            except json.JSONDecodeError as e:
                errors = [f"not valid JSON: {e}"]
            if obj is not None:
                errors = [f"{'/'.join(map(str, er.path)) or '(root)'}: {er.message}"
                          for er in validator.iter_errors(obj)]
                if not errors and validate:
                    errors = validate(obj)
            if not errors:
                return obj
            last_errors = errors
            if log:
                log(f"LLM attempt {attempt}: {len(errors)} problem(s): {errors[:3]}")
            msgs = list(messages) + [
                {"role": "assistant", "content": raw[:6000]},
                {"role": "user", "content": "Your JSON has these problems:\n- " + "\n- ".join(errors[:12]) +
                 "\nReturn the corrected JSON only."},
            ]
        raise LLMError(f"LLM output still invalid after {self.max_retries + 1} attempts: {last_errors[:5]}")


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^<think>.*?</think>", "", s, flags=re.S).strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, flags=re.S)
    return m.group(1) if m else s
