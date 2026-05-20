import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Experiments.multi_model_ipo.prompt_orchestration import get_prompt_data


def test_adanos_sentiment_context_is_disabled_without_key(monkeypatch):
    monkeypatch.setattr(get_prompt_data, "ADANOS_API_KEY", None)

    output = get_prompt_data.get_adanos_sentiment_context(["AAPL"])

    assert "ADANOS_SENTIMENT_START" in output
    assert "ADANOS_API_KEY is not configured" in output
    assert "ADANOS_SENTIMENT_END" in output


def test_adanos_sentiment_context_formats_available_sources(monkeypatch):
    calls = []

    def fake_request(platform, ticker):
        calls.append((platform, ticker))
        if platform == "reddit":
            return {
                "found": True,
                "sentiment_score": 0.31,
                "buzz_score": 64.2,
                "mentions": 42,
                "bullish_pct": 52,
                "bearish_pct": 17,
                "trend": "rising",
            }
        return {"found": False}

    monkeypatch.setattr(get_prompt_data, "ADANOS_API_KEY", "sk_live_test")
    monkeypatch.setattr(get_prompt_data, "_request_adanos_json", fake_request)

    output = get_prompt_data.get_adanos_sentiment_context(["aapl", "AAPL", "msft"])

    assert "TICKER=AAPL | SOURCE=reddit | SENTIMENT=0.31" in output
    assert "TICKER=MSFT | SOURCE=reddit | SENTIMENT=0.31" in output
    assert ("reddit", "AAPL") in calls
    assert calls.count(("reddit", "AAPL")) == 1
