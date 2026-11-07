"""Ollama chat adapter with schema-constrained JSON output, timeouts and usage capture."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

import requests

from blackbox.schemas import RunConfig


class ModelError(RuntimeError):
    """Transport-level failure: timeout, HTTP error, server unavailable."""


@dataclass
class ModelReply:
    text: str
    parsed: Optional[dict[str, Any]]
    parse_error: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    seconds: float
    done_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _tokenizer():
    from transformers import AutoTokenizer  # heavy import, deferred

    return AutoTokenizer.from_pretrained("Qwen/Qwen3-4B")


def count_tokens(text: str) -> int:
    """Exact Qwen3 token count; falls back to a conservative estimate if the tokenizer is unavailable."""
    try:
        return len(_tokenizer().encode(text))
    except Exception:  # pragma: no cover - offline fallback
        return int(len(text) / 1.6) + 1


def model_digest(config: RunConfig) -> str:
    try:
        r = requests.post(f"{config.ollama_url}/api/show", json={"model": config.model}, timeout=10)
        r.raise_for_status()
        info = r.json()
        return info.get("digest") or info.get("details", {}).get("parent_model", "") or "unknown"
    except Exception:
        return "unavailable"


class OllamaAdapter:
    def __init__(self, config: RunConfig):
        self.config = config

    def chat(self, messages: list[dict[str, str]], schema: Optional[dict[str, Any]] = None,
             num_predict: Optional[int] = None) -> ModelReply:
        cfg = self.config
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "stream": False,
            "think": cfg.think,
            "options": {
                "temperature": cfg.temperature,
                "top_p": cfg.top_p,
                "seed": cfg.seed,
                "num_ctx": cfg.num_ctx,
                "num_predict": num_predict or cfg.num_predict,
            },
        }
        if schema is not None:
            body["format"] = schema
        start = time.perf_counter()
        try:
            resp = requests.post(f"{cfg.ollama_url}/api/chat", json=body, timeout=cfg.model_timeout_s)
        except requests.Timeout as exc:
            raise ModelError(f"model timeout after {cfg.model_timeout_s:.0f}s") from exc
        except requests.RequestException as exc:
            raise ModelError(f"model request failed: {exc}") from exc
        seconds = time.perf_counter() - start
        if resp.status_code != 200:
            raise ModelError(f"model HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = (data.get("message") or {}).get("content", "") or ""
        parsed, err = None, None
        if schema is not None:
            parsed, err = parse_json_object(text)
            if data.get("done_reason") == "length" and err:
                err = f"output cut at num_predict ({body['options']['num_predict']} tokens): {err}"
        return ModelReply(
            text=text,
            parsed=parsed,
            parse_error=err,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            seconds=seconds,
            done_reason=data.get("done_reason", ""),
            raw={k: data.get(k) for k in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")},
        )


def parse_json_object(text: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Parse a JSON object, tolerating code fences or leading prose; report why it failed."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate[candidate.find("{"):] if "{" in candidate else candidate
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        first, last = candidate.find("{"), candidate.rfind("}")
        if first == -1 or last <= first:
            return None, "no JSON object in model output"
        try:
            obj = json.loads(candidate[first:last + 1])
        except json.JSONDecodeError as exc:
            return None, f"invalid JSON: {exc.msg} at char {exc.pos}"
    if not isinstance(obj, dict):
        return None, f"expected a JSON object, got {type(obj).__name__}"
    return obj, None
