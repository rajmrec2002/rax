#!/usr/bin/env python3
"""
Rax Logic – Application Entry Point.

Handles dependency checking, argument parsing, logging setup,
and launches the PySide6 GUI.
"""

import sys
import os
import argparse
import logging
import platform

# ── Multiprocessing start method (Linux only, must be before any fork) ───
if platform.system() == 'Linux':
    import multiprocessing
    try:
        multiprocessing.set_start_method('fork', force=True)
    except RuntimeError:
        pass  # already set

# ── Path setup (remove once proper packaging is adopted) ─────────────────
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_package_parent = os.path.dirname(_project_root)
for _entry in (_package_parent, _project_root):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from rax_disp_logic_optimised.io.auto_install import check_pyside6, check_and_install
from rax_disp_logic_optimised.utils.logging_util import setup_logging
from rax_disp_logic_optimised import __version__ as APP_VERSION

logger = logging.getLogger(__name__)

APP_NAME = "Rax Logic"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rax-logic",
        description=f"{APP_NAME} – Railway Physical Progress Module",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--debug", action="store_true", default=False,
                        help="Enable DEBUG log level.")
    parser.add_argument("--no-auto-install", action="store_true", default=False,
                        help="Skip automatic dependency installation.")
    parser.add_argument("--config", type=str, default=None, metavar="PATH",
                        help="Path to custom configuration file.")
    return parser.parse_args()


def main() -> None:
    """Launch the Rax Logic application."""
    args = parse_args()

    # ── 1. Check PySide6 ─────────────────────────────────────────────────
    check_pyside6()

    # ── 2. Install missing dependencies ──────────────────────────────────
    if not args.no_auto_install:
        check_and_install(auto_restart=True)

    # ── 3. Set up logging ────────────────────────────────────────────────
    log_level = logging.DEBUG if args.debug else None
    setup_logging(level=log_level)
    logger.info("Starting %s v%s (PID %d, debug=%s)",
                APP_NAME, APP_VERSION, os.getpid(), args.debug)

    # ── 4. Import GUI lazily (after deps confirmed) ───────────────────────
    from rax_disp_logic_optimised.gui.app import launch_app
    # ── 5. Launch ─────────────────────────────────────────────────────────
    try:
        launch_app(config_path=args.config)
    except Exception:
        logger.critical("Application failed to start.", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
