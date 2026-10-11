"""
Isolation of simultaneous movements ("ISO preferred over dispensation").

For every pair of movements that the locking allows at the same time,
find whether a train on one can run onto the other's route or overlap
through points that neither route locks.  If so, find the points outside
both routes and overlaps that, locked in the right position, cut every
such connection, choosing the points and positions that lock the fewest
other movements (minimum repercussion):

* no other movement needs the opposite position  -> the point is added to
  the route's ISO column (ordinary isolation locking);
* otherwise -> conditional isolation: the point is locked only while both
  movements are set, shown in the COND-ISO column as  #121N# with S12_12.

Isolated pairs need no dispensation; pairs that cannot be isolated keep it.

Track layout
------------
Read from the TOC itself: RT-TC followed by OV-TC lists a route's track
circuits in the order the train runs, and the route's point positions say
which leg of each point it takes.  So two consecutive track circuits are
neighbours, and the edge between a point's track circuit and the next one
is a leg of that point if every route over it sets the point the same
way.  A track circuit has two neighbours, plus one for each point end in
it (a point adds a third leg); where the TOC implies more, the order is doubtful
(listed in the layout check) and pairs that depend on it are reported for
checking, not changed.
"""

import itertools
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Set, Tuple

import pandas as pd

from ..core.constants import RE_TRACK_CIRCUIT
from .point_format import parse_iso

logger = logging.getLogger(__name__)

MAX_HOPS = 4            # track-circuit steps searched between two movements
MAX_PATHS = 400         # per route, safety limit for the path search
COND_MARK = '#'         # conditional isolation:  #121N# with S12_12

_RE_PT = re.compile(r'(\d{1,4}(?:/\d{1,4})?)')
_RE_POINT_TC = re.compile(r'^\d+(?:/\d+)*[A-Z]?T$')

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
    seq: List[str]                       # RT-TC + OV-TC in running order
    zone: Set[str]                       # all track circuits the route occupies / locks
    pos: Dict[str, str]                  # route + overlap points
    iso: Dict[str, str]                  # isolation points

    @property
    def fixed(self) -> Dict[str, str]:
        d = dict(self.iso)
        d.update(self.pos)
        return d


@dataclass
class Layout:
    edges: Dict[Edge, int] = field(default_factory=dict)              # support (routes)
    adj: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    ctrl: Dict[Edge, Dict[str, str]] = field(default_factory=dict)    # leg of point: position
    doubtful: Set[str] = field(default_factory=set)                   # nodes with pruned edges
    checks: List[tuple] = field(default_factory=list)


def _routes(ltoc: pd.DataFrame) -> List[_Route]:
    out = []
    for _, r in ltoc.iterrows():
        pos = {}
        for col, p in (('RT-PT-N', 'N'), ('RT-PT-R', 'R'), ('OV-PT-N', 'N'), ('OV-PT-R', 'R')):
            for pt in _points(r.get(col, '')):
                pos[pt] = p
        if 'ISO-PT-N' in r.index or 'ISO-PT-R' in r.index:
            # after locking the ISO column is shown without N/R; the
            # positions are in ISO-PT-N / ISO-PT-R
            iso = {}
            for col, p in (('ISO-PT-N', 'N'), ('ISO-PT-R', 'R')):
                for pt in _points(r.get(col, '')):
                    iso[pt] = p
        else:
            iso = {pt: p for pt, p in parse_iso(r.get('ISO', ''))[0]}
        seq = _tcs(r.get('RT-TC', '')) + _tcs(r.get('OV-TC', ''))
        zone = set(_tcs(r.get('TCK', ''))) | set(seq)
        out.append(_Route(str(r['FROM-TO']), seq, zone, pos, iso))
    return out


