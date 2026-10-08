"""Wording of the 3.47 / 5.16 dispensation texts."""
from helpers import run_pipeline

# Each allowed pair is described once, under the route that comes first in
# the TOC, so routes whose texts are checked must not be last.
ROWS = [
    # dispatched, overlap but no route points
    dict(FROM='S14', TO='S2', UN='14', **{'RT-TC': '14T', 'OV-SET': 'OV-2',
                                         'OV-PT-N': '131/132N', 'OV-TC': '131T'}),
    # dispatched, with isolation
    dict(FROM='S16', TO='S2', UN='16', **{'RT-PT-N': '161/162N', 'RT-TC': '161T',
                                         'ISO': '171N, 173/174N'}),
    # received (home signal S3, END 'VIRAR')
    dict(FROM='S3', TO='S21', UN='01D', **{'RT-PT-N': '101/102N', 'RT-PT-R': '103/104R',
                                          'RT-TC': '3T, 101T', 'OV-SET': 'OV1-21',
                                          'OV-PT-N': '113/114N', 'OV-TC': '113T'}),
    # dispatched, with route points
    dict(FROM='S12', TO='S2', UN='12', **{'RT-PT-N': '105/106N', 'RT-TC': '105T, 12T'}),
    # shunt route that can run alongside the others, with isolation
    dict(FROM='SH40', TO='SH42', UN='04D', **{'RT-PT-N': '151/152N', 'RT-TC': '151T',
                                             'ISO': '153N'}),
]
HS = [['S3_'], [''], [''], ['']]
END = ['VIRAR', '', '', '']


def _texts():
    _, _, _, lit = run_pipeline(ROWS, hs=HS, end=END)
    return lit.set_index('FROM-TO')


def test_no_doubled_or_missing_with():
    lit = _texts()
    alltext = '\n'.join(lit['MN-MOVT-LIT'].tolist() + lit['SUB-MOVT-LIT'].tolist()
                        + lit['SH-DISP-LIT'].tolist())
    assert alltext.strip()
    assert 'with with' not in alltext
    assert ' with,' not in alltext
    for line in alltext.split('\n'):
        assert ' route point/s' not in line or ' with route point/s' in line, line


def test_main_movement_wording():
    lit = _texts()
    mn = lit.at['S16_16_0', 'MN-MOVT-LIT']
    assert 'dispatched by taking off S16 up to S2 with route point/s (161/162N)' in mn
    mn = lit.at['S3_01D_0', 'MN-MOVT-LIT']
    assert 'received by taking off S3 for line No.- 1 up to S21 with route point/s' in mn
    mn = lit.at['S14_14_0', 'MN-MOVT-LIT']
    assert 'up to S2 with overlap (OV-2)' in mn


def test_sub_movement_wording():
    sub = _texts().at['S3_01D_0', 'SUB-MOVT-LIT']
    assert 'UP train may be dispatched by taking off S12 up to S2 with route point/s (105/106N)' in sub


def test_516_text_lists_isolation_points():
    sh16 = _texts().at['S16_16_0', 'SH-DISP-LIT']
    assert 'with following isolation point/s (171, 173/174)' in sh16
    assert 'SH40 to SH42 with route point/s (151/152N) and vice-versa' in sh16
    assert '()' not in sh16


def test_shunt_line_without_reverse_points_has_no_empty_brackets():
    rows = ROWS + [dict(FROM='SH44', TO='SH46', UN='WCSL', **{'RT-PT-N': '157/158N', 'RT-TC': '157T'})]
    _, _, _, lit = run_pipeline(rows, hs=HS, end=END)
    sh = lit.set_index('FROM-TO').at['S16_16_0', 'SH-DISP-LIT']
    assert 'SH44 to SH46 with route point/s (157/158N) and vice-versa' in sh
    assert '()' not in sh and '& (' not in sh.split('SH44')[1].split('\n')[0]


def test_distance_condition_is_separated_from_movement_text():
    from helpers import make_toc
    from rax_disp_logic_optimised.processing.dispensation import disp_lit_fn
    from rax_disp_logic_optimised.processing.locking import ixl_fn
    from rax_disp_logic_optimised.processing.square_sheet import (
        new_disp_gen_frm_sqsh, new_lck_gen_frm_sqsh, sqsh_fn, vice_versa)
    from rax_disp_logic_optimised.processing.toc_format import toc_format
    toc = toc_format(make_toc(ROWS), HS, END, 18)
    ltoc = ixl_fn(toc, HS, END, 18)
    vv = vice_versa(sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK'))
    dl = new_disp_gen_frm_sqsh(new_lck_gen_frm_sqsh(ltoc, vv, 'NEW-LCK2'), vv, 'NEW-DSP2')
    xx = vv.replace('X', '', regex=True)
    xx.loc['S14_14', 'S3_01D'] = '[Dist. from S2 (CH-10.0) to FM of Point No.-101 (CH-90.0) is (80.0M)]'
    lit = disp_lit_fn(dl, 'NEW-DSP2', HS, END, 18, xx).set_index('FROM-TO')
    sub = lit.at['S14_14_0', 'SUB-MOVT-LIT']
    assert 'and vice versa. [Dist. from S2' in sub
