"""Login accounts: ID and password made by the server, sessions, own jobs only."""
import json
import re
import threading
import time

import pytest

from rax_disp_logic_optimised.server import users as U

pytest.importorskip('fastapi')
uvicorn = pytest.importorskip('uvicorn')

from test_server import ROWS, _free_port  # noqa: E402
from helpers import make_toc  # noqa: E402
from rax_disp_logic_optimised.client import api as client_api  # noqa: E402
from rax_disp_logic_optimised.client.api import LoginRequired, RaxClient, ServerError  # noqa: E402
from rax_disp_logic_optimised.server.__main__ import user_main  # noqa: E402
from rax_disp_logic_optimised.server.app import create_app  # noqa: E402
from rax_disp_logic_optimised.server.config import ServerConfig  # noqa: E402


@pytest.fixture(autouse=True)
def _client_conf(tmp_path, monkeypatch):
    # never touch the real ~/.rax-logic/client.conf
    monkeypatch.setattr(client_api, 'CONFIG_PATH', str(tmp_path / 'client.conf'))
    monkeypatch.delenv('RAX_TOKEN', raising=False)
    monkeypatch.delenv('RAX_SERVER', raising=False)


# ── user store ───────────────────────────────────────────────────────────────
def test_server_makes_login_id_and_password(tmp_path):
    st = U.UserStore(str(tmp_path))
    assert not st.has_users()
    uid, pw = st.add(name='SSE/Sig ADI')
    assert re.fullmatch(r'rx\d{5}', uid)
    assert re.fullmatch(r'[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}', pw)
    saved = (tmp_path / 'users.json').read_text()
    assert pw not in saved and 'hash' in json.loads(saved)[uid]       # only a hash is kept
    s = st.login(uid, pw)
    assert st.session(s['token'])['uid'] == uid
    with pytest.raises(U.UserError, match='Wrong'):
        st.login(uid, 'wrong')
    with pytest.raises(U.UserError, match='already exists'):
        st.add(uid=uid)


def test_reset_disable_remove_end_sessions(tmp_path):
    st = U.UserStore(str(tmp_path))
    uid, pw = st.add(uid='sse.adi')
    tok = st.login(uid, pw)['token']
    new = st.reset(uid)
    assert st.session(tok) is None                   # logged out by the reset
    with pytest.raises(U.UserError):
        st.login(uid, pw)
    tok = st.login(uid, new)['token']
    st.set_enabled(uid, False)
    assert st.session(tok) is None
    with pytest.raises(U.UserError, match='disabled'):
        st.login(uid, new)
    st.set_enabled(uid, True)
    tok = st.login(uid, new)['token']
    # another process (the admin command) changes the file: seen at once
    U.UserStore(str(tmp_path)).remove(uid)
    assert st.session(tok) is None


def test_too_many_wrong_passwords_lock_the_id(tmp_path):
    st = U.UserStore(str(tmp_path))
    uid, pw = st.add()
    for _ in range(U.MAX_FAILS):
        with pytest.raises(U.UserError, match='Wrong'):
            st.login(uid, 'x')
    with pytest.raises(U.UserError, match='Too many'):
        st.login(uid, pw)


def test_admin_command(tmp_path, capsys):
    assert user_main(['--data-dir', str(tmp_path), 'add', '--name', 'SSE ADI']) == 0
    out = capsys.readouterr().out
    uid = re.search(r'Login ID: (\S+)', out).group(1)
    assert re.search(r'Password: \S+', out)
    assert user_main(['--data-dir', str(tmp_path), 'list']) == 0
    assert uid in capsys.readouterr().out
    assert user_main(['--data-dir', str(tmp_path), 'reset', uid]) == 0
    assert user_main(['--data-dir', str(tmp_path), 'remove', 'nobody']) == 2


# ── server with accounts ─────────────────────────────────────────────────────
@pytest.fixture(scope='module')
def acc_server(tmp_path_factory):
    data = tmp_path_factory.mktemp('acc')
    st = U.UserStore(str(data))
    accounts = {'a': st.add(name='User A'), 'b': st.add(name='User B'),
                'admin': st.add(name='Admin', admin=True)}
    cfg = ServerConfig(host='127.0.0.1', port=_free_port(), data_dir=str(data),
                       workers=1, token='', keep_days=0)
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host=cfg.host, port=cfg.port,
                                        log_level='warning'))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f'http://127.0.0.1:{cfg.port}', accounts
    srv.should_exit = True
    t.join(10)


def test_login_needed_and_own_jobs_only(acc_server, tmp_path):
    url, acc = acc_server
    toc = tmp_path / 'TOC.xlsx'
    make_toc(ROWS).to_excel(toc, index=False)

    anon = RaxClient(url, token='')
    assert anon.health()['login_required'] is True
    assert anon.needs_login()
    with pytest.raises(LoginRequired):
        anon.jobs()
    with pytest.raises(ServerError, match='Wrong login ID or password'):
        anon.login(acc['a'][0], 'not-it', save=False)

    a = RaxClient(url, token='')
    assert a.login(*acc['a'])['uid'] == acc['a'][0]
    # saved for next time: the session, not the password
    conf = open(client_api.CONFIG_PATH).read()
    assert f'UID={acc["a"][0]}' in conf and acc['a'][1] not in conf
    assert RaxClient(url).me()['uid'] == acc['a'][0]

    job = a.submit(str(toc), 'TST', l_no=18)
    b = RaxClient(url, token='')
    b.login(*acc['b'], save=False)
    assert all(j['id'] != job['id'] for j in b.jobs())          # not B's job
    with pytest.raises(ServerError, match='404'):
        b.status(job['id'])
    with pytest.raises(ServerError, match='404'):
        b.delete(job['id'])
    adm = RaxClient(url, token='')
    adm.login(*acc['admin'], save=False)
    mine = [j for j in adm.jobs() if j['id'] == job['id']]
    assert mine and mine[0]['user'] == acc['a'][0]               # admin sees all
    st = a.wait(job['id'], poll=0.3, timeout=120)
    assert st['state'] == 'done', st.get('error')
    assert a.download(job['id'], str(tmp_path / 'out'))

    a.logout()
    with pytest.raises(LoginRequired):
        a.jobs()
    with pytest.raises(LoginRequired):
        RaxClient(url).jobs()                                    # saved session ended too


def test_web_page_shows_login_form(acc_server):
    import urllib.request
    url, _ = acc_server
    html = urllib.request.urlopen(url + '/').read().decode()
    assert 'const LOGIN = true' in html and 'id="loginform"' in html