def build_layout(routes: List[_Route]) -> Layout:
    lay = Layout()
    all_pts = {p for r in routes for p in list(r.pos) + list(r.iso)}
    tc_pts: Dict[str, Set[str]] = defaultdict(set)
    for r in routes:
        for tc in r.seq:
            if _RE_POINT_TC.match(tc):
                nums = set(re.findall(r'\d+', tc))
                tc_pts[tc] |= {p for p in all_pts if set(p.split('/')) & nums}

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
            for p in tc_pts.get(a, set()) | tc_pts.get(b, set()):
                seen[(e, p)].add(r.pos.get(p, '?'))

    # prune impossible neighbour counts (out-of-order TC lists)
    nbrs: Dict[str, Set[str]] = defaultdict(set)
    for e in support:
        a, b = tuple(e)
        nbrs[a].add(b); nbrs[b].add(a)
    dropped: Set[Edge] = set()
    for node, ns in nbrs.items():
        # plain track: 2 neighbours; each point end in the track circuit adds a leg
        ends = len(set(re.findall(r'\d+', node))) if _RE_POINT_TC.match(node) else 0
        limit = 2 + ends if ends else 2
        if len(ns) > limit:
            ranked = sorted(ns, key=lambda n: -support[frozenset((node, n))])
            keep_n = support[frozenset((node, ranked[limit - 1]))]
            weak = [n for n in ranked[limit:]
                    if support[frozenset((node, n))] < keep_n]
            if len(ranked) - len(weak) > limit:
                weak = ranked[limit:]               # cannot rank: mark all extras
            lay.doubtful.add(node)
            for n in weak:
                e = frozenset((node, n))
                dropped.add(e)
                lay.checks.append((
                    ', '.join(sorted(set(who[e]))),
                    'Track circuits possibly listed out of running order',
                    f'{node} would have {len(ns)} neighbours ({", ".join(sorted(ns))}); '
                    f'{node}-{n} is given only by these routes; check RT-TC / OV-TC order'))

    for e, n in support.items():
        if e in dropped:
            continue
        lay.edges[e] = n
        a, b = tuple(e)
        lay.adj[a].add(b); lay.adj[b].add(a)

    # a point is "known" at its track circuit if routes use it both ways there
    both: Set[str] = set()
    for (e, p), s in seen.items():
        if {'N', 'R'} <= s:
            both.add(p)
    for e in lay.edges:
        c = {}
        for p in tc_pts.get(min(e), set()) | tc_pts.get(max(e), set()):
            s = seen.get((e, p), set())
            if len(s) == 1 and '?' not in s and p in both:
                c[p] = next(iter(s))
        if c:
            lay.ctrl[e] = c
    return lay


def _route_paths(lay: Layout, r: _Route) -> Dict[str, List[Tuple[Edge, ...]]]:
    """Paths of <= MAX_HOPS steps leaving r's zone, by end node."""
    out: Dict[str, List[Tuple[Edge, ...]]] = defaultdict(list)
    count = 0
    stack = [(s, (), (s,)) for s in r.zone if s in lay.adj]
    while stack and count < MAX_PATHS:
        node, edges, nodes = stack.pop()
        for nxt in lay.adj[node]:
            if nxt in nodes or nxt in r.zone:
                continue
            e = frozenset((node, nxt))
            path = edges + (e,)
            out[nxt].append(path)
            count += 1
            if len(path) < MAX_HOPS:
                stack.append((nxt, path, nodes + (nxt,)))
    return out


@dataclass
class IsolationResult:
    toc: pd.DataFrame                    # ISO updated, ISO-ADDED, COND-ISO columns
    report: pd.DataFrame                 # one row per pair that needed a decision
    isolated: Set[FrozenSet[str]]        # pairs that no longer need dispensation
    checks: pd.DataFrame
    summary: Dict[str, int]


