"""
Isolation of simultaneous movements ("ISO preferred over dispensation").

Single overshoot only
---------------------
Only one train is assumed to overshoot at a time; two trains overshooting
together (double overshoot) is rare and is ignored, as for criss-cross
movements.

* A movement with an overlap stays within it: the overlap is the
  allowance for its overshoot and its points are locked.
* A movement without an overlap (calling-on, shunt) overshoots its next
  stop signal in the direction of travel, along the movements that can
  start from that signal's post (CO21 -> S31: every route of S31, SH31,
  CO31, A31), then from the post at the end of each of those, and so on to
  the last stop signal.  Those routes are not set, so none of their points
  are locked.  The only limit is distance: a meeting counts only if the
  criss-cross point is within the required distance of the next stop
  signal (chainage file).  Without a chainage the distance cannot be
  measured: only the movements from the next signal's post are followed
  and every meeting on them counts.

A pair needs isolation when one movement's overshoot reaches the other
movement's ROUTE.  Reaching only the other's overlap would need that train
to overshoot too (double overshoot) and is ignored.

At a facing point the train goes the way the point lies, so a facing
point outside both routes and overlaps, locked the other way, isolates the
pair.  At a trailing point the train runs through to the toe whatever the
point's position, so trailing points never isolate.

Among the points that cut every such overshoot path, the one that locks
the fewest other movements is chosen (minimum repercussion), Normal first:

* no other permitted movement needs the opposite position -> added to the
  ISO of the overshooting movement (ISO-ADDED column);
* otherwise conditional isolation: locked only while both movements are
  set (COND-ISO column):  #139/140N# with S33_39 set.

Isolated pairs need no dispensation; pairs that cannot be isolated keep it.

Principles used
---------------
* A point track circuit carries the point number (at least 3 digits):
  121T for single-ended point 121; 101T and 102T, or 101/102T, for the two
  ends of crossover 101/102.  Line track circuits (01AT, 3BT …) have none.
* Facing / trailing comes from the point number and the direction: in the
  DN direction (odd signal numbers) odd point ends are facing, in the UP
  direction even ones.  Diversion is possible only at a facing point.
* S, SH, A and CO movements from one signal post to the next follow the
  same path, over the same points, to the same berthing in rear of the
  next stop signal post.  Where a shunt route leaves out the berthing
  track circuits, those of the matching S / CO movement are used here
  (isolation only; the locking is unchanged).

Track layout
------------
Read from the TOC: RT-TC followed by OV-TC lists a route's track circuits
in running order and the route's point positions say which leg of each
point it uses.  At a point's track circuit the edge used with the point
both Normal and Reverse is the toe; edges used only Normal / only Reverse
are the legs.  A track circuit has two neighbours, plus one for each point
end in it.  Where the TOC implies more, the links given by fewer routes are
set aside and listed in LAYOUT CHECK; only where that cannot be decided (a
tie) are pairs that depend on it reported for checking, not changed.
"""

import itertools
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import pandas as pd

from ..core.constants import RE_TRACK_CIRCUIT
from .locking import _has_alnum, co_track_circuits, post_key
from .point_format import parse_iso

logger = logging.getLogger(__name__)

ISO_DISTANCE = 120.0    # default required distance (m), next stop signal -> criss-cross point
MAX_CHAIN = 6           # safety limit on movements chained past the next stop signal
ALT_LEG_TCS = 6         # track circuits followed to check the other leg of an isolation point
COND_MARK = '#'         # conditional isolation:  #121N# with S12_12
MAX_ISO_POINTS = 3      # most isolation points tried for one pair

_RE_PT = re.compile(r'(\d{1,4}(?:/\d{1,4})?)')


def _point_ends(tc: str) -> List[str]:
    """Point numbers in a track-circuit name (3+ digits): '107/108T' -> ['107', '108']."""
    return re.findall(r'\d{3,}', str(tc))


def _facing(end: str, direction: str) -> Optional[bool]:
    """DN: odd point ends are facing; UP: even ones.  None if direction unknown."""
    if direction not in ('UP', 'DN'):
        return None
    return (int(end) % 2 == 1) == (direction == 'DN')

Edge = FrozenSet[str]


