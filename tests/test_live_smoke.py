"""
tests/test_live_smoke.py

Live API sanity check — makes ONE real call to the Gemini API.

Run ONLY when explicitly requested (e.g. before a demo to verify the key works):
    pytest tests/test_live_smoke.py -m live_api

This test is EXCLUDED from the default pytest run via pyproject.toml addopts.
"""

from __future__ import annotations

import pytest

from app.agents.llm_client import call_llm, LLMUnavailableError


@pytest.mark.live_api
def test_gemini_api_key_is_alive():
    """
    Confirm the configured Gemini API key can reach the model and returns
    a non-empty string.  This is the only test permitted to touch the network.
    """
    system_prompt = "You are a simple test assistant. Reply only with valid JSON."
    user_message = '{"ping": true}'

    try:
        result = call_llm(system_prompt, user_message)
    except LLMUnavailableError as exc:
        if "429" in str(exc) or "rate" in str(exc).lower():
            pytest.skip(f"API key quota exhausted (429) — add more keys to GEMINI_API_KEYS or rerun later. {exc}")
        pytest.fail(f"Gemini API unreachable or key invalid: {exc}")

    assert isinstance(result, str), "call_llm must return a string"
    assert len(result) > 0, "call_llm must return a non-empty response"
