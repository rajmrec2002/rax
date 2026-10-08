"""
Dependency auto-installer.

WARNING: This module runs pip install. Only import it explicitly
in the entry-point script (cli/main.py), never in library code.
"""

import sys
import os
import subprocess
import importlib.util
import platform
import logging
from typing import Dict, List, Tuple

from ..core.constants import REQUIRED_PKGS, OPTIONAL_PKGS

logger = logging.getLogger(__name__)


def check_and_install(
    required: Dict[str, str] = None,
    optional: Dict[str, str] = None,
    auto_restart: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Check for missing packages and attempt to install them.

    Returns (failed_required, failed_optional).
    Exits with code 1 if any required package fails.
    """
    required = required or REQUIRED_PKGS
    optional = optional or OPTIONAL_PKGS

    missing_req = {
        k: v for k, v in required.items()
        if importlib.util.find_spec(k) is None
    }
    missing_opt = {
        k: v for k, v in optional.items()
        if importlib.util.find_spec(k) is None
    }

    if not missing_req and not missing_opt:
        return [], []

    all_missing = {**missing_req, **missing_opt}
    print(f"\n[DEPS] Missing: {', '.join(all_missing.values())}")
    print("[DEPS] Auto-installing...\n")

    failed_req: List[str] = []
    failed_opt: List[str] = []

    for import_name, pip_name in all_missing.items():
        print(f"  Installing {pip_name:<25}", end='', flush=True)
        if _pip_install(pip_name):
            print('✓  OK')
        else:
            print('✗  FAILED')
            if import_name in missing_req:
                failed_req.append(pip_name)
            else:
                failed_opt.append(pip_name)

    if failed_opt:
        print(f"\n[WARN] Optional not installed: {', '.join(failed_opt)}")
        print("       Some formats (ODS, fast XLSX) may be unavailable.\n")

    if failed_req:
        _print_install_help(failed_req)
        input('\nPress Enter to exit...')
        sys.exit(1)

    if missing_req and auto_restart:
        print('\n[DEPS] Installation complete. Restarting...\n')
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f'[DEPS] Auto-restart failed: {e}. Please restart the application manually.')
            sys.exit(0)

    return failed_req, failed_opt


def check_pyside6() -> None:
    """Verify PySide6 is available; print install instructions if missing."""
    if importlib.util.find_spec('PySide6') is not None:
        return

    sys_name = platform.system()
    msg = '\n[DEPS] PySide6 is not installed (required for the GUI).'
    hint = '         pip install PySide6'
    if sys_name == 'Windows':
        hint = '         py -m pip install PySide6'
    elif sys_name == 'Linux':
        hint = '         pip install PySide6   # or: pip install --user PySide6'

    print(f'{msg}\n       Install with:\n{hint}')
    input('\nPress Enter to exit...')
    sys.exit(1)


def check_tkinter() -> None:
    """Legacy shim — kept for backward compatibility. Calls check_pyside6()."""
    check_pyside6()


def _pip_install(pip_name: str) -> bool:
    """Try pip install, falling back from --user to bare install."""
    python = sys.executable
    in_venv = (
        hasattr(sys, 'real_prefix')
        or getattr(sys, 'base_prefix', sys.prefix) != sys.prefix
    )

    attempts = []
    if not in_venv and platform.system() != 'Windows':
        attempts.append(
            [python, '-m', 'pip', 'install', '--upgrade', '--user', pip_name]
        )
    attempts.append(
        [python, '-m', 'pip', 'install', '--upgrade', pip_name]
    )
    attempts.append(
        [python, '-m', 'pip', 'install', '--upgrade',
         '--break-system-packages', pip_name]
    )

    for cmd in attempts:
        try:
            subprocess.check_call(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return False


def _print_install_help(failed: List[str]) -> None:
    sys_name = platform.system()
    print(f'\n[ERROR] Could not install: {", ".join(failed)}')
    print('\nInstall manually:')
    print(f'  pip install {" ".join(failed)}')
    if sys_name == 'Windows':
        print(f'  py -m pip install {" ".join(failed)}')
    elif sys_name == 'Linux':
        print(f'  pip3 install --user {" ".join(failed)}')
    elif sys_name == 'Darwin':
        print(f'  pip3 install {" ".join(failed)}')
