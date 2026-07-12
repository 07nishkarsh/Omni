"""
tests/test_llm_key_pool.py

Unit tests for the LLMKeyPool rotation logic in app/agents/llm_client.py.

All tests mock the httpx.Client — no network calls are made.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call

import httpx
import pytest

from app.agents.llm_client import LLMKeyPool, LLMUnavailableError, call_llm


# ── LLMKeyPool unit tests ─────────────────────────────────────────────────────

class TestLLMKeyPool:
    def test_next_available_returns_first_key_when_none_cooling(self):
        pool = LLMKeyPool(["k1", "k2", "k3"])
        assert pool.next_available_index() == 0

    def test_mark_exhausted_skips_that_key(self):
        pool = LLMKeyPool(["k1", "k2", "k3"])
        pool.mark_exhausted(0, cooldown_seconds=60)
        assert pool.next_available_index() == 1

    def test_all_exhausted_returns_none(self):
        pool = LLMKeyPool(["k1", "k2"])
        pool.mark_exhausted(0, cooldown_seconds=60)
        pool.mark_exhausted(1, cooldown_seconds=60)
        assert pool.next_available_index() is None

    def test_cooldown_expires(self):
        pool = LLMKeyPool(["k1"])
        pool.mark_exhausted(0, cooldown_seconds=0)  # instant expiry
        time.sleep(0.01)
        assert pool.next_available_index() == 0

    def test_all_exhausted_property(self):
        pool = LLMKeyPool(["k1", "k2"])
        assert not pool.all_exhausted()
        pool.mark_exhausted(0, cooldown_seconds=60)
        pool.mark_exhausted(1, cooldown_seconds=60)
        assert pool.all_exhausted()


# ── call_llm rotation integration tests ──────────────────────────────────────

def _make_response(status_code: int, text: str = "") -> MagicMock:
    """Build a fake httpx.Response-like object."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.request = MagicMock()
    resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    }
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=resp.request, response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_pool(keys: list[str]) -> LLMKeyPool:
    return LLMKeyPool(keys)


class TestCallLLMRotation:
    """
    Verifies that call_llm rotates to the next key on 429 and succeeds,
    without the caller ever knowing a rotation happened.
    """

    def test_key1_429_rotates_to_key2_and_succeeds(self, monkeypatch):
        """Key #0 returns 429, key #1 returns 200 — caller gets the result cleanly."""
        pool = _make_pool(["bad_key", "good_key"])

        call_count = 0

        def fake_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "bad_key" in url:
                return _make_response(429)
            return _make_response(200, '{"result": "ok"}')

        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = fake_post

        monkeypatch.setattr("app.agents.llm_client._pool", pool)
        monkeypatch.setattr("app.agents.llm_client.httpx.Client", lambda: mock_client)
        monkeypatch.setattr("app.agents.llm_client._jitter_sleep", lambda _: None)

        result = call_llm("system", "user")

        assert result == '{"result": "ok"}'
        assert call_count == 2  # attempted key 0 (429) + key 1 (success)
        # Key 0 should now be cooling down
        assert pool.cooldowns.get(0, 0) > time.time() - 1

    def test_all_keys_exhausted_raises_llm_unavailable(self, monkeypatch):
        """When every key returns 429, LLMUnavailableError is raised — no infinite loop."""
        pool = _make_pool(["key1", "key2"])

        def fake_post(url, **kwargs):
            return _make_response(429)

        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = fake_post

        monkeypatch.setattr("app.agents.llm_client._pool", pool)
        monkeypatch.setattr("app.agents.llm_client.httpx.Client", lambda: mock_client)
        monkeypatch.setattr("app.agents.llm_client._jitter_sleep", lambda _: None)

        with pytest.raises(LLMUnavailableError):
            call_llm("system", "user")

        # Both keys should be cooled down
        assert pool.all_exhausted()

    def test_non_rate_limit_error_does_not_rotate(self, monkeypatch):
        """A 400 bad request must fail fast and NOT try a second key."""
        pool = _make_pool(["key1", "key2"])
        call_count = 0

        def fake_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_response(400)

        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = fake_post

        monkeypatch.setattr("app.agents.llm_client._pool", pool)
        monkeypatch.setattr("app.agents.llm_client.httpx.Client", lambda: mock_client)
        monkeypatch.setattr("app.agents.llm_client._jitter_sleep", lambda _: None)

        with pytest.raises(LLMUnavailableError):
            call_llm("system", "user")

        # Should have only tried once — no rotation for non-429 errors
        assert call_count == 1
        # Key 0 should NOT be cooling down (it wasn't a quota error)
        assert pool.cooldowns.get(0, 0) <= time.time()

    def test_no_keys_configured_raises_immediately(self, monkeypatch):
        """Empty key pool raises LLMUnavailableError without touching the network."""
        monkeypatch.setattr("app.agents.llm_client._pool", LLMKeyPool([]))

        with pytest.raises(LLMUnavailableError, match="No API keys configured"):
            call_llm("system", "user")
