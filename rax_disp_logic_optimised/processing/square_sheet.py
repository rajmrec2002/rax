"""
Square sheet operations – generation, vice-versa logic,
and reconstruction.
"""

import numpy as np
import pandas as pd


def sqsh_fn(toc: pd.DataFrame, frmto: str, lck: str) -> pd.DataFrame:
    """
    Build the locking square sheet from TOC.
    Marks 'X' at intersections where FROM-TO appears in lock column.
    """
    labels = toc[frmto]
    sqsh = pd.DataFrame(index=labels, columns=labels)

    for i in range(len(toc.index)):
        k = str(toc.iloc[i, toc.columns.get_loc(lck)])
        entries = [part.replace(' ', '') for part in k.split('\n')]
        for name in entries:
            if name:
                try:
                    j = sqsh.columns.get_loc(name)
                    sqsh.iat[i, j] = 'X'
                except KeyError:
                    pass

    return sqsh


def vice_versa(sqsh: pd.DataFrame) -> pd.DataFrame:
    """
    Apply vice-versa logic to square sheet:
      - '#' on diagonal (self)
      - '~' where one side is 'X' and other is empty
    """
    sq = sqsh.fillna('').copy()
    arr = sq.values.copy()
    l = min(arr.shape)
    sub = arr[:l, :l]
    np.fill_diagonal(sub, '#')
    diag = np.eye(l, dtype=bool)
    nonzero_off = (sub != '') & ~diag
    zero_off = (sub == '') & ~diag
    sub[nonzero_off & zero_off.T] = '~'
    arr[:l, :l] = sub
    return pd.DataFrame(arr, index=sq.index, columns=sq.columns)


def re_vice_versa(path: str) -> pd.DataFrame:
    """
    Reverse the vice-versa transformation for a saved square sheet.
    """
    from ..io.readers import read_table_file

    sq = read_table_file(path)
    sq = sq.fillna('').replace('~', '', regex=True)

    n_rows, n_cols = sq.shape
    l = min(n_rows, n_cols)

    for i in range(l):
        for j in range(l):
            x = str(sq.iat[i, j])
            y = str(sq.iat[j, i])

            if x == '#' or y == '#':
                continue

            xu, yu = x.upper(), y.upper()

            if (xu == 'Y' and yu != 'Y') or (xu != 'Y' and yu == 'Y'):
                sq.iat[i, j] = 'X'
                sq.iat[j, i] = 'X'
            elif (xu == 'X' and y == '') or (x == '' and yu == 'X'):
                sq.iat[i, j] = '~'
                sq.iat[j, i] = '~'

    idx_name = sq.index.name or 'FROM-TO'
    sq.insert(0, idx_name, sq.index)
    return sq


def re_vice_versa_cc(sqsh: pd.DataFrame) -> pd.DataFrame:
    """Reverse vice-versa for criss-cross square sheet."""
    sq = sqsh.fillna('')

    for x in sq.columns:
        sq[x] = sq[x].replace(r'\~', '', regex=True)

    n_rows = len(sq)
    n_cols = len(sq.columns)
    l = min(n_rows, n_cols)

    for i in range(l):
        for j in range(i, l):
            x = sq.iloc[i, j]
            y = sq.iloc[j, i]

            if x == '#' or y == '#':
                continue

            xu, yu = str(x).upper(), str(y).upper()

            if (xu == 'Y' and yu != 'Y') or (xu != 'Y' and yu == 'Y'):
                sq.iloc[i, j] = 'X'
                sq.iloc[j, i] = 'X'
            elif (xu == 'X' and yu == '') or (xu == '' and yu == 'X'):
                sq.iloc[i, j] = 'X'
                sq.iloc[j, i] = 'X'

    return sq


def new_lck_gen_frm_sqsh(
    ltoc: pd.DataFrame, sqsh: pd.DataFrame, new_lck: str,
) -> pd.DataFrame:
    """Generate lock column from square sheet after vice-versa."""
    arr = sqsh.fillna('').values
    col_names = np.array([str(c) for c in sqsh.columns])
    # Broadcast: keep column name where cell is 'X', else empty string
    marked = np.where(arr == 'X', col_names, '')
    ltoc[new_lck] = ['\n'.join(row).replace(' ', '') for row in marked]
    return ltoc


def new_disp_gen_frm_sqsh(
    ltoc: pd.DataFrame, sqsh: pd.DataFrame, new_disp: str,
) -> pd.DataFrame:
    """Generate dispensation column from square sheet after vice-versa."""
    sqsh = sqsh.fillna("").replace('~', '', regex=True)
    l = len(sqsh.index)
    disp = pd.Series(index=range(l), dtype="object")

    for i in range(l):
        cols_for_row = []
        for j in range(i, l):
            val = str(sqsh.iat[i, j]).strip()
            if i != j and val != 'X':
                cols_for_row.append(sqsh.columns[j])
            else:
                cols_for_row.append('')
        disp.iat[i] = '\n'.join(cols_for_row)

    ltoc[new_disp] = disp
    return ltoc
