"""Synthetic equal-input DS-01 experiment. Run with PYTHONPATH=. python tests/ds01_benchmark.py."""
import json, random, time, tracemalloc
from chomp.models import Observation
from chomp.discovery import discover as baseline
from chomp.research.radar_x import adapt, discover, LANES


def fixture(seed, hours=1):
    rng = random.Random(seed)
    d = hours*3600000
    rows, gold = [], []
    for block in range(d//300000):
        b = block*300000
        for j in range(12):
            a = b+rng.randrange(1000,290000)
            rows.append(Observation('audio','peak',a,a+500,rng.uniform(.7,1),'noise'))
        for off, modality, kind, length, strength in [(30000,'transcript','speech_window',16000,.3),
                (34500,'audio','reaction',500,.28),(170000,'chat','audience_swell',2000,.45)]:
            a = b+off
            if seed%3==0 and modality=='chat': continue
            if seed%5==0 and modality=='transcript': continue
            rows.append(Observation(modality,kind,a,a+length,strength,'fixture'))
            gold.append((a,a+length,modality,max(0,a-4000),min(d,a+length+4000)))
    return d,rows,gold


def score(proposals, gold):
    hits = [[i for i,(a,b) in enumerate(proposals) if 5*max(0,min(b,y)-max(a,x))>=4*(y-x)] for x,y,_,_,_ in gold]
    contexts = sum(any(proposals[i][0]<=e and proposals[i][1]>=f for i in h) for h,(_,_,_,e,f) in zip(hits,gold))
    dup = sum(max(0,len(h)-1) for h in hits)
    merge = sum(sum(i in h for h in hits)>1 for i in range(len(proposals)))
    per = {m:[sum(bool(h) for h,g in zip(hits,gold) if g[2]==m),sum(g[2]==m for g in gold)] for m in ('audio','transcript','chat')}
    return {'hits':sum(map(bool,hits)), 'events':len(gold),'context_hits':contexts,'extra_event_hits':dup,'multi_event_windows':merge,
            'proposals':len(proposals),'footage_ms':sum(b-a for a,b in proposals),'per_modality':per}


def run():
    results=[]
    for seed in (11,12,15,17,19):
        duration, obs, gold = fixture(seed)
        evidence=adapt('fixture',obs)
        available={l:any(e.lane==l for e in evidence) for l in LANES}
        for budget in (10,20,40,60,80):
            for method,kwargs in [('baseline',{}),('weighted',{}),('rrf',{}),('adaptive',{}),
                   ('fixed_assoc',{'association':'fixed'}),('fixed_window',{'adaptive_windows':False}),
                   ('no_balance',{'balance':False}),('no_explore',{'exploration':False})]:
                tracemalloc.start(); t=time.perf_counter()
                if method=='baseline':
                    spans=[(p.start_ms,p.end_ms) for p in baseline(obs,[],duration,max_proposals=budget)]
                else:
                    spans=[tuple(p['context_ms']) for p in discover('fixture',duration,evidence,available,budget,
                        method=method if method in ('weighted','rrf') else 'adaptive',**kwargs)['proposals']]
                elapsed=time.perf_counter()-t; _,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
                results.append(dict(seed=seed,budget=budget,method=method,seconds=elapsed,peak_bytes=peak,**score(spans,gold)))
    return results

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
