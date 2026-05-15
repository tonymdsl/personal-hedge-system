"""JARVIS-authored weekly commentary helpers."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Mapping


def should_generate_weekly_commentary(as_of: date, config: Mapping[str, object] | None = None) -> bool:
    reporting = (config or {}).get('reporting', {}) if isinstance(config, Mapping) else {}
    weekday = str(reporting.get('weekly_commentary_day', 'Friday')) if isinstance(reporting, Mapping) else 'Friday'
    return as_of.strftime('%A').lower() == weekday.lower()


def generate_weekly_commentary(snapshot: Mapping[str, object], *, as_of: date, path: str | Path = 'output/reports/weekly_commentary.md') -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '# JARVIS Weekly Commentary',
        '',
        f'**Week ending:** {as_of.isoformat()}',
        '',
        'JARVIS reviewed the local Meridian reporting snapshot and found the following paper-trading context:',
        '',
        '```json',
        json.dumps(dict(snapshot), indent=2, sort_keys=True, default=str),
        '```',
        '',
        'This commentary is generated from local project artifacts only and is not investment advice.',
    ]
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return output
