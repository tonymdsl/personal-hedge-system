import pandas as pd

from app.services.analytics.metrics import calculate_metrics


def test_calculate_metrics_includes_required_market_statistics():
    prices = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=5),
            "close": [100.0, 102.0, 101.0, 105.0, 103.0],
        }
    )

    metrics = calculate_metrics(prices)

    assert metrics["cumulative_return"] == 0.03
    assert metrics["annualized_volatility"] > 0
    assert metrics["current_drawdown"] < 0
    assert metrics["max_drawdown"] < 0
    assert metrics["best_day"] > 0
    assert metrics["worst_day"] < 0
    assert "sharpe_ratio" in metrics
