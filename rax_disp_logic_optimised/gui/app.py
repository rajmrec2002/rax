"""
Main PySide6 GUI application window.
"""

import os
import platform
import re
import subprocess
import threading
import traceback
from time import strftime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..utils.logging_util import debug_print
from ..io.readers import read_table_file, get_default_output_ext
from ..io.writers import write_table_file
from ..io.preprocessor import read_toc_file, detect_toc_format, remap_extra_cols, read_formatted_toc
from ..io.exporters import write_disp347_xlsx, write_disp516_xlsx
from ..core.constants import TOC_INPUT_COLS, TOC_BLANK_COLS
from ..processing.auto_detect import auto_detect_home_signals
from ..processing.locking import ixl_fn
from ..processing.square_sheet import (
    sqsh_fn, vice_versa, new_lck_gen_frm_sqsh,
    new_disp_gen_frm_sqsh, re_vice_versa,
)
from ..processing.criss_cross import criss_cross_mvt
from ..processing.dispensation import disp_lit_fn
from ..processing.toc_format import toc_format

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


class RaxLogicApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Physical Progress Module')

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

    def _populate_criss_card(self, g: QGridLayout):
        g.addWidget(self._num_lbl('10'), 0, 0)
        g.addWidget(self._desc_lbl('Minimum distance'), 0, 1)
        self.e_cc_min = QLineEdit('0')
        g.addWidget(self.e_cc_min, 0, 2)

        g.addWidget(self._num_lbl('11'), 1, 0)
        g.addWidget(self._desc_lbl('Maximum distance'), 1, 1)
        self.e_cc_max = QLineEdit('0')
        g.addWidget(self.e_cc_max, 1, 2)

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
        bl.addWidget(self._btn('Gen TOC', self.start_gen_toc,
                                _BTN_PRI, _BTN_PRI_HOV, '#fff'))
        bl.addWidget(self._btn('Run Fn', self.start_generate_disp,
                                _BTN_RUN, _BTN_RUN_HOV, '#fff'))
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
        self.e_cc_min.setText('0')
        self.e_cc_max.setText('0')
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
        entries = [
            f'TOC_FILE={self.fp1}',
            f'SAVE_DIR={self.save_dir}',
            f'CHAINAGE_FILE={self.fp_ch}',
            f'VV_FILE={self.fpvv}',
            f'STATION_NAME={self.e_stn.text()}',
            f'HS_A={self.e_hs[0].text()}',  f'END_A={self.e_end[0].text()}',
            f'HS_B={self.e_hs[1].text()}',  f'END_B={self.e_end[1].text()}',
            f'HS_C={self.e_hs[2].text()}',  f'END_C={self.e_end[2].text()}',
            f'HS_D={self.e_hs[3].text()}',  f'END_D={self.e_end[3].text()}',
            f'BERTHING_LINES={self.cb_lno.currentText()}',
            f'VV_SQSH={self.cb_vvm.currentText()}',
            f'CRISS_CROSS={self.cb_cc.currentText()}',
            f'CC_MIN={self.e_cc_min.text()}',
            f'CC_MAX={self.e_cc_max.text()}',
        ]
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(f'{i+1:02d}. {e}' for i, e in enumerate(entries)))
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
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            cfg = {}
            for line in content.splitlines():
                line = re.sub(r'^\d+\.\s*', '', line.strip())
                if '=' in line:
                    k, _, v = line.partition('=')
                    cfg[k.strip()] = v.strip()

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
            self.e_cc_min.setText(cfg.get('CC_MIN', '0'))
            self.e_cc_max.setText(cfg.get('CC_MAX', '0'))

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

    def start_gen_toc(self):
        if not self.fp1:
            QMessageBox.critical(self, 'No TOC File',
                                 'Please select the TOC file first.')
            return
        if not self.save_dir:
            QMessageBox.critical(self, 'No Save Folder',
                                 'Please select an output folder first.')
            return
        threading.Thread(target=self.gen_toc_task, daemon=True).start()

    def gen_toc_task(self):
        try:
            self._set_pb(0)
            stn   = self.e_stn.text()
            t_str = strftime('%Y%m%d_%H-%M')
            self.path2 = os.path.join(
                self.save_dir, f'STN_{stn}TOC-fmt-_{t_str}.xlsx')

            hs, end = [], []
            for i in range(4):
                hs_text  = self.e_hs[i].text().strip()
                end_text = self.e_end[i].text().strip()
                if hs_text and not end_text:
                    hs.append([''])
                    end.append('')
                else:
                    hs.append(
                        [x.strip().upper() + '_'
                         for x in hs_text.split(',') if x.strip()] or ['']
                    )
                    end.append(end_text.upper())
            l_no_val = int(self.cb_lno.currentText())

            toc = (read_toc_file(self.fp1)
                   if not detect_toc_format(self.fp1)
                   else read_formatted_toc(self.fp1))
            self._set_pb(40)
            toc = toc_format(toc, hs, end, l_no_val)
            self._set_pb(80)
            write_table_file(toc, self.path2, index=False)
            self._set_pb(100)
            self._wsig.success.emit()
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

        threading.Thread(target=self.generate_disp_task, daemon=True).start()

    def generate_disp_task(self):
        try:
            self._set_pb(0)
            stn   = self.e_stn.text()
            t_str = strftime('%Y%m%d_%H-%M')
            self.path1      = os.path.join(self.save_dir, f'STN_{stn}SQSH-gen-_{t_str}.xlsx')
            self.path2      = os.path.join(self.save_dir, f'STN_{stn}TOC-gen-_{t_str}.xlsx')
            self.path_347   = os.path.join(self.save_dir, f'STN_{stn}347-gen-_{t_str}.xlsx')
            self.path_516   = os.path.join(self.save_dir, f'STN_{stn}516-gen-_{t_str}.xlsx')
            path_sqsh_xx    = os.path.join(self.save_dir, f'STN_{stn}SQSH_XX-gen-_{t_str}.xlsx')
            path_sqsh_cc    = os.path.join(self.save_dir, f'STN_{stn}SQSH_CC-gen-_{t_str}.xlsx')
            path_xx_pt_list = os.path.join(self.save_dir, f'STN_{stn}XX_PT_LIST-gen-_{t_str}.csv')
            path_xx_mt_list = os.path.join(self.save_dir, f'STN_{stn}XX_MT_LIST-gen-_{t_str}.csv')

            hs, end = [], []
            for i in range(4):
                hs_text  = self.e_hs[i].text().strip()
                end_text = self.e_end[i].text().strip()
                if hs_text and not end_text:
                    # No direction name submitted — ignore this group
                    hs.append([''])
                    end.append('')
                else:
                    hs.append(
                        [x.strip().upper() + '_'
                         for x in hs_text.split(',') if x.strip()] or ['']
                    )
                    end.append(end_text.upper())
            l_no_val   = int(self.cb_lno.currentText())
            sqsh_m_yn  = self.cb_vvm.currentText().upper()
            sqsh_cc_yn = self.cb_cc.currentText().upper()

            def _criss_progress(pct):
                self._set_pb(20 + (pct / 100.0) * 50)

            toc = (read_toc_file(self.fp1)
                   if not detect_toc_format(self.fp1)
                   else read_formatted_toc(self.fp1))
            toc = toc_format(toc, hs, end, l_no_val)
            self._set_pb(10)

            if sqsh_m_yn == 'YES':
                if not self.fpvv:
                    self._wsig.error.emit('Please select a VV file for corrected sqsh.')
                    return
                ltoc    = toc
                vvlsqsh = re_vice_versa(self.fpvv)
                vvlsqsh.fillna('', inplace=True)
                write_table_file(vvlsqsh, self.path1, index=False)
            else:
                ltoc    = ixl_fn(toc, hs, end, l_no_val)
                lsqsh   = sqsh_fn(ltoc, 'FROM-TO', 'NEW-LOCK')
                vvlsqsh = vice_versa(lsqsh)
                write_table_file(vvlsqsh, self.path1, index=True)
            self._set_pb(20)

            if sqsh_cc_yn == 'YES':
                vvlsqsh, sqsh_xx, _ = criss_cross_mvt(
                    toc, vvlsqsh, path_sqsh_xx, path_sqsh_cc,
                    path_xx_pt_list, path_xx_mt_list,
                    self.fp_ch,
                    int(self.e_cc_min.text() or 0),
                    int(self.e_cc_max.text() or 0),
                    parent=None, progress_cb=_criss_progress,
                )
            else:
                sqsh_xx = vvlsqsh.replace('X', '', regex=True)
            self._set_pb(70)

            l_toc = new_lck_gen_frm_sqsh(ltoc, vvlsqsh, 'NEW-LCK2')
            dltoc = new_disp_gen_frm_sqsh(l_toc, vvlsqsh, 'NEW-DSP2')
            write_table_file(dltoc, self.path2, index=False)
            self._set_pb(80)

            dmvt = disp_lit_fn(dltoc, 'NEW-DSP2', hs, end, l_no_val, sqsh_xx)
            write_disp347_xlsx(dmvt, self.path_347, stn)
            write_disp516_xlsx(dmvt, self.path_516, stn)

            self._set_pb(100)
            self._wsig.success.emit()
        except Exception as e:
            debug_print(f'[ERROR] {e}\n{traceback.format_exc()}')
            self._wsig.error.emit(f'{type(e).__name__}: {e}')


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

    app = QApplication.instance() or QApplication(sys.argv)
    window = RaxLogicApp()
    window.showMaximized()
    sys.exit(app.exec())
