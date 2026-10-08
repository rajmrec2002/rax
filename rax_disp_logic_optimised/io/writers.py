"""
File writing – CSV, XLSX.
"""

import os
import time
import logging
import pandas as pd

from .readers import ext

logger = logging.getLogger(__name__)


def write_table_file(
    df: pd.DataFrame,
    path: str,
    index: bool = False,
    header: bool = True,
    **kwargs,
) -> None:
    """Write CSV or XLSX transparently."""
    e = ext(path)
    if e in ('.xlsx', '.xlsm', '.xls'):
        df.to_excel(path, index=index, header=header, engine='openpyxl', **kwargs)
    else:
        df.to_csv(path, index=index, header=header, **kwargs)
    logger.debug("Wrote %d rows to '%s'", len(df), path)


def build_output_path(save_dir: str, station: str, tag: str, ext_str: str) -> str:
    """
    Construct a dated output path.
    Result: <save_dir>/STN_<station>_<tag>_<YYYYMMDD><ext>
    """
    date_str = time.strftime('%Y%m%d')
    filename = f"STN_{station}_{tag}_{date_str}{ext_str}"
    return os.path.join(save_dir, filename)
