"""
TOC column normalization and preprocessing.

Handles: header merging, alias mapping, positional fallback,
cell cleaning, and row filtering – all vectorized.
"""

import re
import logging
import pandas as pd
from typing import List

from ..core.constants import (
    TOC_INPUT_COLS, TOC_BLANK_COLS, ALL_TOC_COLS, TOC_EXTRA_COLS,
    STR_COLS, PT_COLS, TC_COLS, DROP_COL_NAMES,
    COL_ALIASES, RE_CELL_NOISE, RE_TC_SUFFIX,
    EXCEL_ENGINES,
)
from ..utils.patterns import norm_col, strip_isolated_specials
from ..utils.text_cleaning import (
    has_alphanumeric_content,
    coerce_string_columns,
)

logger = logging.getLogger(__name__)


def normalize_toc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename input TOC columns to standard internal names, clean data,
    and add blank generated columns.

    Pass 0 – drop junk cols
    Pass 1 – alias matching
    Pass 2 – positional fill
    Pass 3 – GN cleanup (keep text before '+')
    Pass 4 – TC cleanup (remove /AXT suffix)
    Pass 5 – PT cleanup ('_' → '/')
    Pass 6 – add blank cols
    Pass 7 – final select/reorder
    Pass 8 – drop invalid rows
    """

    # ── Pass 0: Clean column names and drop junk ──────────────────────────
    rename_map = {
        col: strip_isolated_specials(str(col)) or str(col)
        for col in df.columns
        if col not in TOC_INPUT_COLS
    }
    if any(v != str(k) for k, v in rename_map.items()):
        df = df.rename(columns=rename_map)

    drop = [
        c for c in df.columns
        if (norm_col(str(c)) in DROP_COL_NAMES or norm_col(str(c)) == '')
        and c not in TOC_INPUT_COLS
    ]
    if drop:
        df = df.drop(columns=drop)

    # ── Pass 1: Alias rename ────────────────────────────────────────────
    alias_lookup = {norm_col(k): v for k, v in COL_ALIASES.items()}
    for col in TOC_INPUT_COLS:
        alias_lookup[norm_col(col)] = col
    for col in TOC_EXTRA_COLS:
        alias_lookup[norm_col(col)] = col

    rename = {}
    for col in df.columns:
        mapped = alias_lookup.get(norm_col(str(col)))
        if mapped and mapped not in rename.values():
            rename[col] = mapped
    df = df.rename(columns=rename)

    # ── Pass 2: Positional fallback ──────────────────────────────────────
    already = set(df.columns) & set(TOC_INPUT_COLS)
    missing = [c for c in TOC_INPUT_COLS if c not in already]
    if missing:
        _reserved = set(TOC_INPUT_COLS) | set(TOC_EXTRA_COLS)
        cols = list(df.columns)
        dst_iter = iter(missing)
        for idx, col in enumerate(cols):
            if col not in _reserved:
                try:
                    cols[idx] = next(dst_iter)
                except StopIteration:
                    break
        df.columns = cols

    # ── Coerce string columns ────────────────────────────────────────────
    df = coerce_string_columns(df, STR_COLS)

    # ── Pass 3: GN – extract leading signal identifier ──────────────────
    # MRN "SIG. RTE" cells contain annotations: "S3\n'b'" → "S3", "CO3" → "CO3"
    # Extract the leading letters+digits only, dropping any suffix annotation.
    if 'GN' in df.columns:
        df['GN'] = (
            df['GN'].str.split('+').str[0].str.strip()
            .str.extract(r'^([A-Za-z]+\d+)', expand=False)
            .fillna('')
            .replace({'nan': '', 'None': '', 'none': ''})
        )

    # ── Pass 4: TC – remove /AXT suffix (vectorized) ────────────────────
    for col in TC_COLS:
        if col in df.columns:
            df[col] = (
                df[col].str.replace(RE_TC_SUFFIX, '', regex=True).str.strip()
                .replace({'nan': '', 'None': ''})
            )

    # ── Pass 5: PT – replace '_' with '/' ───────────────────────────────
    for col in PT_COLS:
        if col in df.columns:
            df[col] = (
                df[col].str.replace('_', '/', regex=False)
                .replace({'nan': '', 'None': ''})
            )

    # ── Pass 6: Add blank generated columns ─────────────────────────────
    for col in ALL_TOC_COLS:
        if col not in df.columns:
            df[col] = ''

    # ── Pass 7: Select and reorder (standard cols + extra cols present) ──
    extra_present = [c for c in TOC_EXTRA_COLS if c in df.columns]
    df = df[ALL_TOC_COLS + extra_present].fillna('').reset_index(drop=True)
    for ec in TOC_EXTRA_COLS:
        if ec not in df.columns:
            df[ec] = ''

    # ── Pass 8: Drop invalid rows (vectorized) ───────────────────────────
    gn_upper = df['GN'].astype(str).str.strip().str.upper()
    un_upper = df['UN'].astype(str).str.strip().str.upper()
    mask = gn_upper.str.startswith('A') | un_upper.isin([''])
    dropped = mask.sum()
    if dropped:
        df = df[~mask].reset_index(drop=True)
        logger.info(
            "Dropped %d row(s) (GN starts with A, or UN is blank)",
            dropped,
        )

    return df


def preprocess_raw_toc(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert a raw (header=None) TOC DataFrame into one with standard column names.

    Step 1 – Find first DATA row (cell with both letters and digits)
    Step 2 – Merge header rows into composite labels
    Step 3 – Rename FROM … GATE/SIDING → standard 14 names
    Step 4 – Drop fully-blank rows
    Step 5 – Clear cells with no alphanumeric content
    """
    STD_14 = TOC_INPUT_COLS  # ['FROM', 'TO', ... 'YR']
    n_rows, n_cols = df.shape
    logger.debug("Preprocess raw: %d rows x %d cols", n_rows, n_cols)

    # ── Step 1: Find first data row ──────────────────────────────────────
    _s = df.fillna('').astype(str)
    _has_both = (
        _s.apply(lambda col: col.str.contains(r'[A-Za-z]', regex=True))
        & _s.apply(lambda col: col.str.contains(r'\d', regex=True))
    ).any(axis=1)

    data_row_pos = int(_has_both.to_numpy().argmax()) if _has_both.any() else None

    if data_row_pos is None:
        data_row_pos = 1
        header_rows = [0]
    elif data_row_pos == 0:
        header_rows = []
    else:
        header_rows = list(range(data_row_pos))

    logger.debug("First data row: %s, header rows: %s", data_row_pos, header_rows)

    # ── Step 2: Build merged column names ───────────────────────────────
    if header_rows:
        _hdr = (
            df.iloc[header_rows]
            .astype(str)
            .replace(r'^\s*(nan|none|NaN|None|NAN)\s*$', '', regex=True)
        )
        _raw = _hdr.apply(lambda col: ' '.join(v for v in col if v.strip()))
        merged_cols = [
            strip_isolated_specials(v) or f'_col{i}'
            for i, v in enumerate(_raw)
        ]
    else:
        merged_cols = [f'_col{c}' for c in range(n_cols)]

    data_df = df.iloc[data_row_pos:].copy().reset_index(drop=True)
    data_df.columns = merged_cols

    # ── Step 3: Locate FROM … GATE/SIDING and rename ────────────────────
    def _nm(s):
        return re.sub(r'[^A-Z0-9]', '', str(s).upper())

    from_idx = gate_idx = None
    for i, lbl in enumerate(merged_cols):
        n = _nm(lbl)
        if from_idx is None and 'FROM' in n:
            from_idx = i
        if 'GATESIDING' in n or (gate_idx is None and n == 'GATE'):
            gate_idx = i

    if from_idx is not None and gate_idx is not None and gate_idx > from_idx:
        span = gate_idx - from_idx + 1
        cols = list(data_df.columns)
        for offset in range(min(span, len(STD_14))):
            cols[from_idx + offset] = STD_14[offset]
        data_df.columns = cols

    # ── Step 4: Drop fully-blank rows ───────────────────────────────────
    before = len(data_df)
    data_df = (
        data_df.replace('', pd.NA).dropna(how='all').fillna('')
        .reset_index(drop=True)
    )
    logger.debug("Removed %d blank rows", before - len(data_df))

    # ── Step 5: Clear cells with no alphanumeric content ─────────────────
    _alnum_mask = data_df.astype(str).apply(
        lambda col: col.str.contains(r'[A-Za-z0-9]', regex=True, na=False)
    )
    data_df = data_df.where(_alnum_mask, other='')

    return data_df


