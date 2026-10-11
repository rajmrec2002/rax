"""
rax-server: run the Rax Logic web server.

    rax-server --port 8080 --data-dir /var/lib/rax-logic --workers 2
"""

import argparse
import logging
import multiprocessing as mp
import os
import sys


def main(argv=None) -> None:
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
                    help='login token clients must send (default: $RAX_TOKEN; blank = no login)')
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
    if cfg.host not in ('127.0.0.1', 'localhost') and not cfg.token:
        logging.warning('No login token set: anyone who can reach port %d can run jobs '
                        'and download outputs.  Set RAX_TOKEN or --token.', cfg.port)
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level='info')


if __name__ == '__main__':
    mp.freeze_support()
    main()
