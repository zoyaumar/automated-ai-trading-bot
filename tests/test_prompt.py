"""Tests for prompt parsing and validation."""

from bot.prompt import parse_proposals, strip_code_fences, extract_json, build_prompt


def test_parse_valid_list() -> None:
    raw = '[{"action":"BUY","ticker":"AAPL","stop_loss_pct":5,"take_profit_pct":10,"reasoning":"test"}]'
    result = parse_proposals(raw)
    assert result.parsed_ok is True
    assert len(result.proposals) == 1
    assert result.proposals[0].ticker == "AAPL"


def test_parse_with_code_fences() -> None:
    raw = "```json\n[{\"action\":\"HOLD\",\"ticker\":\"MSFT\"}]\n```"
    result = parse_proposals(raw)
    assert result.parsed_ok is True
    assert result.proposals[0].action == "HOLD"


def test_parse_invalid_json() -> None:
    result = parse_proposals("not json at all")
    assert result.parsed_ok is False
    assert len(result.proposals) == 0


def test_parse_missing_fields_skipped() -> None:
    raw = '[{"action":"BUY"}, {"ticker":"AAPL","action":"CLOSE","stop_loss_pct":5,"take_profit_pct":10}]'
    result = parse_proposals(raw)
    assert len(result.proposals) == 1
    assert result.proposals[0].ticker == "AAPL"


def test_strip_code_fences() -> None:
    assert strip_code_fences("```json\n{}\n```") == "{}"
    assert strip_code_fences("just text") == "just text"


def test_build_prompt() -> None:
    prompt = build_prompt(
        system_prompt="You are an analyst.",
        market_block="AAPL: RSI=50",
        holdings_block="AAPL: 10 shares",
        news_block=None,
    )
    assert "You are an analyst." in prompt
    assert "AAPL: RSI=50" in prompt
    assert "AAPL: 10 shares" in prompt