def read_toc_file(path: str) -> pd.DataFrame:
    """
    Read a TOC input file (PDF / XLSX / XLS / CSV), normalize columns,
    and add blank generated columns.

    Non-PDF pipeline:
      Pass 1 – read raw (header=None)
      Pass 2 – preprocess_raw_toc: merge headers, rename, drop blanks
      Pass 3 – normalize_toc_columns: alias-map, add blanks, reorder
    """
    from .readers import ext, _read_pdf_toc

    e = ext(path)

    if e == '.pdf':
        df = _read_pdf_toc(path)
    else:
        logger.info("TOC Pass 1: Reading raw (header=None)...")
        if e in EXCEL_ENGINES:
            raw = pd.read_excel(path, header=None, engine=EXCEL_ENGINES[e])
        else:
            raw = pd.read_csv(path, header=None)
        logger.info("TOC Pass 1: %d rows x %d cols", len(raw), len(raw.columns))

        logger.info("TOC Pass 2: Merging headers...")
        df = preprocess_raw_toc(raw)
        logger.info("TOC Pass 2: %d rows x %d cols", len(df), len(df.columns))

    logger.info("TOC Pass 3: Normalizing columns...")
    mapped = normalize_toc_columns(df)
    logger.info("TOC final columns: %s", ' | '.join(mapped.columns.tolist()))
    logger.info("TOC rows: %d", len(mapped))
    logger.debug("TOC first 5 rows:\n%s", mapped.head().to_string(index=False))

    return mapped


