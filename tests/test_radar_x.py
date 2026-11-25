import unittest
from dataclasses import replace
from chomp.research.radar_x import Evidence, LANES, discover


def row(a=30000,b=31000,lane='energy',kind='peak',strength=.5,link=''):
    return Evidence('s',a,b,lane,kind,strength,'fixture',link=link)


def run(rows,budget=40,duration=3600000,**kwargs):
    availability={l:any(e.lane==l for e in rows) for l in LANES}
    return discover('s',duration,rows,availability,budget,**kwargs)


class RadarXTests(unittest.TestCase):
    def test_budget(self):
        for k in (0,1,5,20):
            self.assertLessEqual(len(run([row(i*10000+1,i*10000+2) for i in range(30)],k)['proposals']),k)
    def test_distinct_nearby(self):
        p=run([row(),row(31000,32000,'dialogue','speech'),row(32000,33000,'visual','scene')],3)['proposals']
        self.assertEqual(len(p),3)
    def test_linked_event(self):
        p=run([row(link='lead'),row(41000,42000,'chat','reaction',link='lead')],2)['proposals']
        self.assertEqual(len(p),1)
        self.assertEqual(p[0]['core_ms'],[30000,42000])
        self.assertEqual(p[0]['lanes'],['chat','energy'])
    def test_duplicate(self):
        self.assertEqual(run([row(),row()],2)['diagnostics']['observations'],1)
    def test_missing_modalities(self):
        for lane in LANES:
            p=run([row(lane=lane)],1)['proposals'][0]
            self.assertEqual(p['lanes'],[lane])
            self.assertEqual(set(p['missing_lanes']),set(LANES)-{lane})
    def test_short(self):
        p=run([row(0,5)],1,10)['proposals'][0]
        self.assertEqual(p['context_ms'],[0,10])
    def test_determinism(self):
        rows=[row(),row(40000,42000,'chat','chat')]
        self.assertEqual(run(rows),run(rows[::-1]))
    def test_invalid(self):
        for e in [row(-1,2),row(2,1),row(strength=float('nan')),row(strength=True),replace(row(),source_id='other'),row(0,61000)]:
            with self.assertRaises(ValueError): run([e])
    def test_context_cap(self):
        p=run([row(50000,110000,'dialogue','speech')],1)['proposals'][0]
        self.assertEqual(p['context_ms'],[50000,110000])
    def test_exploration_unverified(self):
        p=run([],20)['proposals']
        self.assertEqual(len(p),2)
        self.assertTrue(all(x['core_ms'] is None and not x['evidence_ids'] for x in p))
    def test_quiet_with_noise(self):
        rows=[row(i*10000+1,i*10000+2,strength=1) for i in range(30)]+[row(60000,62000,'dialogue','speech',.1)]
        self.assertIn('dialogue',{l for p in run(rows,5)['proposals'] for l in p['lanes']})
    def test_multihour(self):
        rows=[row(i*15000+1,i*15000+2) for i in range(800)]
        p=run(rows,120,duration=14400000)['proposals']
        self.assertEqual(len(p),120)
    def test_available_but_empty(self):
        r=discover('s',10000,[],dict.fromkeys(LANES,True),1)
        self.assertEqual(r['proposals'],[])
    def test_unavailable_rejects_evidence(self):
        with self.assertRaises(ValueError): discover('s',100000,[row()],dict.fromkeys(LANES,False),1)
    def test_modes(self):
        for mode in ('weighted','rrf','adaptive'):
            self.assertEqual(len(run([row()],1,method=mode)['proposals']),1)

if __name__=='__main__': unittest.main()