def _opp(pos: str) -> str:
    return 'R' if pos == 'N' else 'N'


def _tcs(val) -> List[str]:
    return [t for t in (x.strip() for x in re.split(r'[,\s]+', str(val).upper()))
            if RE_TRACK_CIRCUIT.match(t)]


def _points(val) -> List[str]:
    return _RE_PT.findall(str(val))


@dataclass
class _Route:
    name: str
    frm: str                             # starting signal
    to: str                              # next stop signal
    dir: str                             # UP / DN
    seq: List[str]                       # RT-TC + OV-TC in running order (as listed)
    rt_seq: List[str]                    # route track circuits in order (CO: from S route)
    rt: Set[str]                         # route track circuits
    ov: Set[str]                         # overlap track circuits
    has_ov: bool
    pos: Dict[str, str]                  # route + overlap points
    iso: Dict[str, str]                  # isolation points
    rt_pos: Dict[str, str] = field(default_factory=dict)   # route points only

    @property
    def zone(self) -> Set[str]:
        return self.rt | self.ov

    @property
    def fixed(self) -> Dict[str, str]:
        d = dict(self.iso)
        d.update(self.pos)
        return d


@dataclass
class Layout:
    adj: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    tc_pts: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    role: Dict[Tuple[Edge, str], str] = field(default_factory=dict)   # (edge, point) -> toe/N/R
    doubtful: Set[str] = field(default_factory=set)
    checks: List[tuple] = field(default_factory=list)


def _routes(ltoc: pd.DataFrame) -> List[_Route]:
    out = []
    has_ov = [_has_alnum(n) or _has_alnum(r)
              for n, r in zip(ltoc['OV-PT-N'].fillna(''), ltoc['OV-PT-R'].fillna(''))]
    rt_tc = co_track_circuits(ltoc, 'RT-TC')               # CO routes take their S route's
    ov_tc = co_track_circuits(ltoc, 'OV-TC', need=has_ov)
    for k, (_, r) in enumerate(ltoc.iterrows()):
        pos, rt_pos = {}, {}
        for col, p in (('RT-PT-N', 'N'), ('RT-PT-R', 'R'), ('OV-PT-N', 'N'), ('OV-PT-R', 'R')):
            for pt in _points(r.get(col, '')):
                pos[pt] = p
                if col.startswith('RT'):
                    rt_pos[pt] = p
        if 'ISO-PT-N' in r.index or 'ISO-PT-R' in r.index:
            # after locking the ISO column may be shown without N; the
            # positions are in ISO-PT-N / ISO-PT-R
            iso = {}
            for col, p in (('ISO-PT-N', 'N'), ('ISO-PT-R', 'R')):
                for pt in _points(r.get(col, '')):
                    iso[pt] = p
        else:
            iso = {pt: p for pt, p in parse_iso(r.get('ISO', ''))[0]}
        rt_seq = _tcs(rt_tc.iat[k])
        ov_seq = _tcs(ov_tc.iat[k])
        seq = _tcs(r.get('RT-TC', '')) + _tcs(r.get('OV-TC', ''))
        rt = set(rt_seq)
        ov = set(ov_seq) - rt
        out.append(_Route(str(r['FROM-TO']), str(r['FROM']).strip().upper(),
                          str(r['TO']).strip().upper(), str(r.get('DIR', '')).strip().upper(),
                          seq, rt_seq, rt, ov, bool(ov_seq) or has_ov[k], pos, iso, rt_pos))
    _same_berthing(out, [str(u).strip().upper() for u in ltoc['UN']])
    return out


def _same_berthing(routes: List[_Route], lines: List[str]) -> None:
    """
    S / SH / A / CO from one post to the next post on the same line share
    path and berthing: a movement whose route track circuits are all in the
    longest list of its group (typically a shunt route without the berthing
    track circuits, or listed out of order) takes that list, in its order.
    """
    groups: Dict[tuple, List[_Route]] = defaultdict(list)
    for r, un in zip(routes, lines):
        if post_key(r.frm) and post_key(r.to):
            groups[(post_key(r.frm), post_key(r.to), un)].append(r)
    for members in groups.values():
        ref = max((m.rt_seq for m in members), key=lambda q: (len(q), q))
        for m in members:
            if m.rt_seq != ref and set(m.rt_seq) <= set(ref):
                m.rt_seq = list(ref)
                m.rt = set(ref)
                m.ov -= m.rt