def remap_extra_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply extra-column aliases and ensure CH/HG/HHG/DG exist.

    Called after read_table_file (already-formatted path) which skips
    normalize_toc_columns, so the aliases for 'y'→HG, 'yy'→HHG,
    'g'→DG, 'crank handle'→CH never ran.  Also a safe no-op when the
    columns are already correctly named (e.g. after read_toc_file).
    """
    # build lookup restricted to extra-col targets only
    alias_lookup = {
        norm_col(k): v
        for k, v in COL_ALIASES.items()
        if v in TOC_EXTRA_COLS
    }
    rename = {}
    for col in df.columns:
        if col in TOC_EXTRA_COLS:
            continue  # already correctly named
        mapped = alias_lookup.get(norm_col(str(col)))
        if mapped and mapped not in rename.values() and mapped not in df.columns:
            rename[col] = mapped
    if rename:
        df = df.rename(columns=rename)
    for ec in TOC_EXTRA_COLS:
        if ec not in df.columns:
            df[ec] = ''
    return df


def read_formatted_toc(path: str) -> pd.DataFrame:
    """
    Read an already-formatted TOC file using the actual header row.

    detect_toc_format() scans with header=None and may find the real
    column headers in row 1 (when row 0 is a title/blank row). But
    read_table_file() defaults to header=0, reading the wrong row as
    column names.  This function uses _find_header_row() to locate the
    real header row first, then reads with that offset, then remaps
    extra columns (Y→HG, YY→HHG, G→DG, crank handle→CH).
    """
    from .readers import _find_header_row, read_table_file, ext
    e = ext(path)
    hr = _find_header_row(path, e)
    df = read_table_file(path, header=hr)
    # A formatted TOC may be the output of an earlier run.  Its generated
    # columns (old locking, dispensation, texts …) must not leak into this
    # run, so they are cleared and recomputed.
    for col in TOC_BLANK_COLS + ['NEW-DSP2', 'NEW-DISP2']:
        if col in df.columns:
            df[col] = ''
    return remap_extra_cols(df)


def detect_toc_format(path: str) -> bool:
    """
    Auto-detect whether a TOC file is already formatted.

    Returns True (formatted) if at least 5 standard column names
    found in any candidate header row.
    """
    from .readers import ext

    e = ext(path)
    if e == '.pdf':
        return False

    try:
        if e in EXCEL_ENGINES:
            preview = pd.read_excel(
                path, header=None, nrows=5, engine=EXCEL_ENGINES[e]
            )
        else:
            preview = pd.read_csv(path, header=None, nrows=5)
    except Exception:
        return False

    _std_norm = {re.sub(r'[\s\-]', '', c).upper() for c in TOC_INPUT_COLS}

    for _, row in preview.iterrows():
        cells_norm = {
            re.sub(r'[\s\-]', '', str(v)).upper()
            for v in row if pd.notna(v) and str(v).strip()
        }
        raw_cells = {str(v).strip().upper() for v in row if pd.notna(v)}
        if 'FROM' not in cells_norm and 'FROM' not in raw_cells:
            continue
        if len(cells_norm & _std_norm) >= 5:
            return True

    return False
