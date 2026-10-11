"""
rax-client: run dispensation on a Rax Logic server from the command line.

    rax-client config --server http://server:8080 --token XXXX
    rax-client health
    rax-client run --settings ~/.rax-logic/ADI_20261011_101500.txt --out ./ADI_out
    rax-client run --toc ADI_TOC.xlsx --stn ADI --hs "S5,CO5=VIRAR" --hs "S6,S8=SBI" \\
                   --lines 18 --criss-cross --chainage ADI_CH.xlsx --bands "0-120,120-300,300+"
    rax-client check-chainage --toc ADI_TOC.xlsx --chainage ADI_CH.xlsx
    rax-client jobs | status ID | download ID --out DIR | cancel ID | delete ID
"""

import argparse
import os
import sys

from .api import RaxClient, ServerError, save_client_config


def _hs_args(values):
    hs, end = [''] * 4, [''] * 4
    for i, v in enumerate(values or []):
        if i >= 4:
            raise SystemExit('At most 4 --hs groups')
        sigs, _, name = v.partition('=')
        hs[i], end[i] = sigs.strip(), name.strip()
    return hs, end


def _extra(values):
    out = {}
    for v in values or []:
        k, _, ch = v.replace(',', '=').partition('=')
        try:
            out[k.strip().upper()] = float(ch)
        except ValueError:
            raise SystemExit(f'--add-chainage "{v}": use NAME=CHAINAGE, e.g. S12=1234.5')
    return out


