"""Analyst revisions and estimate momentum factor scoring."""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, score_factor


REVISIONS_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("eps_revision_30d", aliases=("eps_revision_30d", "eps_revision_1m", "eps_estimate_revision_1m", "eps_rev_1m")),
    MetricSpec("eps_revision_60d", aliases=("eps_revision_60d", "eps_revision_2m", "eps_rev_2m")),
    MetricSpec("eps_revision_90d", aliases=("eps_revision_90d", "eps_revision_3m", "eps_estimate_revision_3m", "eps_rev_3m")),
)


def score_revisions(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with revisions metric scores and ``revisions_score``.

    If no revisions fields are supplied, all rows remain at neutral 50.
    """

    return score_factor(frame, prefix="revisions", metrics=REVISIONS_METRICS, **kwargs)


score = score_revisions
