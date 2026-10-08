"""
Unified file reading with automatic engine selection, calamine preference,
and mtime-based read cache.
"""

import os
import importlib.util
import logging
import pandas as pd
from typing import Optional

from ..core.constants import EXCEL_ENGINES

logger = logging.getLogger(__name__)

# ── Calamine detection (checked once, cached) ─────────────────────────────
_CALAMINE: Optional[bool] = None

def _xlsx_engine() -> str:
    global _CALAMINE
    if _CALAMINE is None:
        _CALAMINE = importlib.util.find_spec('python_calamine') is not None
        if _CALAMINE:
            logger.debug("Using calamine engine for XLSX (fast read path)")
    return 'calamine' if _CALAMINE else 'openpyxl'


def _engine_for(e: str) -> str:
    """Pick the best available engine for extension e."""
    if e in ('.xlsx', '.xlsm'):
        return _xlsx_engine()
    return EXCEL_ENGINES.get(e, 'openpyxl')


# ── Read cache ────────────────────────────────────────────────────────────
_READ_CACHE: dict = {}
_CACHE_MAX = 8  # keep at most this many DataFrames in memory


def _cache_key(path: str, **kwargs) -> tuple:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return (os.path.abspath(path), mtime, repr(sorted(kwargs.items())))


def _cache_get(key: tuple) -> Optional[pd.DataFrame]:
    entry = _READ_CACHE.get(key)
    return entry.copy() if entry is not None else None


def _cache_put(key: tuple, df: pd.DataFrame) -> None:
    if len(_READ_CACHE) >= _CACHE_MAX:
        _READ_CACHE.pop(next(iter(_READ_CACHE)))
    _READ_CACHE[key] = df.copy()


def invalidate_read_cache() -> None:
    """Clear the read cache (call after writing a file that may be re-read)."""
    _READ_CACHE.clear()


# ── Public API ────────────────────────────────────────────────────────────

def ext(path: str) -> str:
    """Lowercase file extension."""
    s = str(path).strip().strip('"').strip("'")
    return os.path.splitext(s)[1].lower()


def read_table_file(path: str, **kwargs) -> pd.DataFrame:
    """
    Read CSV, XLSX, XLS, ODS transparently.

    Uses calamine engine for XLSX when python-calamine is installed
    (~10x faster than openpyxl for reads). Results are cached by path+mtime
    so repeated reads of the same unchanged file are free.
    """
    e = ext(path)
    key = _cache_key(path, **kwargs)
    cached = _cache_get(key)
    if cached is not None:
        logger.debug("Cache hit: '%s'", path)
        return cached

    logger.debug("Reading '%s' (ext=%s)", path, e)
    if e in ('.xlsx', '.xlsm', '.xls', '.ods'):
        engine = _engine_for(e)
        df = pd.read_excel(path, engine=engine, **kwargs)
    else:
        df = pd.read_csv(path, low_memory=False, **kwargs)

    _cache_put(key, df)
    return df


def read_chainage_file(path_ch: str) -> pd.DataFrame:
    """
    Read chainage file regardless of whether it has a header row.

    Checks if the first row's second cell is numeric; if not, treats
    that row as a label row and drops it.
    Returns DataFrame with columns ['GR_NO', 'GR_CH'].
    """
    e = ext(path_ch)
    if e in ('.xlsx', '.xlsm', '.xls', '.ods'):
        engine = _engine_for(e)
        df = pd.read_excel(path_ch, header=None, engine=engine)
    else:
        df = pd.read_csv(path_ch, header=None, low_memory=False)

    if len(df) > 0:
        try:
            float(df.iloc[0, 1])
        except (ValueError, TypeError):
            df = df.iloc[1:].reset_index(drop=True)

    df = df.iloc[:, :2].copy()
    df.columns = ['GR_NO', 'GR_CH']
    return df


def _read_pdf_toc(path: str) -> pd.DataFrame:
    """Extract TOC table from a PDF using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError(
            "PDF reading requires pdfplumber.\n"
            "Install with:  pip install pdfplumber"
        )

    raw_rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                raw_rows.extend(table)

    if not raw_rows:
        raise ValueError(f"No table data found in PDF: {path}")

    header_idx = None
    for i, row in enumerate(raw_rows):
        cells = [str(c).strip().upper() if c else '' for c in row]
        if 'FROM' in cells and 'TO' in cells:
            header_idx = i

    if header_idx is not None:
        header = [str(c).strip() if c else f'_col{i}'
                  for i, c in enumerate(raw_rows[header_idx])]
        data = raw_rows[header_idx + 1:]
    else:
        header = [f'_col{i}' for i in range(len(raw_rows[0]))]
        data = raw_rows

    df = pd.DataFrame(data, columns=header)
    df = df.replace('', pd.NA).dropna(how='all').fillna('').reset_index(drop=True)
    return df


def _find_header_row(path: str, e: str) -> int:
    """Scan first 10 rows to find the real column header row. Returns 0-based index."""
    try:
        if e in ('.xlsx', '.xlsm', '.xls', '.ods'):
            preview = pd.read_excel(path, header=None, nrows=10, engine=_engine_for(e))
        else:
            preview = pd.read_csv(path, header=None, nrows=10)

        for i, row in preview.iterrows():
            cells = {str(v).strip().upper() for v in row
                     if v is not None and str(v).strip()}
            if 'FROM' in cells and 'TO' in cells:
                return i
    except Exception:
        pass
    return 0


def get_default_output_ext(*paths) -> str:
    """Return preferred output extension derived from input file paths."""
    for path in paths:
        e = ext(path)
        if e in ('.csv', '.xlsx', '.xls', '.xlsm'):
            return '.xlsx' if e in ('.xls', '.xlsm') else e
    return '.csv'
