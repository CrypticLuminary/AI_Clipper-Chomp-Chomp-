"""DS-01 experimental backend; no inference or production pipeline changes."""
from dataclasses import dataclass, asdict
from collections import Counter, defaultdict
from hashlib import sha256
from functools import cached_property
import json
import math

LANES = ('energy', 'acoustic', 'dialogue', 'chat', 'visual')
PADDING = {'energy': (6000, 8000), 'acoustic': (7000, 9000),
           'dialogue': (12000, 14000), 'chat': (10000, 8000), 'visual': (7000, 9000)}

@dataclass(frozen=True)
class Evidence:
    source_id: str
    start_ms: int
    end_ms: int
    lane: str
    kind: str
    strength: float
    producer: str
    description: str = ''
    link: str = ''  # optional upstream association hint, NOT a gold event ID

    @cached_property
    def id(self):
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def validate(self, source, duration):
        if self.source_id != source or not isinstance(source, str) or not source:
            raise ValueError('source mismatch')
        if (type(self.start_ms) is not int or type(self.end_ms) is not int
                or not 0 <= self.start_ms < self.end_ms <= duration or self.end_ms-self.start_ms > 60000):
            raise ValueError('invalid evidence span')
        if self.lane not in LANES or not all(isinstance(x, str) for x in
                (self.kind, self.producer, self.description, self.link)) or not self.kind or not self.producer:
            raise ValueError('invalid evidence metadata')
        if type(self.strength) not in (int, float) or not math.isfinite(self.strength) or not 0 <= self.strength <= 1:
            raise ValueError('invalid strength')


def adapt(source, observations):
    mapping = {'audio': 'energy', 'transcript': 'dialogue', 'chat': 'chat'}
    return [Evidence(source, o.start_ms, o.end_ms, mapping[o.modality], o.kind,
                     o.strength, 'chomp.signals@24bf2f5', o.evidence)
            for o in observations if o.modality in mapping]