def build_layout(routes: List[_Route]) -> Layout:
    lay = Layout()
    all_pts = {p for r in routes for p in list(r.pos) + list(r.iso)}
    for r in routes:
        for tc in r.seq + r.rt_seq:
            nums = set(_point_ends(tc))
            if nums:
                lay.tc_pts[tc] |= {p for p in all_pts if set(p.split('/')) & nums}

    support: Counter = Counter()
    seen: Dict[Tuple[Edge, str], Set[str]] = defaultdict(set)   # (edge, point) -> positions
    who: Dict[Edge, List[str]] = defaultdict(list)
    for r in routes:
        for a, b in zip(r.seq, r.seq[1:]):
            if a == b:
                continue
            e = frozenset((a, b))
            support[e] += 1
            who[e].append(r.name)
            for p in lay.tc_pts.get(a, set()) | lay.tc_pts.get(b, set()):
                seen[(e, p)].add(r.pos.get(p, '?'))

    # prune impossible neighbour counts (out-of-order TC lists)
    nbrs: Dict[str, Set[str]] = defaultdict(set)
    for e in support:
        a, b = tuple(e)
        nbrs[a].add(b); nbrs[b].add(a)
    dropped: Set[Edge] = set()
    for node, ns in nbrs.items():
        # plain track: 2 neighbours; each point end in the track circuit adds a leg
        limit = 2 + len(set(_point_ends(node)))
        if len(ns) > limit:
            ranked = sorted(ns, key=lambda n: (-support[frozenset((node, n))], n))
            keep_n = support[frozenset((node, ranked[limit - 1]))]
            weak = [n for n in ranked[limit:] if support[frozenset((node, n))] < keep_n]
            if len(ranked) - len(weak) > limit:
                weak = ranked[limit:]               # cannot rank: a tie
                lay.doubtful.add(node)              # -> pairs relying on it are held back
            # else the extra links are clearly outvoted by the other routes:
            # set aside and listed for correcting the TOC, results not held back
            for n in weak:
                e = frozenset((node, n))
                dropped.add(e)
                lay.checks.append((
                    ', '.join(sorted(set(who[e]))),
                    'Track circuits possibly listed out of running order',
                    f'{node} would have {len(ns)} neighbours ({", ".join(sorted(ns))}); '
                    f'{node}-{n} is given only by these routes; check RT-TC / OV-TC order'))

    for e in support:
        if e in dropped:
            continue
        a, b = tuple(e)
        lay.adj[a].add(b); lay.adj[b].add(a)

    # role of each edge at a point: toe (used both ways) or the N / R leg.
    # Only for points the TOC shows used both ways (else the legs are unknown).
    both = {p for (_, p), s in seen.items() if {'N', 'R'} <= s}
    for (e, p), s in seen.items():
        if e in dropped or p not in both or '?' in s:
            continue
        lay.role[(e, p)] = 'toe' if {'N', 'R'} <= s else next(iter(s))
    return lay


def _transition(lay: Layout, node: str, prev: Optional[str], nxt: str,
                fixed: Dict[str, str], direction: str = ''
                ) -> Tuple[bool, Tuple[Tuple[str, str], ...]]:
    """
    Can a train at `node`, having come from `prev`, run on to `nxt`?
    Returns (possible, facing points it needs and their positions).
    Facing / trailing by point number and direction (DN odd, UP even); the
    leg (N / R) leading to `nxt` from the TOC layout.
    """
    need = []
    ends_here = set(_point_ends(node))
    for p in lay.tc_pts.get(node, ()):
        rin = lay.role.get((frozenset((prev, node)), p)) if prev else None
        rout = lay.role.get((frozenset((node, nxt)), p))
        ends = [e for e in p.split('/') if e in ends_here]
        fac = _facing(ends[0], direction) if len(ends) == 1 else None
        if fac is None:                             # direction unknown: by the layout
            fac = rout in ('N', 'R') and (rin == 'toe' or prev is None)
        if fac:
            if rout not in ('N', 'R'):
                continue                            # leg not known
            if p in fixed:
                if fixed[p] != rout:
                    return False, ()                # point lies the other way
            else:
                need.append((p, rout))
        elif rin in ('N', 'R') and rout in ('N', 'R') and rin != rout:
            return False, ()                        # trailing: leg to leg not possible
    return True, tuple(need)


