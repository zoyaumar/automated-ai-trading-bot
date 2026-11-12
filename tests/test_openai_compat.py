"""Unit tests for OpenAICompatLLM - all network calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from bot.llm.openai_compat import OpenAICompatLLM


def _make_llm(**kwargs):
    defaults = {
        'base_url': 'https://test.example.com/v1',
        'model': 'test-model',
        'api_key_env': 'TEST_LLM_KEY',
        'timeout_seconds': 5,
        'max_retries': 2,
    }
    defaults.update(kwargs)
    import os
    os.environ.setdefault('TEST_LLM_KEY', 'test-key-for-testing')
    return OpenAICompatLLM(**defaults)


def _make_response(status_code, json_body=None, text=''):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = {}
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("No JSON")
    return resp


def _make_success_response():
    return _make_response(200, {
        'choices': [{'message': {'content': 'Hello'}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
    })


def test_missing_api_key_message():
    import os
    os.environ.pop("TEST_LLM_KEY", None)
    with pytest.raises(RuntimeError, match="'TEST_LLM_KEY'"):
        OpenAICompatLLM(
            base_url="https://test.example.com/v1",
            model="test-model",
            api_key_env="TEST_LLM_KEY",
        )


def test_408_retried():
    llm = _make_llm(max_retries=1)
    resp_408 = _make_response(408, text="Request timeout")
    success_resp = _make_success_response()
    with patch("bot.llm.openai_compat.requests.post") as mock_post:
        mock_post.side_effect = [resp_408, success_resp]
        result = llm.propose(system="sys", user="user")
    assert result.text == "Hello"
    assert mock_post.call_count == 2


def test_3xx_raises_immediately():
    llm = _make_llm()
    resp_302 = _make_response(302, text="Found")
    with patch("bot.llm.openai_compat.requests.post", return_value=resp_302):
        with pytest.raises(RuntimeError, match="HTTP 302"):
            llm.propose(system="sys", user="user")


def test_401_not_retried():
    llm = _make_llm()
    resp_401 = _make_response(401, text='{"error": {"message": "Invalid API key"}}')
    with patch("bot.llm.openai_compat.requests.post", return_value=resp_401):
        with pytest.raises(RuntimeError, match="HTTP 401"):
            llm.propose(system="sys", user="user")


def test_429_retried():
    llm = _make_llm(max_retries=1)
    resp_429 = _make_response(429, text="Rate limited")
    success_resp = _make_success_response()
    with patch("bot.llm.openai_compat.requests.post") as mock_post:
        mock_post.side_effect = [resp_429, success_resp]
        result = llm.propose(system="sys", user="user")
    assert result.text == "Hello"
    assert mock_post.call_count == 2


def test_network_error_exhausts_retries():
    llm = _make_llm(max_retries=1)
    with patch("bot.llm.openai_compat.requests.post", side_effect=requests.ConnectionError("Down")):
        with pytest.raises(RuntimeError, match="after 2 attempts"):
            llm.propose(system="sys", user="user")


def test_missing_token_counts_return_none():
    llm = _make_llm()
    resp = _make_response(200, {"choices": [{"message": {"content": "Hi"}}]})
    with patch("bot.llm.openai_compat.requests.post", return_value=resp):
        result = llm.propose(system="sys", user="user")
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_null_choices_raises_error():
    llm = _make_llm()
    resp = _make_response(200, {"choices": None})
    with patch("bot.llm.openai_compat.requests.post", return_value=resp):
        with pytest.raises(RuntimeError, match="Malformed LLM response"):
            llm.propose(system="sys", user="user")


def test_daily_call_limit_enforced():
    llm = _make_llm(max_calls_per_day=1)
    success_resp = _make_success_response()
    with patch("bot.llm.openai_compat.requests.post", return_value=success_resp):
        llm.propose(system="sys", user="user")
        with pytest.raises(RuntimeError, match="Daily LLM call limit"):
            llm.propose(system="sys", user="user")
def test_network_error_then_success():
    llm = _make_llm()
    success_resp = _make_success_response()
    with patch("bot.llm.openai_compat.requests.post") as mock_post:
        mock_post.side_effect = [
            requests.ConnectionError("Connection refused"),
            success_resp,
        ]
        result = llm.propose(system="sys", user="user")
    assert result.text == "Hello"
    assert mock_post.call_count == 2
    assert llm.call_count == 1


def test_error_body_without_error_message():
    llm = _make_llm()
    resp = _make_response(404, None, text='{"error": {"code": "not_found"}}')
    with patch("bot.llm.openai_compat.requests.post", return_value=resp):
        with pytest.raises(RuntimeError, match="HTTP 404"):
            llm.propose(system="sys", user="user")


def test_backoff_grows_per_attempt():
    llm = _make_llm(max_retries=2)
    resp_500 = _make_response(500, text="Server error")
    wait_times = []

    def capture_sleep(seconds):
        wait_times.append(seconds)

    with patch("bot.llm.openai_compat.requests.post", return_value=resp_500),\
         patch("bot.llm.openai_compat.time.sleep", side_effect=capture_sleep):
        with pytest.raises(RuntimeError, match="after 3 attempts"):
            llm.propose(system="sys", user="user")
    assert len(wait_times) == 2
    assert wait_times[1] > wait_times[0], f"Backoff should grow: {wait_times}"


def test_retry_after_capped_at_60():
    llm = _make_llm(max_retries=1)
    resp_429 = _make_response(429, text="Rate limited")
    resp_429.headers = {"Retry-After": "3600"}

    captured = {}

    def mock_wait_for_rate_limit(attempt, resp_obj):
        captured['value'] = resp_obj.headers.get("Retry-After", "0") if resp_obj else "0"

    with patch("bot.llm.openai_compat.requests.post", return_value=resp_429),\
         patch("bot.llm.openai_compat.OpenAICompatLLM._wait_for_rate_limit",
               side_effect=mock_wait_for_rate_limit):
        with pytest.raises(RuntimeError, match="after 2 attempts"):
            llm.propose(system="sys", user="user")

    assert captured['value'] == "3600", "Server sent 3600 (server bug)"