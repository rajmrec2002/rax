"""
Main PySide6 GUI application window.
"""

import os
import platform
import subprocess
import tempfile
import threading
import traceback
from time import strftime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..utils.logging_util import debug_print
from ..io.readers import read_table_file
from ..io.writers import write_table_file
from ..io.preprocessor import read_toc_file, detect_toc_format, read_formatted_toc
from ..core.constants import TOC_INPUT_COLS, TOC_BLANK_COLS
from ..processing.auto_detect import auto_detect_home_signals
from ..processing.toc_format import toc_format
from ..core.settings import read_settings, write_settings
from ..processing.pipeline import check_chainage, parse_bands
from ..service.jobs import STATES_FINAL, cancel_process, check_process, start_job_process

# App / window icon (Indian Railways logo)
_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'app_icon.png')


def _app_icon() -> QIcon:
    return QIcon(_ICON_PATH) if os.path.isfile(_ICON_PATH) else QIcon()


# ── Colour palette ─────────────────────────────────────────────────────────
_BG        = '#f1f5f9'
_PANEL     = '#ffffff'
_SHADOW    = '#c8d3e0'
_HDR1      = '#1e3a5f'
_HDR2      = '#1d4f8a'
_HDR3      = '#0f6657'
_HDR4      = '#3b1f6e'
_NUM_FG    = '#2563eb'
_BODY_FG   = '#1e293b'
_MUTED_FG  = '#64748b'
_BTN_PRI   = '#2563eb';  _BTN_PRI_HOV  = '#1d4ed8'
_BTN_SEC   = '#e8edf3';  _BTN_SEC_HOV  = '#d1d9e6'
_BTN_RUN   = '#15803d';  _BTN_RUN_HOV  = '#166534'
_BTN_RST   = '#b45309';  _BTN_RST_HOV  = '#92400e'
_BTN_CLO   = '#dc2626';  _BTN_CLO_HOV  = '#b91c1c'

_FILE_FILTER = ('CSV files (*.csv);;Excel files (*.xlsx);;'
                'XLS files (*.xls);;PDF files (*.pdf);;All files (*.*)')
_SAVE_FILTER = 'CSV files (*.csv);;Excel files (*.xlsx);;All files (*.*)'


class PillProgressBar(QWidget):
    """Rounded pill-shaped progress bar drawn with QPainter."""

    def __init__(self, parent=None, track='#dde3eb', fill='#2563eb'):
        super().__init__(parent)
        self._track = QColor(track)
        self._fill  = QColor(fill)
        self._value = 0.0
        self._max   = 100.0
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setValue(self, v: float):
        self._value = float(v)
        self.update()

    def value(self) -> float:
        return self._value

    def configure(self, **kw):
        if 'value'   in kw: self.setValue(kw['value'])
        if 'maximum' in kw: self._max = float(kw['maximum']); self.update()

    def __setitem__(self, k, v): self.configure(**{k: v})
    def __getitem__(self, k):
        if k == 'value':   return self._value
        if k == 'maximum': return self._max
        raise KeyError(k)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2.0
        tp = QPainterPath()
        tp.addRoundedRect(0, 0, w, h, r, r)
        p.fillPath(tp, self._track)
        if self._max > 0 and self._value > 0:
            fw = max(min(self._value / self._max, 1.0) * w, h)
            fp = QPainterPath()
            fp.addRoundedRect(0, 0, fw, h, r, r)
            p.fillPath(fp, self._fill)
        p.end()


class _WorkerSignals(QObject):
    pb_value = Signal(float)
    error    = Signal(str)
    success  = Signal()
    finished = Signal(dict)       # job status (local or server run)


class RaxLogicApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Physical Progress Module')
        self.setWindowIcon(_app_icon())

        self.fp1 = self.fp_ch = self.fpvv = self.save_dir = ''
        self.path_347 = self.path_516 = self.path1 = self.path2 = ''

        scr = QApplication.primaryScreen().availableGeometry()
        w = max(400, int(scr.width()  * 0.60))
        h = max(300, int(scr.height() * 0.60))
        self.setMinimumSize(400, 300)
        self.resize(w, h)

        self._wsig = _WorkerSignals()
        self._wsig.pb_value.connect(lambda v: self.pb.setValue(v))
        self._wsig.error.connect(
            lambda m: QMessageBox.critical(self, 'Error', m))
        self._wsig.success.connect(
            lambda: QMessageBox.information(self, 'Success',
                                            'Function completed successfully.'))
        self._wsig.finished.connect(self._job_finished)
        self._wsig.error.connect(lambda _m: self._set_busy(False))
        self._job = None                  # (process, status_path) of a local run
        self._job_timer = QTimer(self)
        self._job_timer.timeout.connect(self._poll_job)
        self._remote_stop = threading.Event()
        self._build_ui()
        self._apply_styles()

    # ── Stylesheet ─────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.centralWidget().setStyleSheet(f"""
            QWidget#bg   {{ background: {_BG}; }}
            QWidget#card {{ background: {_PANEL}; }}
            QWidget#bg   {{ font-family: 'Segoe UI'; font-size: 11pt; }}
            QWidget#card {{ font-family: 'Segoe UI'; font-size: 11pt; }}
            QWidget#bg   QLabel {{ background: transparent; color: {_BODY_FG}; }}
            QWidget#card QLabel {{ background: transparent; color: {_BODY_FG}; }}
            QLineEdit {{
                background: {_PANEL}; border: 1px solid #94a3b8;
                border-radius: 3px; padding: 2px 6px;
                color: {_BODY_FG}; font-family: 'Segoe UI'; font-size: 11pt;
            }}
            QLineEdit:focus {{ border: 2px solid {_NUM_FG}; padding: 1px 5px; }}
            QComboBox {{
                background: {_PANEL}; border: 1px solid #94a3b8;
                border-radius: 3px; padding: 2px 6px;
                color: {_BODY_FG}; font-family: 'Segoe UI'; font-size: 11pt;
            }}
            QComboBox:focus {{ border: 2px solid {_NUM_FG}; padding: 1px 5px; }}
            QComboBox::drop-down {{
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 20px; border-left: 1px solid #94a3b8;
            }}
            QComboBox QAbstractItemView {{
                background: {_PANEL}; border: 1px solid #94a3b8;
                selection-background-color: #dbeafe; color: {_BODY_FG}; outline: none;
            }}
            QFrame#card {{ background: {_PANEL}; border: 1px solid #e2e8f0; }}
            QWidget#bg   QPushButton,
            QWidget#card QPushButton {{
                border: 1px solid transparent; border-radius: 3px; padding: 3px 8px;
                font-weight: bold; font-family: 'Segoe UI'; font-size: 11pt;
                background: {_BTN_SEC}; color: {_BODY_FG};
            }}
            QWidget#bg   QPushButton:hover,
            QWidget#card QPushButton:hover {{
                background: {_BTN_SEC_HOV}; border-color: #94a3b8;
            }}
        """)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root_w = QWidget(); root_w.setObjectName('bg')
        self.setCentralWidget(root_w)
        root_lay = QVBoxLayout(root_w)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        root_lay.addWidget(self._make_banner())

        cards_w = QWidget(); cards_w.setObjectName('bg')
        cards_lay = QVBoxLayout(cards_w)
        cards_lay.setContentsMargins(8, 4, 8, 4)
        cards_lay.setSpacing(4)

        _, ff_g = self._make_card(cards_lay, 'File Selection',       _HDR1)
        _, sf_g = self._make_card(cards_lay, 'Signal Inputs',         _HDR2)
        _, cf_g = self._make_card(cards_lay, 'Criss-Cross Settings',  _HDR3)
        _, af_g = self._make_card(cards_lay, 'Run & Open Outputs',    _HDR4)

        cards_lay.setStretch(0, 2)
        cards_lay.setStretch(1, 3)
        cards_lay.setStretch(2, 1)
        cards_lay.setStretch(3, 1)
        root_lay.addWidget(cards_w, 1)

        self._populate_file_card(ff_g)
        self._populate_signal_card(sf_g)
        self._populate_criss_card(cf_g)
        self._populate_run_card(af_g)

    def _make_banner(self) -> QFrame:
        banner = QFrame(); banner.setObjectName('banner')
        banner.setStyleSheet(f'QFrame#banner {{ background: {_HDR1}; }}')
        lay = QHBoxLayout(banner)
        lay.setContentsMargins(12, 6, 12, 6)
        left = QVBoxLayout(); left.setSpacing(1)
        t = QLabel('Physical Progress Module')
        t.setStyleSheet('color: #f8fafc; font-size: 16pt; font-weight: bold;')
        s = QLabel('Choose files, set the station inputs, and run the workflow.')
        s.setStyleSheet('color: #94a3b8; font-size: 11pt;')
        left.addWidget(t); left.addWidget(s)
        self.lbl_time = QLabel('--:--:--')
        self.lbl_time.setStyleSheet('color: #94a3b8; font-size: 11pt;')
        self.lbl_time.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if os.path.isfile(_ICON_PATH):
            logo = QLabel()
            logo.setPixmap(_app_icon().pixmap(44, 44))
            logo.setFixedSize(48, 48)
            lay.addWidget(logo)
            lay.addSpacing(6)
        lay.addLayout(left, 1)
        lay.addWidget(self.lbl_time)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._tick()
        return banner

    def _make_card(self, parent_lay, title, hdr_color):
        shadow = QFrame()
        shadow.setStyleSheet(f'background: {_SHADOW};')
        s_lay = QVBoxLayout(shadow)
        s_lay.setContentsMargins(0, 0, 2, 2); s_lay.setSpacing(0)

        card = QFrame(); card.setObjectName('card')
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(0, 0, 0, 0); c_lay.setSpacing(0)

        hdr = QFrame()
        hdr.setStyleSheet(
            f'background: {hdr_color};'
            f'border-bottom: 1px solid rgba(0,0,0,0.18);')
        h_lay = QHBoxLayout(hdr)
        h_lay.setContentsMargins(8, 4, 8, 4)
        hl = QLabel(title)
        hl.setStyleSheet('color: white; font-size: 11pt; font-weight: bold;')
        h_lay.addWidget(hl)

        body = QWidget(); body.setObjectName('card')
        g = QGridLayout(body)
        g.setContentsMargins(8, 4, 8, 4); g.setSpacing(3)
        g.setColumnStretch(2, 1)

        c_lay.addWidget(hdr); c_lay.addWidget(body); c_lay.addStretch(1)
        s_lay.addWidget(card)
        parent_lay.addWidget(shadow)
        return body, g

    # ── Label / button helpers ────────────────────────────────────────────

    def _num_lbl(self, text) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f'color: {_NUM_FG}; font-weight: bold;')
        l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        l.setFixedWidth(30)
        return l

    def _desc_lbl(self, text) -> QLabel:
        return QLabel(text)

    def _path_lbl(self) -> QLabel:
        l = QLabel('..............................')
        l.setStyleSheet(f'color: {_MUTED_FG}; font-size: 10pt;')
        l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return l

    def _btn(self, text, slot, bg=_BTN_SEC, hov=_BTN_SEC_HOV,
             fg=_BODY_FG) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setStyleSheet(
            f'QPushButton {{ background: {bg}; color: {fg};'
            f' border: 1px solid transparent; border-radius: 3px;'
            f' padding: 3px 8px; font-weight: bold; }}'
            f'QPushButton:hover {{ background: {hov}; border-color: #94a3b8; }}'
        )
        return b

    # ── Card population ───────────────────────────────────────────────────

    def _populate_file_card(self, g: QGridLayout):
        rows = [
            ('01', 'Browse and choose TOC.CSV file',                'lb_toc',  self.get_toc_loc),
            ('02', 'Select folder to save outputs',                 'lb_save', self.get_save_loc),
            ('03', 'Browse and choose CHAINAGE file',               'lb_ch',   self.get_ch_loc),
            ('04', 'Browse and choose VV SQSH file (if corrected)', 'lb_vv',   self.get_vv_loc),
        ]
        for r, (num, desc, attr, slot) in enumerate(rows):
            g.addWidget(self._num_lbl(num),   r, 0)
            g.addWidget(self._desc_lbl(desc), r, 1)
            lbl = self._path_lbl()
            setattr(self, attr, lbl)
            g.addWidget(lbl, r, 2)
            g.addWidget(self._btn('Browse', slot, _BTN_PRI, _BTN_PRI_HOV, '#fff'), r, 3)

    def _populate_signal_card(self, g: QGridLayout):
        g.setColumnStretch(1, 1); g.setColumnStretch(2, 1)

        g.addWidget(self._num_lbl('05'), 0, 0)
        g.addWidget(self._desc_lbl('Station name'), 0, 1)
        self.e_stn = QLineEdit()
        g.addWidget(self.e_stn, 0, 2, 1, 2)

        g.addWidget(self._num_lbl('06'), 1, 0)
        g.addWidget(self._desc_lbl('First stop signal no.'), 1, 1)
        g.addWidget(self._desc_lbl('Reception direction'), 1, 2)

        self.e_hs = []; self.e_end = []
        for i, lbl_text in enumerate(['(A)', '(B)', '(C)', '(D)'], start=2):
            nl = QLabel(lbl_text)
            nl.setStyleSheet(f'color: {_NUM_FG}; font-weight: bold;')
            nl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            nl.setFixedWidth(30)
            g.addWidget(nl, i, 0)
            hs = QLineEdit(); end = QLineEdit()
            g.addWidget(hs, i, 1); g.addWidget(end, i, 2)
            self.e_hs.append(hs); self.e_end.append(end)

        g.addWidget(
            self._btn('Auto-Detect Home Signals', self.auto_detect_hs),
            6, 1, 1, 3)

        g.addWidget(self._num_lbl('07'), 7, 0)
        g.addWidget(self._desc_lbl('Berthing lines'), 7, 1)
        self.cb_lno = QComboBox()
        self.cb_lno.addItems([str(i) for i in range(1, 51)])
        g.addWidget(self.cb_lno, 7, 2, 1, 2)

        g.addWidget(self._num_lbl('08'), 8, 0)
        g.addWidget(self._desc_lbl('Attach corrected vvlsqsh?'), 8, 1)
        self.cb_vvm = QComboBox()
        self.cb_vvm.addItems(['NO', 'YES'])
        g.addWidget(self.cb_vvm, 8, 2, 1, 2)

        g.addWidget(self._num_lbl('09'), 9, 0)
        g.addWidget(self._desc_lbl('Require criss-cross?'), 9, 1)
        self.cb_cc = QComboBox()
        self.cb_cc.addItems(['NO', 'YES'])
        g.addWidget(self.cb_cc, 9, 2, 1, 2)

        g.addWidget(self._num_lbl('13'), 10, 0)
        g.addWidget(self._desc_lbl('Isolation (ISO) preferred over dispensation?'), 10, 1)
        self.cb_iso = QComboBox()
        self.cb_iso.addItems(['YES', 'NO'])
        self.cb_iso.setToolTip(
            'YES: for every pair of movements the locking allows together, find points\n'
            'outside both routes and overlaps that isolate them. Isolated pairs are left\n'
            'out of the 3.47 / 5.16 dispensation; points no other movement needs the\n'
            'other way are added to ISO, the rest become conditional isolation\n'
            '(in ISO-PT-N / ISO-PT-R, e.g. #139/140# with S33_39). See the ISO output file.')
        g.addWidget(self.cb_iso, 10, 2, 1, 2)

        g.addWidget(self._num_lbl('14'), 11, 0)
        g.addWidget(self._desc_lbl('Isolation: min. criss-cross distance from next stop signal (m)'),
                    11, 1)
        self.e_iso_dist = QLineEdit('120')
        self.e_iso_dist.setToolTip(
            'A movement without overlap (calling-on, shunt) is taken to overshoot its next stop\n'
            'signal along the movements possible from that signal post, and onwards.\n'
            'A meeting with another movement\'s route needs isolation only if the criss-cross\n'
            'point is within this distance of the next stop signal (uses the chainage file,\n'
            'field 03; without chainage every meeting counts). Blank = no limit.')
        g.addWidget(self.e_iso_dist, 11, 2, 1, 2)

        g.addWidget(self._num_lbl('15'), 12, 0)
        g.addWidget(self._desc_lbl('Isolation: consider rear-end movements?'), 12, 1)
        self.cb_iso_rear = QComboBox()
        self.cb_iso_rear.addItems(['NO', 'YES'])
        self.cb_iso_rear.setToolTip(
            'An overshoot that only runs on along the other movement\'s route in the same\n'
            'direction (e.g. CO38 to S30 overshooting onto the line S12 to S2 has just left)\n'
            'meets no criss-cross point: only a rear-end collision is possible.\n'
            'NO: such pairs are not isolated (listed in the ISO file, kept in dispensation).\n'
            'YES: isolation points are searched for them as well.')
        g.addWidget(self.cb_iso_rear, 12, 2, 1, 2)

    def _populate_criss_card(self, g: QGridLayout):
        g.addWidget(self._num_lbl('10'), 0, 0)
        g.addWidget(self._desc_lbl('Minimum distance'), 0, 1)
        self.e_cc_min = QLineEdit('0')
        g.addWidget(self.e_cc_min, 0, 2)

        g.addWidget(self._num_lbl('11'), 1, 0)
        g.addWidget(self._desc_lbl('Maximum distance'), 1, 1)
        self.e_cc_max = QLineEdit('0')
        g.addWidget(self.e_cc_max, 1, 2)

        g.addWidget(self._num_lbl('12'), 2, 0)
        g.addWidget(self._desc_lbl('Distance bands (m)'), 2, 1)
        self.e_cc_bands = QLineEdit()
        self.e_cc_bands.setPlaceholderText(
            'e.g. 0-120, 120-300, 300+   (blank = use min/max above)')
        self.e_cc_bands.setToolTip(
            'One set of 3.47 / 5.16 documents is generated per band.\n'
            'A band "a-b" dispenses pairs whose criss-cross distance is > a and <= b;\n'
            '"300+" has no upper limit.')
        g.addWidget(self.e_cc_bands, 2, 2)

    def _populate_run_card(self, g: QGridLayout):
        g.setColumnStretch(0, 1)

        self.pb = PillProgressBar()
        g.addWidget(self.pb, 0, 0, 1, 5)

        btns_w = QWidget(); btns_w.setObjectName('card')
        bl = QHBoxLayout(btns_w)
        bl.setContentsMargins(0, 2, 0, 2); bl.setSpacing(4)
        bl.addWidget(self._btn('Save Settings', self.save_settings))
        bl.addWidget(self._btn('Load Settings', self.load_settings))
        bl.addSpacing(10)
        self.btn_gen = self._btn('Gen TOC', self.start_gen_toc,
                                 _BTN_PRI, _BTN_PRI_HOV, '#fff')
        bl.addWidget(self.btn_gen)
        self.btn_run = self._btn('Run Fn', self.start_generate_disp,
                                 _BTN_RUN, _BTN_RUN_HOV, '#fff')
        bl.addWidget(self.btn_run)
        self.btn_stop = self._btn('Stop', self.stop_job)
        self.btn_stop.setEnabled(False)
        bl.addWidget(self.btn_stop)
        bl.addWidget(self._btn('RESET',  self.reset,
                                _BTN_RST, _BTN_RST_HOV, '#fff'))
        bl.addStretch()
        bl.addWidget(self._btn('Close', self.close,
                                _BTN_CLO, _BTN_CLO_HOV, '#fff'))
        g.addWidget(btns_w, 1, 0, 1, 5)

        ol = QLabel('Open outputs')
        ol.setStyleSheet(f'color: {_MUTED_FG}; font-weight: bold;')
        g.addWidget(ol, 2, 0, 1, 5)

        out_w = QWidget(); out_w.setObjectName('card')
        out_lay = QHBoxLayout(out_w)
        out_lay.setContentsMargins(0, 0, 0, 0); out_lay.setSpacing(6)
        for txt, attr in [('Disp 3.47', 'path_347'), ('Disp 5.16', 'path_516'),
                           ('Square Sheet', 'path1'), ('New TOC', 'path2')]:
            out_lay.addWidget(
                self._btn(txt,
                          lambda _=False, a=attr: self.open_file(getattr(self, a))))
        out_lay.addStretch()
        g.addWidget(out_w, 3, 0, 1, 5)

        srv_w = QWidget(); srv_w.setObjectName('card')
        sl = QHBoxLayout(srv_w)
        sl.setContentsMargins(0, 2, 0, 0); sl.setSpacing(6)
        sl.addWidget(QLabel('Run on server'))
        self.e_server = QLineEdit()
        self.e_server.setPlaceholderText(
            'blank = run on this computer;  or e.g. http://10.0.0.5:8765')
        self.e_server.setToolTip(
            'Address of a Rax Logic server. The TOC, chainage and VV files are uploaded,\n'
            'the job runs on the server and the outputs are saved in the folder of field 02.\n'
            'Log in with the login ID and password the server administrator gave you;\n'
            'the session (not the password) is kept in ~/.rax-logic/client.conf.')
        sl.addWidget(self.e_server, 1)
        self.btn_login = self._btn('Login', lambda: self._ensure_login(force=True))
        self.btn_login.setToolTip('Log in to the server (login ID and password from the '
                                  'server administrator)')
        sl.addWidget(self.btn_login)
        g.addWidget(srv_w, 4, 0, 1, 5)

    # ── Event handlers ────────────────────────────────────────────────────

    @Slot()
    def _tick(self):
        self.lbl_time.setText(strftime('%H:%M:%S %p'))

    def get_toc_loc(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Select TOC file', '', _FILE_FILTER)
        if f:
            self.fp1 = os.path.abspath(f)
            self.lb_toc.setText(self.fp1)
            self.pb.setValue(2)

    def get_ch_loc(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Select Chainage file', '', _FILE_FILTER)
        if f:
            self.fp_ch = os.path.abspath(f)
            self.lb_ch.setText(self.fp_ch)

    def get_vv_loc(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Select VV SQSH file', '', _FILE_FILTER)
        if f:
            self.fpvv = os.path.abspath(f)
            self.lb_vv.setText(self.fpvv)

    def get_save_loc(self):
        d = QFileDialog.getExistingDirectory(self, 'Select output folder')
        if d:
            self.save_dir = d
            self.lb_save.setText(d)

    def open_file(self, filepath):
        if not filepath or not os.path.exists(filepath):
            QMessageBox.warning(self, 'File Missing',
                                'Output file has not been generated yet.')
            return
        try:
            sys_name = platform.system().lower()
            if sys_name == 'windows':
                os.startfile(filepath)
            elif sys_name == 'darwin':
                subprocess.run(['open', filepath], check=True)
            else:
                subprocess.run(['xdg-open', filepath], check=True)
        except Exception as e:
            QMessageBox.critical(self, 'Error Opening File',
                                 f'Could not open file:\n{e}')

    def reset(self):
        self.fp1 = self.fp_ch = self.fpvv = self.save_dir = ''
        self.path_347 = self.path_516 = self.path1 = self.path2 = ''
        for lbl in (self.lb_toc, self.lb_save, self.lb_ch, self.lb_vv):
            lbl.setText('..............................')
        self.e_stn.clear()
        for e in self.e_hs + self.e_end:
            e.clear()
        self.cb_lno.setCurrentIndex(0)
        self.cb_vvm.setCurrentText('NO')
        self.cb_cc.setCurrentText('NO')
        self.cb_iso.setCurrentText('YES')
        self.e_iso_dist.setText('120')
        self.cb_iso_rear.setCurrentText('NO')
        self.e_cc_min.setText('0')
        self.e_cc_max.setText('0')
        self.e_cc_bands.clear()
        self.e_server.clear()
        self.pb.setValue(0)

    def save_settings(self):
        stn = self.e_stn.text().strip()
        if not stn:
            QMessageBox.warning(self, 'Station Name Missing',
                                'Please enter the station name (field 05) before saving.')
            return
        base_dir = os.path.expanduser('~/.rax-logic')
        os.makedirs(base_dir, exist_ok=True)
        filepath = os.path.join(base_dir, f'{stn}_{strftime("%Y%m%d_%H%M%S")}.txt')
        cfg = {
            'TOC_FILE': self.fp1, 'SAVE_DIR': self.save_dir,
            'CHAINAGE_FILE': self.fp_ch, 'VV_FILE': self.fpvv,
            'STATION_NAME': self.e_stn.text(),
            'BERTHING_LINES': self.cb_lno.currentText(),
            'VV_SQSH': self.cb_vvm.currentText(),
            'CRISS_CROSS': self.cb_cc.currentText(),
            'ISO_PREFERRED': self.cb_iso.currentText(),
            'ISO_DISTANCE': self.e_iso_dist.text().strip(),
            'ISO_REAR_END': self.cb_iso_rear.currentText(),
            'CC_MIN': self.e_cc_min.text(), 'CC_MAX': self.e_cc_max.text(),
            'CC_BANDS': self.e_cc_bands.text(),
            'SERVER_URL': self.e_server.text().strip(),
        }
        for i, grp in enumerate('ABCD'):
            cfg[f'HS_{grp}'] = self.e_hs[i].text()
            cfg[f'END_{grp}'] = self.e_end[i].text()
        try:
            write_settings(filepath, cfg)
            QMessageBox.information(self, 'Settings Saved',
                                    f'Settings saved to:\n{filepath}')
        except Exception as e:
            QMessageBox.critical(self, 'Save Error',
                                 f'Could not save settings:\n{e}')

    def load_settings(self):
        base_dir = os.path.expanduser('~/.rax-logic')
        filepath, _ = QFileDialog.getOpenFileName(
            self, 'Load Station Settings', base_dir,
            'Settings files (*.txt);;All files (*.*)')
        if not filepath:
            return
        try:
            cfg = read_settings(filepath)

            if cfg.get('TOC_FILE') and os.path.isfile(cfg['TOC_FILE']):
                self.fp1 = cfg['TOC_FILE']; self.lb_toc.setText(self.fp1)
            if cfg.get('SAVE_DIR') and os.path.isdir(cfg['SAVE_DIR']):
                self.save_dir = cfg['SAVE_DIR']; self.lb_save.setText(self.save_dir)
            if cfg.get('CHAINAGE_FILE') and os.path.isfile(cfg['CHAINAGE_FILE']):
                self.fp_ch = cfg['CHAINAGE_FILE']; self.lb_ch.setText(self.fp_ch)
            if cfg.get('VV_FILE') and os.path.isfile(cfg['VV_FILE']):
                self.fpvv = cfg['VV_FILE']; self.lb_vv.setText(self.fpvv)

            self.e_stn.setText(cfg.get('STATION_NAME', ''))
            for i, (hk, ek) in enumerate([('HS_A', 'END_A'), ('HS_B', 'END_B'),
                                           ('HS_C', 'END_C'), ('HS_D', 'END_D')]):
                self.e_hs[i].setText(cfg.get(hk, ''))
                self.e_end[i].setText(cfg.get(ek, ''))
            self.cb_lno.setCurrentText(cfg.get('BERTHING_LINES', '1'))
            self.cb_vvm.setCurrentText(cfg.get('VV_SQSH', 'NO'))
            self.cb_cc.setCurrentText(cfg.get('CRISS_CROSS', 'NO'))
            self.cb_iso.setCurrentText(cfg.get('ISO_PREFERRED', 'YES') or 'YES')
            self.e_iso_dist.setText(cfg.get('ISO_DISTANCE', '120'))
            self.cb_iso_rear.setCurrentText(cfg.get('ISO_REAR_END', 'NO') or 'NO')
            self.e_cc_min.setText(cfg.get('CC_MIN', '0'))
            self.e_cc_max.setText(cfg.get('CC_MAX', '0'))
            self.e_cc_bands.setText(cfg.get('CC_BANDS', ''))
            self.e_server.setText(cfg.get('SERVER_URL', ''))

            QMessageBox.information(self, 'Settings Loaded',
                                    f'Settings loaded from:\n{os.path.basename(filepath)}')
        except Exception as e:
            QMessageBox.critical(self, 'Load Error',
                                 f'Could not load settings:\n{e}')

    def _ask_end_names_dialog(self, groups):
        active = [(gi, sigs) for gi, (sigs, _) in enumerate(groups)
                  if any(s.strip('_') for s in sigs)]
        if not active:
            return None

        dlg = QDialog(self)
        dlg.setWindowTitle('Home Signal Groups — Enter End Names')
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg); lay.setSpacing(6)

        info = QLabel('Detected home/CO signal groups (grouped by extreme ends).\n'
                      'Enter a direction or end name for each group.')
        info.setWordWrap(True)
        lay.addWidget(info)

        entries = {}
        for gi, sigs in active:
            sig_str = ', '.join(s.rstrip('_') for s in sigs if s.strip('_'))
            gl = QLabel(f'Group {gi + 1}  —  {sig_str}')
            gl.setStyleSheet('font-weight: bold;')
            lay.addWidget(gl)
            rw = QWidget()
            rl = QHBoxLayout(rw); rl.setContentsMargins(16, 0, 0, 0)
            rl.addWidget(QLabel('End / direction name:'))
            e = QLineEdit(); rl.addWidget(e)
            lay.addWidget(rw)
            entries[gi] = e

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        ends = ['', '', '', '']
        for gi, e in entries.items():
            if gi < 4:
                ends[gi] = e.text().strip().upper()
        return ends

    def auto_detect_hs(self):
        if not self.fp1:
            QMessageBox.warning(self, 'No TOC File',
                                'Please select the TOC file first (field 01).')
            return
        try:
            if detect_toc_format(self.fp1):
                df = read_table_file(self.fp1)
                for col in TOC_INPUT_COLS + TOC_BLANK_COLS:
                    if col not in df.columns:
                        df[col] = ''
                toc_prev = (df[TOC_INPUT_COLS + TOC_BLANK_COLS]
                            .fillna('').reset_index(drop=True))
            else:
                toc_prev = read_toc_file(self.fp1)

            ad_hs, ad_end = auto_detect_home_signals(toc_prev)
            ends = self._ask_end_names_dialog(list(zip(ad_hs, ad_end)))
            if ends is None:
                return
            for i in range(4):
                self.e_hs[i].setText(
                    ','.join(s.rstrip('_') for s in ad_hs[i] if s.strip('_')))
                self.e_end[i].setText(ends[i])
            QMessageBox.information(self, 'Auto-Detect Result',
                                    'Home signals detected and fields updated.')
        except Exception as e:
            QMessageBox.critical(self, 'Read Error',
                                 f'Could not read TOC file:\n{e}')

    def _set_pb(self, value):
        self._wsig.pb_value.emit(float(value))

    def _set_busy(self, busy: bool):
        self.btn_run.setEnabled(not busy)
        self.btn_gen.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)

    def _home_signal_texts(self):
        return ([e.text() for e in self.e_hs], [e.text() for e in self.e_end])

    def start_gen_toc(self):
        if not self.fp1:
            QMessageBox.critical(self, 'No TOC File',
                                 'Please select the TOC file first.')
            return
        if not self.save_dir:
            QMessageBox.critical(self, 'No Save Folder',
                                 'Please select an output folder first.')
            return
        # Read the widgets here, in the GUI thread; the worker gets plain values.
        from ..processing.pipeline import parse_home_signals
        hs, end = parse_home_signals(*self._home_signal_texts())
        args = (self.fp1, self.save_dir, self.e_stn.text(), hs, end,
                int(self.cb_lno.currentText()))
        self._set_busy(True)
        self.btn_stop.setEnabled(False)
        threading.Thread(target=self.gen_toc_task, args=args, daemon=True).start()

    def gen_toc_task(self, toc_path, save_dir, stn, hs, end, l_no_val):
        try:
            self._set_pb(0)
            t_str = strftime('%Y%m%d_%H-%M')
            path2 = os.path.join(save_dir, f'STN_{stn}TOC-fmt-_{t_str}.xlsx')
            toc = (read_toc_file(toc_path)
                   if not detect_toc_format(toc_path)
                   else read_formatted_toc(toc_path))
            self._set_pb(40)
            toc = toc_format(toc, hs, end, l_no_val)
            self._set_pb(80)
            write_table_file(toc, path2, index=False)
            self.path2 = path2
            self._set_pb(100)
            self._wsig.finished.emit({'state': 'gen-toc'})
        except Exception as e:
            debug_print(f'[ERROR] {e}\n{traceback.format_exc()}')
            self._wsig.error.emit(f'{type(e).__name__}: {e}')

    def start_generate_disp(self):
        if not self.fp1:
            QMessageBox.critical(self, 'No TOC File',
                                 'Please select the TOC file first.')
            return
        if not self.save_dir:
            QMessageBox.critical(self, 'No Save Folder',
                                 'Please select an output folder first.')
            return

        # Warn about groups that have signals but no end/direction name
        ignored = [
            f'  Group ({lbl}): {self.e_hs[i].text().strip()}'
            for i, lbl in enumerate(['A', 'B', 'C', 'D'])
            if self.e_hs[i].text().strip() and not self.e_end[i].text().strip()
        ]
        if ignored:
            QMessageBox.warning(
                self,
                'Groups Without End Name — Will Be Ignored',
                'The following home signal groups have no end/direction name\n'
                'and will be ignored in this run:\n\n'
                + '\n'.join(ignored)
                + '\n\nFill in the direction names and run again to include them.',
            )

        vv = self.cb_vvm.currentText().upper() == 'YES'
        if vv and not self.fpvv:
            QMessageBox.critical(self, 'No VV File',
                                 'Please select a VV file for corrected sqsh (field 04).')
            return
        criss = self.cb_cc.currentText().upper() == 'YES'
        bands, extra = None, {}
        if criss:
            try:
                bands = self._read_bands()
            except ValueError as e:
                QMessageBox.critical(self, 'Distance Bands', str(e))
                return
            extra = self._ask_missing_chainage()
            if extra is None:
                return

        try:
            t = self.e_iso_dist.text().strip()
            iso_dist = float(t) if t else None
        except ValueError:
            QMessageBox.critical(self, 'Isolation Distance',
                                 'Field 14 must be a distance in metres (or blank).')
            return
        hs, end = self._home_signal_texts()
        params = {
            'toc_path': self.fp1, 'save_dir': self.save_dir, 'stn': self.e_stn.text(),
            'hs': hs, 'end': end, 'l_no': int(self.cb_lno.currentText()),
            'vv_file': self.fpvv if vv else '',
            'criss_cross': criss, 'chainage_path': self.fp_ch,
            'bands': [[lo, None if hi == float('inf') else hi] for lo, hi in (bands or [])],
            'extra_chainage': extra,
            'iso_preferred': self.cb_iso.currentText().upper() == 'YES',
            'iso_distance': iso_dist,
            'iso_rear_end': self.cb_iso_rear.currentText().upper() == 'YES',
        }
        self._set_busy(True)
        self.pb.setValue(0)
        server = self.e_server.text().strip()
        if server:
            if not self._ensure_login():
                self._set_busy(False)
                return
            self._remote_stop.clear()
            threading.Thread(target=self._remote_task, args=(server, params),
                             daemon=True).start()
            return
        # Local run in its own process: the window stays responsive.
        status = os.path.join(tempfile.gettempdir(), f'rax_job_{os.getpid()}.json')
        try:
            for f in (status, status + '.params.json'):
                if os.path.exists(f):
                    os.remove(f)
            self._job = (start_job_process(params, status), status)
        except Exception as e:
            self._set_busy(False)
            QMessageBox.critical(self, 'Error', f'Could not start the run:\n{e}')
            return
        self._job_timer.start(300)

    def _poll_job(self):
        if not self._job:
            self._job_timer.stop()
            return
        proc, status = self._job
        st = check_process(proc, status)
        self.pb.setValue(float(st.get('progress', 0) or 0))
        if st.get('state') in STATES_FINAL:
            self._job_timer.stop()
            self._job = None
            proc.join(timeout=1)
            for f in (status, status + '.params.json'):
                try:
                    os.remove(f)
                except OSError:
                    pass
            self._wsig.finished.emit(st)

    def stop_job(self):
        if self._job:
            proc, status = self._job
            cancel_process(proc, status)
        self._remote_stop.set()

    def _ensure_login(self, force: bool = False) -> bool:
        """
        Make sure there is a valid session on the server in 'Run on server'
        (asks for login ID and password when needed).  False = cancelled or
        the server cannot be reached.
        """
        from ..client.api import RaxClient, ServerError
        server = self.e_server.text().strip()
        if not server:
            QMessageBox.information(self, 'Login', 'Fill in "Run on server" first.')
            return False
        try:
            c = RaxClient(server, timeout=15)
            if not c.health().get('login_required'):
                if force:
                    QMessageBox.information(self, 'Login', f'{c.base} needs no login.')
                return True
            if not force and not c.needs_login():
                return True
        except ServerError as e:
            QMessageBox.critical(self, 'Server', str(e))
            return False
        uid, msg = c.uid, ''
        while True:
            got = self._login_dialog(c.base, uid, msg)
            if got is None:
                return False
            uid, pw = got
            try:
                s = c.login(uid, pw)
            except ServerError as e:
                msg = str(e).replace('Server said: ', '')
                continue
            if force:
                QMessageBox.information(self, 'Login', f'Logged in to {c.base} as {s["uid"]}.')
            return True

    def _login_dialog(self, base: str, uid: str = '', msg: str = ''):
        """(login ID, password), or None if cancelled."""
        dlg = QDialog(self)
        dlg.setWindowTitle('Log in to the server')
        dlg.setMinimumWidth(380)
        lay = QVBoxLayout(dlg); lay.setSpacing(6)
        info = QLabel(f'{base}\nLogin ID and password from the server administrator.')
        info.setWordWrap(True)
        lay.addWidget(info)
        if msg:
            err = QLabel(msg); err.setWordWrap(True)
            err.setStyleSheet('color: #b91c1c; font-weight: bold;')
            lay.addWidget(err)
        g = QGridLayout(); lay.addLayout(g)
        e_uid = QLineEdit(uid); e_pw = QLineEdit()
        e_pw.setEchoMode(QLineEdit.EchoMode.Password)
        g.addWidget(QLabel('Login ID'), 0, 0); g.addWidget(e_uid, 0, 1)
        g.addWidget(QLabel('Password'), 1, 0); g.addWidget(e_pw, 1, 1)
        (e_pw if uid else e_uid).setFocus()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText('Log in')
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return e_uid.text().strip(), e_pw.text()

    def _remote_task(self, server, params):
        """Upload, wait and download on a worker thread (network only, no GUI calls)."""
        from ..client.api import RaxClient, ServerError
        job_id, c = None, None
        try:
            c = RaxClient(server, timeout=120)
            b = params['bands']
            band_txt = ', '.join(f'{lo:g}-{hi:g}' if hi is not None else f'{lo:g}+' for lo, hi in b)
            job = c.submit(params['toc_path'], params['stn'], params['hs'], params['end'],
                           params['l_no'], criss_cross=params['criss_cross'],
                           chainage=params['chainage_path'], vv=params['vv_file'],
                           bands=band_txt, extra_chainage=params['extra_chainage'],
                           iso_preferred=params['iso_preferred'],
                           iso_distance=params['iso_distance'],
                           iso_rear_end=params['iso_rear_end'])
            job_id = job['id']
            st = job
            while st.get('state') not in STATES_FINAL:
                if self._remote_stop.wait(2):
                    st = c.cancel(job_id)
                    break
                st = c.status(job_id)
                self._set_pb(st.get('progress', 0))
            if st.get('state') == 'done':
                c.download(job_id, params['save_dir'])
                st['paths'] = {k: os.path.join(params['save_dir'], v)
                               for k, v in st.get('outputs', {}).items()}
            self._wsig.finished.emit(st)
        except ServerError as e:
            self._wsig.error.emit(str(e))
        except Exception as e:
            debug_print(f'[ERROR] {e}\n{traceback.format_exc()}')
            self._wsig.error.emit(f'{type(e).__name__}: {e}')

    @Slot(dict)
    def _job_finished(self, st):
        self._set_busy(False)
        state = st.get('state')
        if state == 'gen-toc':
            self._wsig.success.emit()
            return
        if state == 'cancelled':
            self.pb.setValue(0)
            QMessageBox.information(self, 'Stopped', 'The run was stopped.')
            return
        if state != 'done':
            QMessageBox.critical(self, 'Error', st.get('error') or 'The run failed.')
            return
        paths = st.get('paths', {})
        self.path1 = paths.get('SQSH', '')
        first = next((k for k in paths if k.startswith('347')), '347')
        sfx = first[3:]
        self.path2 = paths.get('TOC' + sfx, '')
        self.path_347 = paths.get('347' + sfx, '')
        self.path_516 = paths.get('516' + sfx, '')
        self.pb.setValue(100)
        msg = 'Function completed successfully.'
        miss = st.get('missing_chainage') or []
        if miss:
            msg += (f'\n\nNo chainage for {len(miss)} signal(s)/point(s): '
                    f'{", ".join(miss[:30])}{" …" if len(miss) > 30 else ""}\n'
                    'Criss-cross pairs that need them stay locked. Fill in the '
                    'CH-MISSING file in the output folder and run again.')
        iso = st.get('isolation') or {}
        if iso:
            msg += (f'\n\nIsolation: {iso.get("isolated", 0)} pair(s) isolated '
                    f'({iso.get("iso points added", 0)} ISO point(s) added, '
                    f'{iso.get("conditional entries", 0)} conditional) – no dispensation needed. '
                    f'{iso.get("not isolable", 0)} cannot be isolated, '
                    f'{iso.get("check layout", 0)} to check, '
                    f'{iso.get("rear-end ignored", 0)} rear-end only (not considered). '
                    'See the ISO file.')
        if st.get('checks'):
            msg += f'\n\nTOC-CHECK: {st["checks"]} finding(s) – see the TOC-CHECK file.'
        (QMessageBox.warning if miss else QMessageBox.information)(self, 'Success', msg)

    def _ask_missing_chainage(self):
        """
        Before a criss-cross run: list signals/points with no chainage and let
        the user type them.  Returns {name: chainage} (may be empty), or None
        to cancel the run.
        """
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            missing = check_chainage(self.fp1, self.fp_ch)
        except Exception as e:
            missing = None
            err = e
        finally:
            QApplication.restoreOverrideCursor()
        if missing is None:
            QMessageBox.warning(self, 'Chainage Check',
                                f'Could not check the chainage:\n{err}')
            return {}
        if not missing:
            return {}

        dlg = QDialog(self)
        dlg.setWindowTitle('Missing Chainage')
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        no_file = not (self.fp_ch and os.path.isfile(self.fp_ch))
        info = QLabel(
            ('No chainage file is attached (field 03).\n' if no_file else
             'These signals / points are not in the chainage file.\n')
            + f'{len(missing)} entr{"y" if len(missing) == 1 else "ies"} missing. '
            'Enter the chainage for any you know; criss-cross pairs that need a '
            'blank one stay locked. The values are used for this run only and '
            'are written to the CH-USED file in the output folder.')
        info.setWordWrap(True)
        lay.addWidget(info)
        area = QScrollArea(); area.setWidgetResizable(True)
        inner = QWidget(); gl = QGridLayout(inner)
        entries = {}
        for r, name in enumerate(missing):
            gl.addWidget(QLabel(name), r, 0)
            e = QLineEdit(); e.setPlaceholderText('chainage (m)')
            gl.addWidget(e, r, 1)
            entries[name] = e
        area.setWidget(inner)
        area.setMinimumHeight(min(400, 34 * len(missing) + 10))
        lay.addWidget(area)
        bb = QDialogButtonBox()
        bb.addButton('Run', QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        save = bb.addButton('Save list…', QDialogButtonBox.ButtonRole.ActionRole)
        save.setToolTip('Save the missing entries as a chainage file (GR_NO, GR_CH) to fill in\n'
                        'and attach as field 03 or add to your chainage file.')

        def _save_list():
            path, _ = QFileDialog.getSaveFileName(
                dlg, 'Save missing chainage list',
                os.path.join(self.save_dir or os.path.expanduser('~'),
                             f'{self.e_stn.text() or "STN"}_CH-MISSING.csv'),
                'CSV files (*.csv)')
            if path:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('GR_NO,GR_CH\n')
                    for name, e in entries.items():
                        f.write(f'{name},{e.text().strip()}\n')
        save.clicked.connect(_save_list)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            out, bad = {}, []
            for name, e in entries.items():
                t = e.text().strip()
                if not t:
                    continue
                try:
                    out[name] = float(t)
                except ValueError:
                    bad.append(f'{name}: "{t}"')
            if not bad:
                return out
            QMessageBox.warning(dlg, 'Invalid Chainage',
                                'Not a number:\n' + '\n'.join(bad))

    def _read_bands(self):
        if self.e_cc_bands.text().strip():
            return parse_bands(self.e_cc_bands.text())
        lo = float(self.e_cc_min.text() or 0)
        hi = float(self.e_cc_max.text() or 0)
        if hi <= lo:
            raise ValueError('Maximum distance must be greater than minimum distance.')
        return [(lo, hi)]

    def closeEvent(self, event):
        if self._job or self.btn_stop.isEnabled():
            if QMessageBox.question(
                    self, 'Run in Progress',
                    'A run is still in progress. Stop it and close?') \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_job()
        event.accept()


def launch_app(config_path: Optional[str] = None) -> None:
    """Entry point called from cli/main.py."""
    import sys
    if platform.system() == 'Linux':
        os.environ.setdefault('QT_QPA_PLATFORMTHEME', 'generic')

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    _rules = os.environ.get('QT_LOGGING_RULES', '')
    _w = 'qt.qpa.wayland.textinput=false'
    if _w not in _rules:
        os.environ['QT_LOGGING_RULES'] = (_rules + ';' + _w if _rules else _w)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    if platform.system() == 'Windows':
        # Give the app its own taskbar entry so Windows shows our icon,
        # not the Python one.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('RaxLogic.PPM')
        except Exception:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(_app_icon())
    window = RaxLogicApp()
    window.showMaximized()
    sys.exit(app.exec())