@dataclass
class _Overshoot:
    via: Tuple[str, ...]                 # movements followed past the next stop signal
    nodes: Tuple[str, ...]               # track circuits run over
    steps: Tuple[Tuple[Tuple[str, str, str, str], ...], ...]   # per node: facing points
                                         # (point, position, its track circuit, came from)


def _overshoot_paths(lay: Layout, r: _Route, by_post: Dict[str, List[_Route]],
                     dist_from: callable, limit: Optional[float]) -> List[_Overshoot]:
    """
    Overshoot of movement r past its next stop signal, along the movements
    that can start from that signal's post and onwards (see module notes).
    A movement with an overlap stays within its overlap (none listed).
    """
    if r.has_ov or len(r.rt_seq) < 2:
        return []           # overlap = the overshoot; or running direction unknown
    out: List[_Overshoot] = []
    # state: signal reached, node, came from, nodes, steps, via, signals seen
    stack = [(r.to, r.rt_seq[-1], r.rt_seq[-2], (), (), (), {r.to})]
    while stack:
        sig, node, came, nodes, steps, via, seen = stack.pop()
        for c in by_post.get(post_key(sig), ()):
            if not c.rt_seq or c.name == r.name:
                continue
            cur, prev = node, came
            n2, s2, ok = list(nodes), list(steps), True
            direction = r.dir if r.dir in ('UP', 'DN') else c.dir
            for tc in c.rt_seq:
                if tc == cur:
                    continue
                n2.append(tc)
                need = []
                ends_here = set(_point_ends(tc))
                for p, pos in c.rt_pos.items():
                    for e in (x for x in p.split('/') if x in ends_here):
                        if _facing(e, direction) is False:
                            continue            # trailing: runs through
                        if p in r.fixed:
                            if r.fixed[p] != pos:
                                ok = False      # locked by r the other way: not this path
                        else:
                            need.append((p, pos, tc, cur))     # met at tc, coming from cur
                if not ok:
                    break
                s2.append(tuple(need))
                prev, cur = cur, tc
            if not ok or not n2:
                continue
            ov = _Overshoot(via + (c.name,), tuple(n2), tuple(s2))
            out.append(ov)
            d = dist_from(c.to)
            # chain on only while the next signal is known to be within the
            # distance (no chainage: only the movements from the next post)
            if (len(ov.via) < MAX_CHAIN and c.to not in seen
                    and (limit is None or (d is not None and d <= limit))):
                stack.append((c.to, cur, prev, ov.nodes, ov.steps, ov.via, seen | {c.to}))
    return out


def _alt_leg_nodes(lay: Layout, p: str, pos: str, node: str, came: Optional[str],
                   fixed: Dict[str, str], direction: str = '') -> Set[str]:
    """
    Track circuits a train facing point p at `node` can reach within
    ALT_LEG_TCS when p lies `pos` (any way through other unlocked points).
    If that includes the other movement's route, p cannot isolate it.
    """
    fx = dict(fixed); fx[p] = pos
    stack = [(node, came, 0)]
    seen, out = set(), set()
    while stack:
        cur, prev, n = stack.pop()
        for nxt in sorted(lay.adj.get(cur, ())):
            if nxt == prev or (cur, nxt) in seen:
                continue
            good, _ = _transition(lay, cur, prev, nxt, fx, direction)
            if not good:
                continue
            seen.add((cur, nxt))
            out.add(nxt)
            if n + 1 < ALT_LEG_TCS:
                stack.append((nxt, cur, n + 1))
    return out


@dataclass
class IsolationResult:
    toc: pd.DataFrame                    # ISO-ADDED, COND-ISO columns
    report: pd.DataFrame                 # one row per pair that needed a decision
    isolated: Set[FrozenSet[str]]        # pairs that no longer need dispensation
    checks: pd.DataFrame
    summary: Dict[str, int]


