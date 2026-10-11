"""Server + command-line client, end to end over HTTP."""
import os
import socket
import threading
import time

import pandas as pd
import pytest

pytest.importorskip('fastapi')
uvicorn = pytest.importorskip('uvicorn')

from helpers import make_toc  # noqa: E402
from rax_disp_logic_optimised.client.api import LoginRequired, RaxClient, ServerError  # noqa: E402
from rax_disp_logic_optimised.server.app import create_app  # noqa: E402
from rax_disp_logic_optimised.server.config import ServerConfig  # noqa: E402

ROWS = [
    dict(FROM='S3', TO='S21', UN='01D', **{'RT-PT-N': '101/102N', 'RT-TC': '3T, 101T'}),
    dict(FROM='S12', TO='S2', UN='12', **{'RT-PT-N': '105/106N', 'RT-TC': '105T, 12T'}),
    dict(FROM='S14', TO='S4', UN='14', **{'RT-PT-N': '107/108N', 'RT-TC': '107T, 14T'}),
]
TOKEN = 'secret-token'


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp('srv')
    cfg = ServerConfig(host='127.0.0.1', port=_free_port(), data_dir=str(data),
                       workers=1, token=TOKEN, keep_days=0)
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host=cfg.host, port=cfg.port,
                                        log_level='warning'))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f'http://127.0.0.1:{cfg.port}', data
    srv.should_exit = True
    t.join(10)


@pytest.fixture
def files(tmp_path):
    toc = tmp_path / 'TST TOC.xlsx'
    make_toc(ROWS).to_excel(toc, index=False)
    ch = tmp_path / 'ch.csv'
    pd.DataFrame({'GR_NO': ['S2', 'S4', 'S12', 'S14', 'S21', 'PT101', 'PT102', 'PT105', 'PT106'],
                  'GR_CH': [0.0, 50.0, 100.0, 400.0, 900.0, 950, 960, 120, 110]}).to_csv(ch, index=False)
    return str(toc), str(ch)


def test_login_is_required(server):
    url, _ = server
    assert RaxClient(url, token='').health()['login_required'] is True
    # RAX_TOKEN still works as a server key; anything else must log in
    with pytest.raises(LoginRequired, match='Not logged in'):
        RaxClient(url, token='wrong').jobs()


def test_run_job_and_download(server, files, tmp_path):
    url, data = server
    toc, ch = files
    c = RaxClient(url, token=TOKEN)
    job = c.submit(toc, 'TST', ['S3=', ''], l_no=18, criss_cross=True, chainage=ch,
                   bands='0-120, 120-3000', extra_chainage={'S3': 1000},
                   iso_distance=None, iso_rear_end=True)
    assert job['state'] in ('queued', 'running')
    st = c.wait(job['id'], poll=0.3, timeout=120)
    assert st['state'] == 'done', st.get('error')
    assert st['missing_chainage'] == ['PT107', 'PT108']
    assert '347_CC0-120m' in st['outputs'] and 'CH-USED' in st['outputs']
    out = tmp_path / 'dl'
    got = {os.path.basename(p) for p in c.download(job['id'], str(out))}
    assert set(st['outputs'].values()) <= got
    # uploaded files stay on the server under the job, not elsewhere
    assert os.path.isdir(data / 'jobs' / job['id'] / 'input')
    # the isolation options reach the job
    import json
    sent = json.load(open(data / 'jobs' / job['id'] / 'job.json'))
    sent = sent.get('params', sent)
    assert sent['iso_rear_end'] is True and sent['iso_distance'] is None
    assert any(j['id'] == job['id'] for j in c.jobs())
    c.delete(job['id'])
    with pytest.raises(ServerError, match='404'):
        c.status(job['id'])


def test_check_chainage_endpoint(server, files):
    url, _ = server
    toc, ch = files
    c = RaxClient(url, token=TOKEN)
    assert c.check_chainage(toc, ch) == ['PT107', 'PT108', 'S3']
    assert c.check_chainage(toc, ch, {'S3': 1}) == ['PT107', 'PT108']


def test_bad_requests(server, files):
    url, _ = server
    toc, _ = files
    c = RaxClient(url, token=TOKEN)
    with pytest.raises(ServerError, match='422'):
        c.submit(toc, 'TST', criss_cross=True, bands='300')       # unreadable band
    with pytest.raises(ServerError, match='404'):
        c.status('../../etc')
    with pytest.raises(ServerError, match='404'):
        c._request('GET', '/api/jobs/0123456789ab/files/x.xlsx')


def test_web_page(server):
    url, _ = server
    import urllib.request
    html = urllib.request.urlopen(url + '/').read().decode()
    assert 'Physical Progress Module' in html and 'const LOGIN = true' in html
    assert 'name="iso_rear_end"' in html and 'ISO_REAR_END' in html
    assert urllib.request.urlopen(url + '/assets/app_icon.png').status == 200
