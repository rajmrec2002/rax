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