def _progress(st):
    bar = '#' * (int(st.get('progress', 0)) // 4)
    sys.stderr.write(f'\r  {st.get("state", ""):<9} [{bar:<25}] {st.get("progress", 0):>3}%')
    sys.stderr.flush()
    if st.get('state') in ('done', 'failed', 'cancelled'):
        sys.stderr.write('\n')


def _print_job(st):
    print(f'{st["id"]}  {st.get("stn", ""):<8} {st.get("state", ""):<9} {st.get("progress", 0):>3}%'
          + (f'  {st["error"]}' if st.get('error') else ''))


def cmd_run(c: RaxClient, a) -> int:
    if a.settings:
        from ..core.settings import home_signal_texts, read_settings
        s = read_settings(a.settings)
        hs, end = home_signal_texts(s)
        a.toc = a.toc or s.get('TOC_FILE', '')
        a.stn = a.stn or s.get('STATION_NAME', '')
        a.chainage = a.chainage or s.get('CHAINAGE_FILE', '')
        a.lines = a.lines or int(s.get('BERTHING_LINES') or 1)
        a.criss_cross = a.criss_cross or s.get('CRISS_CROSS', '').upper() == 'YES'
        a.bands = a.bands or s.get('CC_BANDS', '')
        a.cc_min = a.cc_min if a.cc_min is not None else float(s.get('CC_MIN') or 0)
        a.cc_max = a.cc_max if a.cc_max is not None else float(s.get('CC_MAX') or 0)
        if s.get('VV_SQSH', '').upper() == 'YES':
            a.vv = a.vv or s.get('VV_FILE', '')
        a.out = a.out or s.get('SAVE_DIR', '')
        if a.hs:
            hs, end = _hs_args(a.hs)
    else:
        hs, end = _hs_args(a.hs)
    if not a.toc or not a.stn:
        raise SystemExit('Need --toc and --stn (or --settings)')
    out = a.out or os.path.join(os.getcwd(), f'{a.stn}_outputs')
    job = c.submit(a.toc, a.stn, hs, end, a.lines or 1, criss_cross=a.criss_cross,
                   chainage=a.chainage or '', vv=a.vv or '', bands=a.bands or '',
                   cc_min=a.cc_min or 0, cc_max=a.cc_max or 0,
                   extra_chainage=_extra(a.add_chainage))
    print(f'Job {job["id"]} submitted to {c.base}')
    if a.no_wait:
        return 0
    st = c.wait(job['id'], on_progress=_progress)
    if st['state'] != 'done':
        print(f'Job {st["state"]}: {st.get("error", "")}', file=sys.stderr)
        return 1
    files = c.download(job['id'], out)
    print(f'{len(files)} file(s) saved in {out}')
    if st.get('missing_chainage'):
        print(f'WARNING: no chainage for {len(st["missing_chainage"])} signal(s)/point(s): '
              f'{", ".join(st["missing_chainage"])}\n  Pairs that need them stay locked. Fill in '
              'the CH-MISSING file (or use --add-chainage NAME=CH) and run again.', file=sys.stderr)
    if st.get('check_summary'):
        print('TOC-CHECK findings:')
        for k, n in st['check_summary'].items():
            print(f'  {n:>4}  {k}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='rax-client', description='Rax Logic server client')
    ap.add_argument('--server', help='server address, e.g. http://10.0.0.5:8080 '
                                     '(default: ~/.rax-logic/client.conf or $RAX_SERVER)')
    ap.add_argument('--token', help='login token (default: client.conf or $RAX_TOKEN)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('config', help='save the server address and token')
    p.add_argument('--server', dest='cfg_server', required=True)
    p.add_argument('--token', dest='cfg_token', default='')

    sub.add_parser('health', help='check the server is reachable')

    p = sub.add_parser('run', help='submit a job, wait, download the outputs')
    p.add_argument('--settings', help='settings file saved by the desktop app')
    p.add_argument('--toc'); p.add_argument('--stn')
    p.add_argument('--hs', action='append', metavar='"S5,CO5=VIRAR"',
                   help='home-signal group and its end name; up to 4 times')
    p.add_argument('--lines', type=int, help='berthing lines')
    p.add_argument('--criss-cross', action='store_true')
    p.add_argument('--chainage'); p.add_argument('--vv', help='corrected VV square sheet')
    p.add_argument('--bands', help='e.g. "0-120, 120-300, 300+"')
    p.add_argument('--cc-min', type=float); p.add_argument('--cc-max', type=float)
    p.add_argument('--add-chainage', action='append', metavar='NAME=CH',
                   help='chainage missing from the file, e.g. S12=1234.5; repeatable')
    p.add_argument('--out', help='folder for the outputs')
    p.add_argument('--no-wait', action='store_true', help='submit and return the job id')

    p = sub.add_parser('check-chainage', help='list signals/points with no chainage')
    p.add_argument('--toc', required=True); p.add_argument('--chainage', default='')

    p = sub.add_parser('jobs', help='list recent jobs')
    for name, hlp in (('status', 'show one job'), ('cancel', 'stop a job'),
                      ('delete', 'delete a job and its outputs')):
        sub.add_parser(name, help=hlp).add_argument('job_id')
    p = sub.add_parser('download', help='download outputs of a job')
    p.add_argument('job_id'); p.add_argument('--out', default='.')

    a = ap.parse_args(argv)
    try:
        if a.cmd == 'config':
            path = save_client_config(a.cfg_server, a.cfg_token)
            print(f'Saved to {path}')
            a.server, a.token = a.cfg_server, a.cfg_token
            print('Server:', RaxClient(a.server, a.token).health())
            return 0
        c = RaxClient(a.server, a.token)
        if a.cmd == 'health':
            print(c.health()); return 0
        if a.cmd == 'run':
            return cmd_run(c, a)
        if a.cmd == 'check-chainage':
            miss = c.check_chainage(a.toc, a.chainage)
            print('\n'.join(miss) if miss else 'All signals and points have a chainage.')
            return 0
        if a.cmd == 'jobs':
            for st in c.jobs():
                _print_job(st)
            return 0
        if a.cmd == 'status':
            st = c.status(a.job_id); _print_job(st)
            for f in st.get('files', []):
                print('   ', f)
            return 0
        if a.cmd == 'download':
            files = c.download(a.job_id, a.out)
            print(f'{len(files)} file(s) saved in {a.out}'); return 0
        if a.cmd == 'cancel':
            _print_job(c.cancel(a.job_id)); return 0
        if a.cmd == 'delete':
            c.delete(a.job_id); print('Deleted'); return 0
    except ServerError as e:
        print(f'Error: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
