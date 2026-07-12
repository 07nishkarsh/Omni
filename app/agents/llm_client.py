"""
app/agents/llm_client.py

The ONLY place in the codebase that calls the Gemini API directly.
All agents must route through call_llm() — never import httpx in an agent file.

Features:
  - Multi-key pool with transparent rotation on 429/rate-limit errors only
  - Per-key cooldown (60 s default) so an exhausted key is skipped, not retried
  - Client-side token-bucket rate limiter (LLM_CALLS_PER_MINUTE, default 10/min)
  - Bounded total attempts: len(pool) + 1 — never loops forever
  - LLMUnavailableError raised when all keys are cooling or all attempts fail
  - Key *index* logged for debugging; key *value* is NEVER logged
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.0-flash")
LLM_CALLS_PER_MINUTE: int = int(os.getenv("LLM_CALLS_PER_MINUTE", "10"))

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ── Key pool loading ──────────────────────────────────────────────────────────

def _load_keys() -> list[str]:
    """Load keys from GEMINI_API_KEYS (comma-sep) or fall back to LLM_API_KEY."""
    raw = os.getenv("GEMINI_API_KEYS", "").strip()
    if raw:
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            return keys
    # Single-key legacy fallback
    single = os.getenv("LLM_API_KEY", "").strip()
    return [single] if single else []


# ── Errors ────────────────────────────────────────────────────────────────────

class LLMUnavailableError(Exception):
    """
    Raised when no key can service the request (all exhausted or all failed).

    The orchestrator must catch this and route the transaction to human review
    with reason="LLM Unavailable". Never swallow or retry above this layer.
    """


# ── Key Pool ──────────────────────────────────────────────────────────────────

class LLMKeyPool:
    """Thread-safe pool of API keys with per-key cooldown tracking."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.cooldowns: dict[int, float] = {}   # key_index -> unix timestamp when usable again
        self._lock = threading.Lock()

    def next_available_index(self) -> int | None:
        """Return the index of the first key not currently cooling down, or None."""
        now = time.time()
        with self._lock:
            for i in range(len(self.keys)):
                if self.cooldowns.get(i, 0) <= now:
                    return i
        return None

    def mark_exhausted(self, index: int, cooldown_seconds: int = 60) -> None:
        """Put key at *index* on cooldown for *cooldown_seconds*."""
        with self._lock:
            self.cooldowns[index] = time.time() + cooldown_seconds
        log.warning("llm_client: key #%d rate-limited, cooling down for %ds", index, cooldown_seconds)

    def all_exhausted(self) -> bool:
        now = time.time()
        with self._lock:
            return all(self.cooldowns.get(i, 0) > now for i in range(len(self.keys)))


# ── Token-bucket rate limiter ─────────────────────────────────────────────────

class _TokenBucket:
    """Thread-safe token bucket for client-side rate limiting."""

    def __init__(self, calls_per_minute: int) -> None:
        self._capacity = calls_per_minute
        self._tokens = float(calls_per_minute)
        self._refill_rate = calls_per_minute / 60.0
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            if time.monotonic() >= deadline:
                raise LLMUnavailableError("Rate-limiter timeout: could not acquire token.")
            time.sleep(0.1)


# ── Module-level singletons (replaceable in tests) ───────────────────────────

_pool = LLMKeyPool(_load_keys())
_bucket = _TokenBucket(LLM_CALLS_PER_MINUTE)


# ── Public interface ──────────────────────────────────────────────────────────

def call_llm(system_prompt: str, user_message: str) -> str:
    """
    The ONLY function in the codebase that calls the Gemini API.

    Transparent key rotation: on a 429 / quota error, the current key is
    cooled down and the next available key is tried immediately.
    Non-rate-limit errors (400 bad request, 401 auth, etc.) fail fast without
    wasting another key's quota on the same broken request.

    Total attempts are capped at len(pool.keys) + 1.
    Raises LLMUnavailableError when all keys are exhausted or max attempts hit.
    """
    if not _pool.keys:
        raise LLMUnavailableError("No API keys configured. Set GEMINI_API_KEYS in .env.")

    _bucket.acquire()

    max_attempts = len(_pool.keys) + 1
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        key_index = _pool.next_available_index()

        if key_index is None:
            raise LLMUnavailableError(
                f"All {len(_pool.keys)} key(s) are rate-limited. "
                "Transaction must be routed to human review."
            )

        api_key = _pool.keys[key_index]
        url = f"{_GEMINI_BASE}/{LLM_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": user_message}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"responseMimeType": "application/json"},
        }

        log.debug("llm_client: attempt %d using key #%d", attempt + 1, key_index)

        try:
            with httpx.Client() as client:
                response = client.post(url, json=payload, timeout=30.0)

            if response.status_code == 429:
                # Rate-limit: cool this key and rotate to the next one
                _pool.mark_exhausted(key_index)
                last_exc = httpx.HTTPStatusError(
                    f"429 on key #{key_index}",
                    request=response.request,
                    response=response,
                )
                _jitter_sleep(attempt)
                continue  # try next key immediately

            # Non-rate-limit HTTP errors: fail fast, do NOT rotate keys
            response.raise_for_status()

            data = response.json()
            text: str = data["candidates"][0]["content"]["parts"][0]["text"]
            log.info("llm_client: success on key #%d", key_index)
            return text.strip()

        except httpx.HTTPStatusError as exc:
            # Already handled 429 above; any other 4xx/5xx fails fast
            raise LLMUnavailableError(f"LLM API error (key #{key_index}): {exc}") from exc
        except httpx.RequestError as exc:
            # Network error — not a key-rotation trigger
            last_exc = exc
            if attempt >= max_attempts - 1:
                raise LLMUnavailableError(
                    f"LLM network error after {max_attempts} attempts: {exc}"
                ) from exc
            _jitter_sleep(attempt)

    raise LLMUnavailableError(
        f"All {max_attempts} attempt(s) failed. Last error: {last_exc}"
    )


def _jitter_sleep(attempt: int) -> None:
    """Exponential backoff with ±0.5 s jitter: ~1 s, ~2 s, ~4 s."""
    base = 2 ** attempt
    time.sleep(max(0.0, base + random.uniform(-0.5, 0.5)))
