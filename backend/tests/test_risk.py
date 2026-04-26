import pandas as pd

from app.services.analytics.risk import calculate_portfolio_risk


def test_calculate_portfolio_risk_returns_equal_weight_contributions_and_alerts():
    dates = pd.date_range("2024-01-01", periods=5)
    price_map = {
        "AAA": pd.DataFrame({"date": dates, "close": [100.0, 102.0, 98.0, 97.0, 96.0]}),
        "BBB": pd.DataFrame({"date": dates, "close": [50.0, 50.5, 51.0, 50.0, 49.0]}),
    }

    result = calculate_portfolio_risk(price_map)

    assert result["assumption"] == "equal_weight_no_real_positions"
    assert result["portfolio_volatility"] > 0
    assert result["portfolio_max_drawdown"] < 0
    assert result["current_portfolio_drawdown"] < 0
    assert len(result["risk_contribution"]) == 2
    assert sum(item["weight"] for item in result["risk_contribution"]) == 1.0
    assert "concentration_alerts" in result
    assert "drawdown_alerts" in result