def find_isolation(ltoc: pd.DataFrame, sqsh: pd.DataFrame,
                   conflict_marks=('X', '~')) -> IsolationResult:
    routes = _routes(ltoc)
    by_name = {r.name: r for r in routes}
    names = [r.name for r in routes]
    lay = build_layout(routes)

    # permitted partners and who needs each point position
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

    paths = {r.name: _route_paths(lay, r) for r in routes}
    reach = {n: set(paths[n]) for n in names}

    added: Dict[str, Dict[str, str]] = defaultdict(dict)        # route -> {point: pos}
    added_why: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    cond: Dict[str, Dict[Tuple[str, str], Set[str]]] = defaultdict(lambda: defaultdict(set))
    isolated: Set[FrozenSet[str]] = set()
    rows = []
    stats = Counter()

    def fixed(r: _Route) -> Dict[str, str]:
        d = dict(added[r.name]); d.update(r.fixed)
        return d

    def enabled(e: Edge, fx: Dict[str, str]) -> bool:
        return all(fx.get(p, pos) == pos for p, pos in lay.ctrl.get(e, {}).items())

    def repercussion(rname: str, p: str, pos: str, other: str) -> Set[str]:
        return (permitted[rname] & need[(p, _opp(pos))]) - {other}

    for a_name, b_name in itertools.combinations(names, 2):
        if b_name not in permitted[a_name]:
            continue
        A, B = by_name[a_name], by_name[b_name]
        if not (reach[b_name] & A.zone) and not (reach[a_name] & B.zone):
            stats['separated'] += 1
            continue
        fa, fb = fixed(A), fixed(B)
        if any(p in fb and fb[p] != pos for p, pos in fa.items()):
            continue                                    # already conflicting points
        fx = {**fb, **fa}
        live = []
        for src, dst in ((b_name, A), (a_name, B)):
            for node in reach[src] & dst.zone:
                for path in paths[src][node]:
                    if (set().union(*path) - {node}) & dst.zone:
                        continue            # reaches dst earlier on a shorter path
                    if all(enabled(e, fx) for e in path):
                        live.append(path)
        if not live:
            stats['separated'] += 1
            continue
        doubtful = any(n in lay.doubtful for path in live for e in path for n in e)
        route_pts = set(A.pos) | set(B.pos) | set(A.iso) | set(B.iso)
        options = []
        for path in live:
            opts = set()
            for e in path:
                for p, pos in lay.ctrl.get(e, {}).items():
                    if p not in route_pts:
                        opts.add((p, _opp(pos)))
            options.append(opts)
        if any(not o for o in options):
            via = ' - '.join(sorted({n for p, o in zip(live, options) if not o
                                     for e in p for n in e}))
            rows.append((a_name, b_name, 'not isolable', '', '', '',
                         'connected through ' + via + ' with no free point to isolate'))
            stats['not isolable'] += 1
            continue

        # greedy cover: most paths cut, then least repercussion, Normal first
        todo = list(range(len(live)))
        chosen = []
        while todo:
            cand = Counter(o for i in todo for o in options[i])

            def cost(o):
                p, pos = o
                return min(len(repercussion(a_name, p, pos, b_name)),
                           len(repercussion(b_name, p, pos, a_name)))
            best = min(cand, key=lambda o: (-cand[o], cost(o), o[1] != 'N', o[0]))
            chosen.append(best)
            todo = [i for i in todo if best not in options[i]]

        parts, kinds, reps = [], [], []
        for p, pos in chosen:
            ra = repercussion(a_name, p, pos, b_name)
            rb = repercussion(b_name, p, pos, a_name)
            owner, other, rep = (a_name, b_name, ra) if len(ra) <= len(rb) else (b_name, a_name, rb)
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
        evidence = '; '.join('-'.join(_order(p)) for p in live[:3])
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
                                         'CONNECTION (track circuits)'])
    checks = pd.DataFrame(lay.checks, columns=['ROUTE', 'CHECK', 'DETAIL'])
    summary = dict(stats)
    summary['iso points added'] = sum(len(v) for v in added.values())
    summary['conditional entries'] = sum(len(v) for v in cond.values()) // 2
    logger.info('Isolation: %s', summary)
    return IsolationResult(out, report, isolated, checks, summary)


def _order(path: Tuple[Edge, ...]) -> List[str]:
    """Edges -> node sequence for the report."""
    if len(path) == 1:
        return sorted(path[0])
    seq = list(path[0] - path[1]) + list(path[0] & path[1])
    for e in path[1:]:
        nxt = list(e - {seq[-1]})
        seq += nxt
    return seq


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
