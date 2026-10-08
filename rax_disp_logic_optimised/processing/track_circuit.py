"""
Track circuit formatting.
"""

import re
import pandas as pd

from ..core.constants import RE_TRACK_CIRCUIT

_RE_TC_SPLIT = re.compile(r'[,\s\^\n]+')


def tc_format_fn(toc: pd.DataFrame) -> pd.Series:
    """
    Extract and format track circuit entries from RT-TC, OV-TC, ISO-TC.
    Combines all three columns, splits by comma/whitespace, keeps only
    tokens matching the pattern (e.g. '229T', '03AT').
    """
    def _extract_tcs(row):
        combined = ', '.join(str(v) for v in row if str(v) not in ('', 'nan'))
        tokens = _RE_TC_SPLIT.split(combined)
        return ', '.join(
            t.upper() for t in tokens
            if t.strip() and RE_TRACK_CIRCUIT.match(t.strip())
        )

    return (
        toc[['RT-TC', 'OV-TC', 'ISO-TC']]
        .fillna('')
        .astype(str)
        .apply(_extract_tcs, axis=1)
        .astype('string')
    )