def find_isolation(ltoc: pd.DataFrame, sqsh: pd.DataFrame,
                   conflict_marks=('X', '~'), chainage_path: str = '',
                   distance: Optional[float] = ISO_DISTANCE) -> IsolationResult:
    """
    `distance`: required minimum criss-cross distance (m) from the next stop
    signal; meetings farther away need no isolation.  Needs the chainage
    file; without it (or for signals / points missing from it) every
    meeting counts.
    """
    routes = _routes(ltoc)
    by_name = {r.name: r for r in routes}
    names = [r.name for r in routes]
    lay = build_layout(routes)
    by_post: Dict[str, List[_Route]] = defaultdict(list)
    for r in routes:
        if post_key(r.frm):
            by_post[post_key(r.frm)].append(r)

    from .criss_cross import _resolve_ch, load_chainage
    import math
    ch = load_chainage(chainage_path) if chainage_path else None
    cache: dict = {}

    def chain(name: str) -> Optional[float]:
        if ch is None:
            return None
        v = _resolve_ch(ch, name, cache)
        return None if math.isnan(v) else v

    def dist(a: str, b: str) -> Optional[float]:
        ca, cb = chain(a), chain(b)
        return None if ca is None or cb is None else round(abs(ca - cb), 2)

    def meeting_point(nodes: Tuple[str, ...], k: int) -> str:
        """Chainage key of the criss-cross point: the point where the paths meet."""
        for i in range(k, -1, -1):
            if lay.tc_pts.get(nodes[i]):
                ends = {e for pt in lay.tc_pts[nodes[i]] for e in pt.split('/')}
                nums = [n for n in re.findall(r'\d+', nodes[i]) if n in ends]
                if nums:
                    return 'PT' + nums[0]
        return ''

    permitted: Dict[str, Set[str]] = defaultdict(set)
    idx = set(sqsh.index)
    for a, b in itertools.combinations(names, 2):
        if a in idx and b in idx and sqsh.at[a, b] not in conflict_marks \
                and sqsh.at[b, a] not in conflict_marks:
            permitted[a].add(b); permitted[b].add(a)
    need: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for r in routes:
        for p, pos in r.fixed.items():
            need[(p, pos)].add(r.name)

    if distance is not None and ch is None:
        logger.info('Isolation: no chainage file, every overshoot meeting counts')
    paths = {r.name: _overshoot_paths(lay, r, by_post,
                                      lambda s, r=r: dist(r.to, s), distance) for r in routes}
    reach = {n: {pn for ov in paths[n] for pn in ov.nodes} for n in names}

    added: Dict[str, Dict[str, str]] = defaultdict(dict)        # route -> {point: pos}
    added_why: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    cond: Dict[str, Dict[Tuple[str, str], Set[str]]] = defaultdict(lambda: defaultdict(set))
    isolated: Set[FrozenSet[str]] = set()
    rows = []
    stats = Counter()

    def fixed(r: _Route) -> Dict[str, str]:
        d = dict(added[r.name]); d.update(r.fixed)
        return d

    alt_cache: Dict[tuple, Set[str]] = {}

    def alt(src: _Route, p: str, pos: str, at: str, came: Optional[str]) -> Set[str]:
        key = (src.name, p, pos, at, came)
        if key not in alt_cache:
            alt_cache[key] = _alt_leg_nodes(lay, p, pos, at, came, src.fixed, src.dir)
        return alt_cache[key]

    def repercussion(rname: str, p: str, pos: str, other: str) -> Set[str]:
        return (permitted[rname] & need[(p, _opp(pos))]) - {other}

    for a_name, b_name in itertools.combinations(names, 2):
        if b_name not in permitted[a_name]:
            continue
        A, B = by_name[a_name], by_name[b_name]
        if not (reach[a_name] & B.rt) and not (reach[b_name] & A.rt):
            stats['separated'] += 1
            continue
        fx = {**B.fixed, **A.fixed}            # as in the TOC
        fx_added = {**added[b_name], **added[a_name]}   # added here for earlier pairs
        covered = []                    # paths already cut by an added ISO point
        live = []                       # (overshooting movt, description, facing points needed)
        seen_paths = set()
        for src, dst in ((A, B), (B, A)):
            for ov in paths[src.name]:
                k = next((i for i, n in enumerate(ov.nodes) if n in dst.rt), None)
                if k is None:
                    continue                        # never reaches the other's ROUTE
                key = (src.name, ov.nodes[:k + 1])
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                mp = meeting_point(ov.nodes, k)
                d = dist(src.to, mp) if mp else None
                if distance is not None and d is not None and d > distance:
                    continue                        # criss-cross point far enough
                req: Dict[str, Tuple[str, str, str]] = {}
                bad = False
                for step in ov.steps[:k + 1]:
                    for p, pos, at, came in step:
                        if p in req and req[p][0] != pos:
                            bad = True              # same point both ways: not a real path
                        req.setdefault(p, (pos, at, came))
                if bad or any(p in fx and fx[p] != v[0] for p, v in req.items()):
                    continue                        # a facing point lies the other way
                cut = [p + fx_added[p] for p, v in req.items()
                       if p not in fx and p in fx_added and fx_added[p] != v[0]]
                if cut:
                    covered.append(cut[0])
                    continue
                # facing points it needs whose other leg really leads away
                free = {p: v[0] for p, v in req.items() if p not in fx
                        and not (alt(src, p, _opp(v[0]), v[1], v[2]) & dst.rt)}
                where = (f' at {mp} {d:g} m from {src.to}' if d is not None else
                         f' at {mp} (distance not known)' if mp else '')
                desc = (f'{src.name} overshoot past {src.to} via {", ".join(ov.via)}: '
                        + '-'.join((src.rt_seq[-1],) + ov.nodes[:k + 1]) + where)
                live.append((src.name, desc, free, (src.rt_seq[-1],) + ov.nodes[:k + 1]))
        if not live:
            if covered:                             # isolated by an ISO point added earlier
                owners = sorted({n for n in (a_name, b_name)
                                 for c in covered if c[:-1] in added[n]})
                isolated.add(frozenset((a_name, b_name)))
                stats['isolated'] += 1
                rows.append((a_name, b_name, 'isolated', ', '.join(sorted(set(covered))),
                             'already in ISO of ' + ', '.join(owners), '-',
                             'covered by the isolation added for another pair'))
            else:
                stats['separated'] += 1
            continue
        doubtful = any(n in lay.doubtful for x in live for n in x[3])
        if any(not x[2] for x in live):
            desc = next(x[1] for x in live if not x[2])
            rows.append((a_name, b_name, 'not isolable', '', '', '',
                         desc + ' (no free facing point that leads away)'))
            stats['not isolable'] += 1
            continue

        chosen = _choose_points(live, lambda p, pos: min(
            len(repercussion(a_name, p, pos, b_name)), len(repercussion(b_name, p, pos, a_name))))
        if chosen is None:
            ev = '; '.join(x[1] for x in live[:3])
            rows.append((a_name, b_name, 'not isolable', '', '', '',
                         ev + ' (every setting of the free facing points leads onto the route)'))
            stats['not isolable'] += 1
            continue

        over = {x[0] for x in live}
        parts, kinds, reps = [], [], []
        for p, pos in chosen:
            ra = repercussion(a_name, p, pos, b_name)
            rb = repercussion(b_name, p, pos, a_name)
            # least repercussion; on a tie the movement that overshoots
            cands = [(len(ra), a_name not in over, a_name, b_name, ra),
                     (len(rb), b_name not in over, b_name, a_name, rb)]
            _, _, owner, other, rep = min(cands, key=lambda c: c[:3])
            if doubtful:
                kinds.append('check layout')
            elif not rep:
                added[owner][p] = pos
                added_why[owner][p + pos].add(other)
                kinds.append(f'ISO of {owner}')
            else:
                cond[a_name][(p, pos)].add(b_name)
                cond[b_name][(p, pos)].add(a_name)
                kinds.append('conditional')
            parts.append(p + pos)
            reps.append(', '.join(sorted(rep)) if rep else '-')
        status = 'check layout' if doubtful else 'isolated'
        if not doubtful:
            isolated.add(frozenset((a_name, b_name)))
        stats[status] += 1
        evidence = '; '.join(x[1] for x in live[:3])
        rows.append((a_name, b_name, status, ', '.join(parts), '; '.join(kinds),
                     ' | '.join(reps), evidence))

    out = ltoc.copy()
    iso_add, cond_col = [], []
    for name in out['FROM-TO'].astype(str):
        iso_add.append(', '.join(f'{p}{pos} (for {", ".join(sorted(added_why[name][p + pos]))})'
                                 for p, pos in sorted(added[name].items())))
        cond_col.append('; '.join(
            f'{COND_MARK}{p}{pos}{COND_MARK} with {", ".join(sorted(others))} set'
            for (p, pos), others in sorted(cond[name].items())))
    out['ISO-ADDED'] = iso_add
    out['COND-ISO'] = cond_col

    report = pd.DataFrame(rows, columns=['MOVT-1', 'MOVT-2', 'RESULT', 'ISOLATION POINTS',
                                         'LOCKED AS', 'MOVTS NEWLY LOCKED (repercussion)',
                                         'OVERSHOOT PATH (track circuits)'])
    checks = pd.DataFrame(lay.checks, columns=['ROUTE', 'CHECK', 'DETAIL'])
    summary = dict(stats)
    summary['iso points added'] = sum(len(v) for v in added.values())
    summary['conditional entries'] = sum(len(v) for v in cond.values()) // 2
    logger.info('Isolation: %s', summary)
    return IsolationResult(out, report, isolated, checks, summary)


