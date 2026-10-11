"""
Station settings files (~/.rax-logic/<STN>_<time>.txt).

Written by the GUI "Save Settings" button and read by the GUI, the
command-line client and the server, so one file describes a run
everywhere.  Format: one 'NN. KEY=value' per line.
"""

import re
from typing import Dict, List, Tuple

KEYS = [
    'TOC_FILE', 'SAVE_DIR', 'CHAINAGE_FILE', 'VV_FILE', 'STATION_NAME',
    'HS_A', 'END_A', 'HS_B', 'END_B', 'HS_C', 'END_C', 'HS_D', 'END_D',
    'BERTHING_LINES', 'VV_SQSH', 'CRISS_CROSS', 'ISO_PREFERRED', 'CC_MIN', 'CC_MAX', 'CC_BANDS',
    'SERVER_URL',
]


def read_settings(path: str) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f.read().splitlines():
            line = re.sub(r'^\d+\.\s*', '', line.strip())
            if '=' in line:
                k, _, v = line.partition('=')
                cfg[k.strip()] = v.strip()
    return cfg


def write_settings(path: str, cfg: Dict[str, str]) -> None:
    entries = [f'{k}={cfg.get(k, "")}' for k in KEYS]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(f'{i + 1:02d}. {e}' for i, e in enumerate(entries)))


def home_signal_texts(cfg: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """Settings -> (['S5,CO5', ...] x4, ['VIRAR', ...] x4)."""
    return ([cfg.get(f'HS_{g}', '') for g in 'ABCD'],
            [cfg.get(f'END_{g}', '') for g in 'ABCD'])
