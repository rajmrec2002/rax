"""
rax-server: run the Rax Logic web server, and manage its user accounts.

    rax-server --port 8765 --data-dir /var/lib/rax-logic --workers 2

    rax-server user add [--name "SSE/Sig ADI"] [--uid ID] [--admin]
    rax-server user list
    rax-server user reset ID          new password (the user's sessions end)
    rax-server user disable ID | enable ID | remove ID

The server makes up the login ID (unless --uid is given) and the password,
and shows them once; only a hash of the password is kept.  On an installed
server run these through  sudo bash deploy/install.sh user ...  so that
they use the server's data folder and run as its service user.
"""

import argparse
import logging
import multiprocessing as mp
import os
import sys


def _fmt_time(t) -> str:
    import time
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(t)) if t else '-'


def user_main(argv) -> int:
    from .config import ServerConfig
    from .users import UserError, UserStore
    ap = argparse.ArgumentParser(prog='rax-server user', description='Manage login accounts')
    ap.add_argument('--data-dir', default=ServerConfig().data_dir)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('add', help='create an account; the server makes up ID and password')
    p.add_argument('--name', default='', help='who it is for, e.g. "SSE/Sig ADI"')
    p.add_argument('--uid', default='', help='login ID to use instead of a generated one')
    p.add_argument('--admin', action='store_true', help='may see and manage all jobs')
    sub.add_parser('list', help='list accounts')
    for name, hlp in (('reset', 'new password; the user is logged out'),
                      ('disable', 'block the account; the user is logged out'),
                      ('enable', 'unblock the account'), ('remove', 'delete the account')):
        sub.add_parser(name, help=hlp).add_argument('uid')
    a = ap.parse_args(argv)
    store = UserStore(os.path.abspath(a.data_dir))
    try:
        if a.cmd == 'add':
            uid, pw = store.add(a.uid, a.name, a.admin)
            print(f'Account created{" for " + a.name if a.name else ""}'
                  f'{" (administrator)" if a.admin else ""}.\n'
                  f'  Login ID: {uid}\n  Password: {pw}\n'
                  'Give these to the user. The password is shown only now; '
                  f'if it is lost, make a new one with:  user reset {uid}')
        elif a.cmd == 'list':
            rows = store.list()
            if not rows:
                print('No accounts: login is off unless RAX_TOKEN is set. '
                      'Create one with: rax-server user add --name "..."')
            for u in rows:
                print(f'{u["uid"]:<16} {"admin" if u["admin"] else "user ":<6} '
                      f'{"enabled " if u["enabled"] else "DISABLED"}  created {_fmt_time(u["created"])}'
                      f'  last login {_fmt_time(u["last_login"])}  {u["name"]}')
        elif a.cmd == 'reset':
            print(f'New password for {a.uid.lower()}: {store.reset(a.uid)}\n'
                  '(shown only now; the user must log in again)')
        elif a.cmd in ('disable', 'enable'):
            store.set_enabled(a.uid, a.cmd == 'enable')
            print(f'{a.uid.lower()} {a.cmd}d')
        elif a.cmd == 'remove':
            store.remove(a.uid)
            print(f'{a.uid.lower()} removed (their jobs are kept)')
    except UserError as e:
        print(f'Error: {e}', file=sys.stderr)
        return 2
    return 0


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'user':
        sys.exit(user_main(argv[1:]))
    from .config import ServerConfig
    d = ServerConfig()
    ap = argparse.ArgumentParser(prog='rax-server', description='Rax Logic dispensation server')
    ap.add_argument('--host', default=d.host, help=f'listen address (default {d.host})')
    ap.add_argument('--port', type=int, default=d.port, help=f'port (default {d.port})')
    ap.add_argument('--data-dir', default=d.data_dir, help='where jobs and outputs are kept')
    ap.add_argument('--workers', type=int, default=d.workers,
                    help='jobs that may run at the same time (each uses all CPU cores '
                         'for criss-cross)')
    ap.add_argument('--token', default=d.token,
                    help='server key with access to all jobs, for scripts (default: $RAX_TOKEN). '
                         'People log in with accounts made by: rax-server user add')
    ap.add_argument('--max-upload-mb', type=int, default=d.max_upload_mb)
    ap.add_argument('--keep-days', type=int, default=d.keep_days,
                    help='delete finished jobs after this many days (0 = never)')
    ap.add_argument('--version', action='store_true')
    a = ap.parse_args(argv)

    from .. import __version__
    if a.version:
        print(__version__)
        return
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)-7s %(name)s: %(message)s')
    try:
        import uvicorn
        from .app import create_app
    except ImportError as e:
        sys.exit(f'Server packages missing ({e.name}). Install with:  pip install "rax-logic[server]"')

    cfg = ServerConfig(host=a.host, port=a.port, data_dir=os.path.abspath(a.data_dir),
                       workers=max(1, a.workers), token=a.token,
                       max_upload_mb=a.max_upload_mb, keep_days=a.keep_days)
    os.makedirs(cfg.jobs_dir, exist_ok=True)
    import socket
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((cfg.host, cfg.port))
        except OSError:
            sys.exit(f'Port {cfg.port} is already used by another program (another dashboard?). '
                     f'Start with --port <free port>, or set RAX_PORT in '
                     f'/etc/rax-logic/server.env and run: sudo systemctl restart rax-logic')
    from .users import UserStore
    if cfg.host not in ('127.0.0.1', 'localhost') and not cfg.token \
            and not UserStore(cfg.data_dir).has_users():
        logging.warning('No user accounts: anyone who can reach port %d can run jobs and '
                        'download outputs.  Create accounts with: rax-server user add', cfg.port)
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level='info')


if __name__ == '__main__':
    mp.freeze_support()
    main()
