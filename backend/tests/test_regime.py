import pandas as pd

from app.services.analytics.regime import classify_market_regime


def test_classify_market_regime_returns_risk_on_with_positive_trend():
    dates = pd.date_range("2023-01-01", periods=260)
    spy = pd.DataFrame({"date": dates, "close": range(100, 360)})
    qqq = pd.DataFrame({"date": dates, "close": range(120, 380)})

    result = classify_market_regime({"SPY": spy, "QQQ": qqq})

    assert result["regime"] == "risk_on"
    assert result["confidence"] > 0
    assert result["evidence"]["spy_above_200dma"] is True
    assert result["evidence"]["qqq_above_200dma"] is True
    assert result["evidence"]["realized_volatility_below_threshold"] is True
    assert result["thresholds"]["max_volatility_for_risk_on"] == 0.25
    assert result["values"]["spy_price"] == 359.0
    assert result["values"]["qqq_200dma"] > 0


def test_classify_market_regime_returns_market_stress_for_large_drawdown():
    dates = pd.date_range("2023-01-01", periods=260)
    base = list(range(100, 350)) + [230] * 10
    spy = pd.DataFrame({"date": dates, "close": base})
    qqq = pd.DataFrame({"date": dates, "close": base})

    result = classify_market_regime({"SPY": spy, "QQQ": qqq})

    assert result["regime"] == "market_stress"
    assert result["evidence"]["drawdown_above_stress_threshold"] is False
    assert result["values"]["current_drawdown"] <= -0.10