def _choose_points(live, cost) -> Optional[List[Tuple[str, str]]]:
    """
    Smallest set of (point, position) that cuts every overshoot path, each
    point in one position only; among equal sizes the least repercussion,
    then Normal before Reverse.  None if no set of <= MAX_ISO_POINTS works.
    A path is cut when one of the facing points it needs lies the other way.
    """
    cands = sorted({(p, _opp(pos)) for x in live for p, pos in x[2].items()})
    for k in range(1, MAX_ISO_POINTS + 1):
        best = None
        for combo in itertools.combinations(cands, k):
            sel = dict(combo)
            if len(sel) < k:
                continue                            # same point in both positions
            if all(any(p in sel and sel[p] != pos for p, pos in x[2].items())
                   for x in live):
                key = (sum(cost(p, pos) for p, pos in combo),
                       sum(pos == 'R' for _, pos in combo), combo)
                if best is None or key < best[0]:
                    best = (key, list(combo))
        if best:
            return best[1]
    return None


def iso_text_with_added(iso_val: str, added: Dict[str, str]) -> str:
    """Existing ISO cell plus added isolation points ('121, 111/112' + {'135/136': 'N'})."""
    have = {p for p, _ in parse_iso(iso_val)[0]}
    extra = [p + pos for p, pos in sorted(added.items()) if p not in have]
    base = str(iso_val or '').strip().strip(',')
    return ', '.join(x for x in [base] + extra if x)


def apply_added_iso(toc: pd.DataFrame, result: IsolationResult) -> pd.DataFrame:
    """Formatted TOC with the unconditional isolation points added to ISO."""
    toc = toc.copy()
    add = {}
    for name, txt in zip(result.toc['FROM-TO'], result.toc['ISO-ADDED']):
        pts = {}
        for item in filter(None, (x.strip() for x in re.split(r',\s*(?![^()]*\))', txt))):
            m = re.match(r'(\d{1,4}(?:/\d{1,4})?)([NR])', item)
            if m:
                pts[m.group(1)] = m.group(2)
        if pts:
            add[name] = pts
    toc['ISO'] = [iso_text_with_added(iso, add.get(n, {}))
                  for n, iso in zip(toc['FROM-TO'], toc['ISO'])]
    return toc


def isolated_pairs_sheet(sqsh: pd.DataFrame, isolated: Set[FrozenSet[str]], mark: str = 'I'):
    """Square sheet with isolated pairs marked (for the ISO output)."""
    s = sqsh.copy()
    for pair in isolated:
        a, b = tuple(pair)
        if a in s.index and b in s.columns:
            s.at[a, b] = mark
            s.at[b, a] = mark
    return s