def discover(source, duration, evidence, availability, budget, *, method='adaptive',
             association='typed', adaptive_windows=True, balance=True, exploration=True):
    if type(duration) is not int or duration <= 0 or type(budget) is not int or budget < 0:
        raise ValueError('invalid duration or budget')
    if not isinstance(source, str) or not source or set(availability) != set(LANES) or any(type(v) is not bool for v in availability.values()):
        raise ValueError('explicit lane availability required')
    if method not in ('weighted', 'rrf', 'adaptive') or association not in ('typed', 'fixed', 'overlap', 'none'):
        raise ValueError('unknown method or association')
    unique = {}
    for e in evidence:
        e.validate(source, duration)
        if not availability[e.lane]:
            raise ValueError('evidence in unavailable lane')
        unique[e.id] = e
    rows = sorted(unique.values(), key=lambda e: (e.start_ms, e.end_ms, e.id))
    groups = []
    active = defaultdict(list)
    for e in rows:
        # Same producer/type overlap OR explicit upstream linkage. Never proximity alone.
        key = ('link', e.link) if e.link else ('type', e.lane, e.kind, e.producer)
        if association in ('fixed', 'overlap'):
            key = ('all',)
        group = active[key][-1] if active[key] else None
        anchor = (e.start_ms + e.end_ms) // 2
        if group:
            gap = e.start_ms - group['end']
            eligible = (association == 'fixed' and abs(anchor-group['anchor']) <= 2600
                        or association == 'overlap' and gap < 0
                        or association == 'typed' and gap < (15000 if e.link else 0))
            eligible = eligible and max(e.end_ms, group['end']) - group['start'] <= 60000
        else:
            eligible = False
        if eligible:
            group['items'].append(e)
            group['end'] = max(group['end'], e.end_ms)
        else:
            group = {'start': e.start_ms, 'end': e.end_ms, 'anchor': anchor, 'items': [e]}
            groups.append(group)
            active[key].append(group)
    # One ranked list per lane, with ties receiving the same competition rank.
    lanes = defaultdict(list)
    for i, g in enumerate(groups):
        strengths = {}
        for e in g['items']:
            strengths[e.lane] = max(strengths.get(e.lane, 0), e.strength)
        g['strengths'] = strengths
        g['ranks'] = {}
        for lane, strength in strengths.items():
            lanes[lane].append((strength, i))
    for lane, entries in lanes.items():
        last, rank = None, 0
        for j, (s, i) in enumerate(sorted(entries, reverse=True), 1):
            if s != last:
                rank, last = j, s
            groups[i]['ranks'][lane] = rank
    for g in groups:
        ranks = g['ranks']
        if method == 'rrf':
            g['score'] = sum(1/(60+r) for r in ranks.values())
        else:
            values = [1-(r-1)/max(1, len(lanes[l])) for l,r in ranks.items()]
            g['score'] = sum(values)/len(values) if method == 'weighted' else max(values)
    counts, blocks = Counter(), Counter()
    selected = []
    reserve = min(budget//10, max(0, math.ceil(duration/300000)-1)) if method == 'adaptive' and exploration else 0
    remaining = list(groups)
    while remaining and len(selected) < budget-reserve:
        def priority(g):
            block = g['anchor']//300000
            bonus = (0.35/(1+blocks[block]) + 0.35*max(1/(1+counts[l]) for l in g['ranks'])) if method == 'adaptive' and balance else 0
            return (g['score']+bonus, -g['anchor'], g['items'][0].id)
        g = max(remaining, key=priority)
        remaining.remove(g)
        counts.update(g['ranks'].keys())
        blocks[g['anchor']//300000] += 1
        selected.append(g)
    missing = [l for l in LANES if not availability[l]]
    proposals = []
    def emit(g, exploration=False):
        core = [g['start'], g['end']]
        anchor = g['anchor']
        if exploration:
            start, end = max(0, anchor-9000), min(duration, anchor+14000)
        elif adaptive_windows:
            pre = max(PADDING[l][0] for l in g['ranks'])
            post = max(PADDING[l][1] for l in g['ranks'])
            start, end = max(0, core[0]-pre), min(duration, core[1]+post)
        else:
            start, end = max(0, anchor-9000), min(duration, anchor+14000)
        if end-start > 60000:
            start = min(core[0], max(start, core[1]-60000))
            end = start+60000
        ids = sorted(e.id for e in g['items'])
        proposals.append({'candidate_id': sha256(json.dumps([source, core, ids]).encode()).hexdigest(),
                          'source_id': source, 'anchor_ms': anchor, 'core_ms': None if exploration else core,
                          'context_ms': [start, end], 'evidence_ids': ids, 'lanes': ['exploration'] if exploration else sorted(g['ranks']),
                          'lane_ranks': g['ranks'], 'lane_strengths': g.get('strengths', {}), 'priority': g.get('score'),
                          'missing_lanes': missing, 'verification_status': 'unverified',
                          'rationale': 'unexamined interval' if exploration else association+' association; '+method+' allocation'})
    for g in selected:
        emit(g)
    # Sample the largest remaining gaps, not evidence-free claims of interestingness.
    for _ in range(reserve):
        covered = sorted(p['context_ms'] for p in proposals)
        gaps, end = [], 0
        for a,b in covered + [[duration,duration]]:
            if a > end:
                gaps.append((a-end,end,a))
            end = max(end,b)
        if not gaps:
            break
        length,a,b = max(gaps)
        if length < 1000:
            break
        anchor = (a+b)//2
        emit({'start': anchor, 'end': anchor+1, 'anchor': anchor, 'items': [], 'ranks': {}}, True)
    return {'schema_version': 'chomp-radar-x/1', 'source_id': source, 'duration_ms': duration,
            'budget': budget, 'availability': dict(availability), 'proposals': proposals,
            'diagnostics': {'observations': len(rows), 'groups': len(groups), 'unselected_groups': len(remaining),
                            'lane_counts': dict(counts), 'unrepresented_blocks': [i for i in range(math.ceil(duration/300000)) if i not in blocks]}}
