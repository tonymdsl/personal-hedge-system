from __future__ import annotations

import pandas as pd

from factors.common import sector_percentile_rank
from factors.growth import GROWTH_METRICS
from factors.insider import INSIDER_METRICS
from factors.institutional import INSTITUTIONAL_METRICS
from factors.revisions import REVISIONS_METRICS
from factors.short_interest import SHORT_INTEREST_METRICS
from factors.value import score_value
from factors.revisions import score_revisions
from factors.regime_weights import apply_regime_weights
from factors.composite import score_composite
from factors.crowding import daily_factor_return_spreads, detect_crowding
from run_scoring import build_crowding_warnings, score_all_factors


def test_sector_percentile_rank_stays_inside_sector() -> None:
    frame = pd.DataFrame({'ticker': ['A', 'B', 'C', 'D'], 'gics_sector': ['Tech', 'Tech', 'Energy', 'Energy'], 'metric': [1, 3, 10, 20]})
    scores = sector_percentile_rank(frame, 'metric')
    assert list(scores) == [0.0, 100.0, 0.0, 100.0]


def test_sector_percentile_rank_uses_date_cross_sections_when_available() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-01-02", "2026-01-02", "2026-01-03", "2026-01-03"],
            "ticker": ["A", "B", "A", "B"],
            "gics_sector": ["Tech", "Tech", "Tech", "Tech"],
            "metric": [1, 3, 3, 1],
        }
    )

    scores = sector_percentile_rank(frame, "metric")

    assert list(scores) == [0.0, 100.0, 100.0, 0.0]


def test_value_inverts_ev_to_ebitda() -> None:
    frame = pd.DataFrame({
        'ticker': ['A', 'B'], 'gics_sector': ['Tech', 'Tech'],
        'enterprise_value': [100, 100], 'ebitda_ttm': [10, 5],
        'market_cap': [100, 100], 'net_income': [10, 8], 'total_equity': [50, 40],
        'free_cash_flow_ttm': [12, 8], 'revenue_ttm': [100, 80],
    })
    scored = score_value(frame)
    assert scored.loc[0, 'value_ev_to_ebitda_score'] > scored.loc[1, 'value_ev_to_ebitda_score']


def test_revisions_neutral_until_snapshots_exist() -> None:
    frame = pd.DataFrame({'ticker': ['A', 'B'], 'gics_sector': ['Tech', 'Tech']})
    scored = score_revisions(frame)
    assert set(scored['revisions_score']) == {50.0}


def test_regime_weights_boost_momentum_in_low_vix() -> None:
    low = apply_regime_weights(vix=10)
    high = apply_regime_weights(vix=30)
    assert low['momentum'] > high['momentum']
    assert low['momentum'] == 0.28
    assert low['value'] == 0.10
    assert high['quality'] == 0.28
    assert high['value'] == 0.22
    assert high['momentum'] == 0.10
    assert abs(sum(low.values()) - 1.0) < 1e-9


def test_composite_flags_top_and_bottom_quintiles() -> None:
    frame = pd.DataFrame({
        'ticker': [f'T{i}' for i in range(10)],
        'gics_sector': ['Tech'] * 10,
        'momentum_score': list(range(10, 110, 10)),
        'quality_score': list(range(10, 110, 10)),
        'value_score': list(range(10, 110, 10)),
        'revisions_score': [50] * 10,
        'insider_score': [50] * 10,
        'growth_score': [50] * 10,
        'short_interest_score': [50] * 10,
        'institutional_score': [50] * 10,
    })
    scored = score_composite(frame)
    assert scored['long_candidate'].sum() >= 2
    assert scored['short_candidate'].sum() >= 2
    assert scored['composite_score'].between(0, 100).all()


def test_prompt_metric_counts_are_layer_two_counts() -> None:
    assert len(GROWTH_METRICS) == 5
    assert len(REVISIONS_METRICS) == 3
    assert len(SHORT_INTEREST_METRICS) == 3
    assert len(INSIDER_METRICS) == 3
    assert len(INSTITUTIONAL_METRICS) == 3


