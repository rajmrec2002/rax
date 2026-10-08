"""
Auto-detection of home signals and calling-on signals.

Indian Railway conventions:
  - A home-signal number appears as BOTH S<n> AND CO<n> in the TOC.
  - DN-end home signals carry odd numbers  (S1, CO1, S3, CO3, …).
  - UP-end home signals carry even numbers (S2, CO2, S4, CO4, …).
  - Within each parity group the home signals sit at the two extremes
    of the number range, separated from dispatch/intermediate signals
    by a significantly larger gap.
  - This gives up to 4 groups: DN-low, DN-high, UP-low, UP-high.
"""

import logging
import pandas as pd
from typing import List, Set, Tuple

from ..core.constants import RE_HOME_SIGNAL, RE_SIGNAL_NUM

logger = logging.getLogger(__name__)


def _split_extremes(nums: List[int]) -> Tuple[List[int], List[int]]:
    """
    Split a sorted list of numbers into a low-end cluster and a high-end
    cluster using spare-gap analysis.

    The threshold for a 'spare gap' is max(2 × median consecutive gap, 4).
    Returns (low_cluster, high_cluster); high_cluster is [] when no
    significant gap exists (single cluster).
    """
    if len(nums) < 2:
        return nums, []

    gaps = [nums[i + 1] - nums[i] for i in range(len(nums) - 1)]
    median_gap = sorted(gaps)[len(gaps) // 2]
    threshold = max(median_gap * 2.0, 4)

    first_spare = next((i for i, g in enumerate(gaps) if g >= threshold), None)
    if first_spare is None:
        return nums, []

    last_spare = len(gaps) - 1 - next(
        i for i, g in enumerate(reversed(gaps)) if g >= threshold
    )

    low = [n for n in nums if n <= nums[first_spare]]
    high = [n for n in nums if n >= nums[last_spare + 1]]

    if not low or not high or low[-1] >= high[0]:
        return nums, []

    return low, high


def auto_detect_home_signals(
    toc_df: pd.DataFrame,
) -> Tuple[List[List[str]], List[str]]:
    """
    Auto-detect home/calling-on signal groups from the TOC.

    Algorithm
    ---------
    1. Collect all S<n> and CO<n> tokens from GN and TO columns.
    2. Keep only numbers where BOTH S<n> AND CO<n> are present.
    3. Split by parity: odd → DN direction, even → UP direction.
    4. Within each parity group apply spare-gap analysis to find the
       low-end and high-end extreme clusters (dispatch/intermediate
       signals in the middle are excluded).
    5. Return up to 4 groups: [DN-low, DN-high, UP-low, UP-high].

    Returns
    -------
    hs  : list of 4 lists – 'SIGNAL_' pattern strings
    end : list of 4 str   – always '' (user fills in direction names)
    """
    _empty: Tuple[List[List[str]], List[str]] = (
        [[''], [''], [''], ['']], ['', '', '', '']
    )

    # ── 1. Collect S<n> and CO<n> tokens ──────────────────────────────────
    cols = ['GN'] + (['TO'] if 'TO' in toc_df.columns else [])
    raw = pd.concat(
        [toc_df[c].dropna().astype(str) for c in cols], ignore_index=True
    )
    tokens: Set[str] = {
        t.strip().upper()
        for t in raw
        if RE_HOME_SIGNAL.match(t.strip().upper())
    }

    if not tokens:
        logger.info("AutoDetect: No S/CO tokens found")
        return _empty

    # Map number → set of prefixes present
    num_to_prefixes: dict = {}
    for tok in tokens:
        m = RE_SIGNAL_NUM.search(tok)
        if not m:
            continue
        n = int(m.group())
        prefix = 'CO' if tok.startswith('CO') else 'S'
        num_to_prefixes.setdefault(n, set()).add(prefix)

    # ── 2. Keep only paired numbers (S<n> + CO<n> both present) ──────────
    home_nums = sorted(
        n for n, px in num_to_prefixes.items()
        if {'S', 'CO'}.issubset(px)
    )

    if len(home_nums) < 2:
        logger.info("AutoDetect: Fewer than 2 paired S+CO numbers – cannot detect")
        return _empty

    logger.info("AutoDetect: Paired home-signal numbers: %s", home_nums)

    # ── 3. Split by parity ────────────────────────────────────────────────
    dn_nums = [n for n in home_nums if n % 2 != 0]   # odd  → DN
    up_nums = [n for n in home_nums if n % 2 == 0]   # even → UP

    if not dn_nums or not up_nums:
        logger.info(
            "AutoDetect: All home numbers same parity (dn=%s up=%s) – "
            "cannot split by direction", dn_nums, up_nums
        )
        return _empty

    logger.info("AutoDetect: DN (odd)=%s  UP (even)=%s", dn_nums, up_nums)

    # ── 4. Split each parity group into low-end / high-end extremes ───────
    dn_low, dn_high = _split_extremes(dn_nums)
    up_low, up_high = _split_extremes(up_nums)

    logger.info(
        "AutoDetect: DN low=%s high=%s | UP low=%s high=%s",
        dn_low, dn_high, up_low, up_high,
    )

    # ── 5. Build signal name lists ─────────────────────────────────────────
    def _names(nums: List[int]) -> List[str]:
        out = []
        for n in nums:
            if f'S{n}' in tokens:
                out.append(f'S{n}')
            if f'CO{n}' in tokens:
                out.append(f'CO{n}')
        return out

    groups_nums = [dn_low, dn_high, up_low, up_high]
    hs: List[List[str]] = []
    for gnums in groups_nums:
        names = _names(gnums)
        hs.append([s + '_' for s in names] if names else [''])

    end: List[str] = ['', '', '', '']
    logger.info("AutoDetect: Result hs=%s", hs)
    return hs, end
