import numpy as np
import pandas as pd

from rax_disp_logic_optimised.core.constants import ALL_TOC_COLS
from rax_disp_logic_optimised.processing.dispensation import disp_lit_fn
from rax_disp_logic_optimised.processing.locking import ixl_fn
from rax_disp_logic_optimised.processing.square_sheet import (
    new_disp_gen_frm_sqsh, new_lck_gen_frm_sqsh, sqsh_fn, vice_versa,
)
from rax_disp_logic_optimised.processing.toc_format import toc_format

NO_HS = [[''], [''], [''], ['']]
NO_END = ['', '', '', '']


def make_toc(rows):
    """Build a formatted TOC from a list of {column: value} dicts."""
    out = []
    for r in rows:
        row = {c: '' for c in ALL_TOC_COLS}
        row.update(r)
        row.setdefault('GN', r['FROM'])
        row['GN'] = row['GN'] or r['FROM']
        out.append(row)
    return pd.DataFrame(out, columns=ALL_TOC_COLS)


def run_pipeline(rows, hs=NO_HS, end=NO_END, l_no=18):
    """toc_format -> ixl_fn -> square sheet -> lock/disp -> literals (no criss-cross)."""
    toc = toc_format(make_toc(rows), hs, end, l_no)
    ltoc = ixl_fn(toc, hs, end, l_no)
    vv = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
    dl = new_disp_gen_frm_sqsh(new_lck_gen_frm_sqsh(ltoc, vv, 'NEW-LCK2'), vv, 'NEW-DSP2')
    lit = disp_lit_fn(dl, 'NEW-DSP2', hs, end, l_no, vv.replace('X', '', regex=True))
    return ltoc, vv, dl, lit


def allowed_pairs(dl):
    names = list(dl['FROM-TO'])
    pairs = set()
    for i, cell in enumerate(dl['NEW-DSP2']):
        for n in str(cell).split('\n'):
            if n.strip():
                pairs.add(frozenset((names[i], n.strip())))
    return pairs


def cell(vv, a, b):
    return vv.loc[a, b]


def conflicts(vv, a, b):
    return np.isin([cell(vv, a, b), cell(vv, b, a)], ['X', '~']).any()
