"""Daily LP letter generation."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Sequence

from common.config import PROJECT_ROOT, ensure_project_path

COMPLIANCE_FOOTER = 'For research and paper-trading use only. Not investment advice.'


def generate_daily_lp_letter(summary: str | Sequence[str], *, letter_date: date | None = None, path: str | Path | None = None) -> Path:
    letter_date = letter_date or date.today()
    output = Path(path) if path is not None and Path(path).is_absolute() else ensure_project_path(path or f'output/reports/lp_letter_{letter_date.isoformat()}.md', PROJECT_ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    paragraphs = [summary] if isinstance(summary, str) else list(summary)
    lines = [
        '# Meridian Capital Partners',
        '',
        f'**Date:** {letter_date.isoformat()}  ',
        '**CONFIDENTIAL**',
        '',
        'Dear Limited Partner,',
        '',
        '\n\n'.join(str(paragraph) for paragraph in paragraphs),
        '',
        'Sincerely,',
        '',
        'Meridian Capital Partners Research Desk',
        '',
        '---',
        COMPLIANCE_FOOTER,
    ]
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return output
