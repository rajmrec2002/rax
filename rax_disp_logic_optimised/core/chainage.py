"""
Chainage lookup, caching, and user prompting.

Three-tier resolution:
  1. In-memory cache (instant)
  2. File-loaded DataFrame (fast)
  3. User prompt via tkinter (slow, last resort)

Three-step silent fallback before prompting:
  1. Exact signal name       → 'SH12'
  2. Base name (no route)    → 'SH12' from 'SH12_01D'
  3. S{number} equivalent    → 'S12' from 'SH12' / 'CO12' / 'A18'
"""

import re
import logging
import pandas as pd
from typing import Dict, Optional, Tuple

from ..io.writers import write_table_file

logger = logging.getLogger(__name__)


class ChainageResolver:
    """
    Centralized chainage resolution with caching and fallback prompting.

    Parameters
    ----------
    ch_df : pd.DataFrame
        Chainage data with columns ['GR_NO', 'GR_CH'].
    path_ch : str
        File path for persisting new chainage entries.
    parent : tkinter widget, optional
        Parent window for dialog prompts.
    """

    def __init__(
        self,
        ch_df: pd.DataFrame,
        path_ch: str,
        parent=None,
    ):
        self._df = ch_df.copy()
        self._df['GR_NO'] = self._df['GR_NO'].astype(str).str.strip()
        self._df['GR_CH'] = pd.to_numeric(
            self._df['GR_CH'], errors='coerce'
        ).fillna(0.0)
        self._path_ch = path_ch
        self._parent = parent
        self._cache: Dict[str, float] = {}
        self._dirty = False

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._df

    @dataframe.setter
    def dataframe(self, value: pd.DataFrame):
        self._df = value

    def resolve(self, sig: str) -> float:
        """
        Three-step silent fallback before prompting user.

        Parameters
        ----------
        sig : str
            Signal name to look up (e.g. 'SH12', 'CO5_01D').

        Returns
        -------
        float
            Chainage value, or 0.0 if not found and user cancels.
        """
        sig = str(sig).strip()
        if not sig:
            return 0.0

        candidates = self._build_candidates(sig)

        for candidate in candidates:
            val = self._silent_lookup(candidate)
            if val != 0.0:
                if candidate != sig:
                    logger.debug(
                        "Chainage: '%s' not found → using '%s' = %.2f",
                        sig, candidate, val,
                    )
                    self._cache[sig] = val
                return val

        # All silent lookups failed → prompt user
        return self._prompt_user(sig)

    def lookup_point(self, pt_key: str) -> Tuple[float, bool]:
        """
        Look up a point chainage entry (e.g. 'PT207').

        Returns
        -------
        (value, found) : Tuple[float, bool]
            found=False only when the user cancels.
        """
        row = self._df.loc[self._df['GR_NO'] == pt_key, 'GR_CH']
        if not row.empty:
            return float(row.values[0]), True
        if pt_key in self._cache:
            return self._cache[pt_key], True

        val = self._prompt_user(pt_key)
        return val, (val != 0.0)

    def silent_lookup(self, sig: str) -> float:
        """
        Check cache then file – no dialog, no side effects.
        Returns chainage value or 0.0 if not found.
        """
        sig = str(sig).strip()
        if not sig:
            return 0.0
        if sig in self._cache:
            return self._cache[sig]
        try:
            match = self._df.loc[
                self._df['GR_NO'] == sig, 'GR_CH'
            ]
            if not match.empty:
                val = float(match.iloc[-1])
                self._cache[sig] = val
                return val
        except Exception:
            pass
        return 0.0

    def save(self) -> None:
        """Persist updated chainage file if new entries were added."""
        if not self._dirty or not self._path_ch:
            return
        try:
            write_table_file(
                self._df[['GR_NO', 'GR_CH']],
                self._path_ch,
                index=False,
                header=True,
            )
            self._dirty = False
            logger.info("Chainage file updated: %s", self._path_ch)
        except Exception as e:
            logger.error("Failed to save chainage file: %s", e)

    def _build_candidates(self, sig: str) -> list:
        """Build ordered list of candidate names to try."""
        candidates = [sig]

        # Strip route suffix: 'SH12_01D' → 'SH12'
        base = re.sub(r'[_/].*$', '', sig)
        if base and base != sig:
            candidates.append(base)
        else:
            base = sig

        # S{number}: 'SH12' → 'S12', 'CO5' → 'S5', 'A18' → 'S18'
        num_match = re.search(r'\d+', base)
        if num_match:
            s_sig = 'S' + num_match.group()
            if s_sig not in candidates:
                candidates.append(s_sig)

        return candidates

    def _silent_lookup(self, key: str) -> float:
        """Lookup in cache then file – no dialog."""
        key = key.strip()
        if not key:
            return 0.0
        if key in self._cache:
            return self._cache[key]
        try:
            match = self._df.loc[self._df['GR_NO'] == key, 'GR_CH']
            if not match.empty:
                val = float(match.iloc[-1])
                self._cache[key] = val
                return val
        except Exception:
            pass
        return 0.0

    def _prompt_user(self, gr_no: str) -> float:
        """Prompt via tkinter or fall back to 0.0."""
        if not self._parent_alive():
            logger.warning(
                "Chainage parent unavailable for '%s'. Using 0.0.", gr_no
            )
            return 0.0

        from tkinter import simpledialog, messagebox as msbox

        while True:
            try:
                user_val = simpledialog.askstring(
                    'Missing Chainage',
                    f"Chainage for '{gr_no}' is missing in chainage file.\n"
                    f"Please enter chainage value:",
                    parent=self._parent,
                )
            except Exception as e:
                logger.warning(
                    "Chainage prompt failed for '%s': %s", gr_no, e
                )
                return 0.0

            if user_val is None:
                logger.info("Chainage for '%s' not provided. Using 0.0.", gr_no)
                return 0.0

            try:
                ch_val = float(str(user_val).strip())
                self._cache[gr_no] = ch_val
                self._append_to_dataframe(gr_no, ch_val)
                return ch_val
            except ValueError:
                if self._parent_alive():
                    try:
                        msbox.showerror(
                            'Invalid Chainage',
                            f"'{user_val}' is not a valid numeric chainage.",
                        )
                    except Exception:
                        pass

    def _append_to_dataframe(self, gr_no: str, gr_ch: float) -> None:
        """Add new entry and deduplicate."""
        new_row = pd.DataFrame([{'GR_NO': gr_no, 'GR_CH': gr_ch}])
        self._df = pd.concat([self._df, new_row], ignore_index=True)
        self._df['GR_NO'] = self._df['GR_NO'].astype(str).str.strip()
        self._df = self._df.drop_duplicates(subset=['GR_NO'], keep='last')
        self._dirty = True

    def _parent_alive(self) -> bool:
        try:
            return bool(self._parent and self._parent.winfo_exists())
        except Exception:
            return False