def test_run_scoring_scores_all_eight_factors_before_composite() -> None:
    frame = pd.DataFrame(
        {
            "ticker": [f"T{i}" for i in range(10)],
            "gics_sector": ["Tech"] * 10,
            "return_6m": list(range(10)),
            "eps_ttm": list(range(10, 20)),
            "price": [100] * 10,
            "book_value_per_share": list(range(10, 20)),
            "gross_margin": list(range(10, 20)),
            "revenue_growth": list(range(10)),
            "eps_revision_30d": list(range(10)),
            "short_percent_float": list(range(10)),
            "insider_net_purchases": list(range(10)),
            "tracked_fund_holder_count": list(range(10)),
        }
    )

    scored = score_all_factors(frame)

    for column in [
        "momentum_score",
        "value_score",
        "quality_score",
        "growth_score",
        "revisions_score",
        "short_interest_score",
        "insider_score",
        "institutional_score",
        "composite_score",
    ]:
        assert column in scored.columns
        assert scored[column].between(0, 100).all()


def test_daily_factor_return_spreads_builds_top_minus_bottom_by_date() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-01-02"] * 6 + ["2026-01-03"] * 6,
            "ticker": [f"T{i}" for i in range(12)],
            "momentum_score": [10, 20, 30, 80, 90, 100, 15, 25, 35, 85, 95, 100],
            "value_score": [100, 90, 80, 30, 20, 10, 100, 95, 85, 35, 25, 15],
            "forward_return": [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03, -0.04, -0.02, -0.01, 0.02, 0.03, 0.04],
        }
    )

    spreads = daily_factor_return_spreads(frame, ["momentum_score", "value_score"])

    assert set(spreads["factor"]) == {"momentum", "value"}
    first_momentum = spreads[(spreads["date"] == "2026-01-02") & (spreads["factor"] == "momentum")].iloc[0]
    first_value = spreads[(spreads["date"] == "2026-01-02") & (spreads["factor"] == "value")].iloc[0]
    assert round(first_momentum["spread"], 4) == 0.05
    assert round(first_value["spread"], 4) == -0.05
    assert first_momentum["long_count"] == 2
    assert first_momentum["short_count"] == 2


def test_detect_crowding_flags_pairwise_correlation_deviation_from_baseline() -> None:
    dates = pd.date_range("2026-01-01", periods=65, freq="D")
    rows = []
    for idx, date in enumerate(dates):
        spread = idx / 100.0
        rows.append({"date": date, "factor": "momentum", "spread": spread})
        rows.append({"date": date, "factor": "value", "spread": spread + 0.01})

    warnings = detect_crowding(
        pd.DataFrame(rows),
        window=60,
        min_periods=60,
        deviation_threshold=0.4,
    )

    pair_warnings = [
        warning
        for warning in warnings
        if warning.get("warning") == "factor_correlation_deviation"
        and warning.get("factor_a") == "momentum"
        and warning.get("factor_b") == "value"
    ]
    assert pair_warnings
    assert pair_warnings[-1]["baseline"] == -0.3
    assert pair_warnings[-1]["correlation"] > 0.99
    assert pair_warnings[-1]["deviation"] > 1.2


def test_run_scoring_builds_crowding_warnings_from_daily_factor_history() -> None:
    rows = []
    for day_idx, date in enumerate(pd.date_range("2026-01-01", periods=65, freq="D")):
        for ticker_idx in range(10):
            score = ticker_idx * 10
            rows.append(
                {
                    "date": date,
                    "ticker": f"T{ticker_idx}",
                    "momentum_score": score,
                    "value_score": score,
                    "forward_return": (ticker_idx - 4.5) * (day_idx + 1) / 1000.0,
                }
            )

    warnings = build_crowding_warnings(
        pd.DataFrame(rows),
        {"scoring": {"crowding": {"rolling_window_days": 60, "correlation_deviation_threshold": 0.4}}},
    )

    assert any(warning.get("warning") == "factor_correlation_deviation" for warning in warnings)
