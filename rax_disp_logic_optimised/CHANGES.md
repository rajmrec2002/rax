# Changes

## 2.0.1 – locking and dispensation fixes

Tested on GRF (`GRF_TOC_Final_01.04.2026.xlsx` + `GRF_CH-Signal_Point.xlsx`,
criss-cross 0–3000 m) and ADI. Regression tests: `make test` (19 tests).

### Conflict detection (`processing/locking.py`, `point_format.py`, `constants.py`)

| Bug | Effect | Fix |
|---|---|---|
| Route number stripped of `D`/`M` before the same-line lookup (`01D` → `01`) but the index kept `01D` | Two routes onto the same line (e.g. `CO21_01D` / `CO38_01D`) were not locked against each other | Both sides use `un_key()`: drops a trailing `D`/`M` after a digit and leading zeros (`01D` = `01M` = `1`, `5M` = `05M`) |
| Isolation points re-parsed with commas removed, so `824N, 821N` became one point `824821` | A route's own isolation points never matched; conflicts were found from one side only (`~`) | Isolation points read from `ISO-PT-N` / `ISO-PT-R` |
| `ISO-PT-N` kept only the last half of a crossover (`111/112N` → `112`) | Did not match `111/112R` in other routes | Full name kept |
| ISO track circuits blanked before the search | Isolation track circuits never caused a conflict | Kept from `toc_format()` |
| Track-circuit pattern `^\d+[A-Z]*T$` | `240/250T`, `259/260T`, `DMAT`, `JKCL1AT` … ignored | Pattern accepts `/` and letter names (`AXT` excluded) |
| `D`/`M` stripped from the ends of track-circuit lists | First/last name corrupted (`DMAT` → `AT`) | Only whitespace/punctuation stripped |
| Points limited to 3 digits | 2- or 4-digit points ignored | 1–4 digits |
| Run-together isolation entries `142T135T` | Read as one name | Split like `121N111/112N` already was |

### Square sheet and dispensation (`processing/square_sheet.py`, `criss_cross.py`)

* A conflict found from one side only is now **marked `~` on both cells and
  treated as a conflict everywhere** (lock list, dispensation, criss-cross).
  Before, `~` was dropped and the pair was printed as a permitted simultaneous
  movement.
* **Behaviour change for a corrected VV square sheet (option 08):** an `X`
  left on one side only now keeps the pair locked. To permit a pair, clear
  both cells.
* Criss-cross: a signal or point missing from the chainage file used to count
  as chainage 0, giving a false distance that could pass the min/max check.
  Missing chainage now means "no distance proof", so the pair stays
  conflicting, and the run logs which entries are missing.
* Criss-cross no longer rewrites the chainage file (it replaced blank or text
  chainages with 0 and dropped duplicates).

### Dispensation text (`processing/dispensation.py`)

* "up to S10 **with with** route point/s" and "up to S10 route point/s"
  (missing "with") fixed for main, sub and 5.16 texts.
* 5.16 texts now list isolation points (only shown before if the ISO cell
  literally said `YES`).
* Shunt lines no longer end with empty `()`; no double space after the line
  number.
* Permitted main movements are matched by exact route name, not by a regex
  substring.

### Not changed (needs a decision)

* Calling-on (CO) routes have a blank `RT-TC` in the TOC, so they only lock
  through points and line number, not track circuits.
* Home-signal auto-detect can pick wrong groups (on ADI it picked S87–S121).
  Check the groups before running.
