import sys,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone,date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from custom_components.docan_deye_ems.learning import Learning
from custom_components.docan_deye_ems.model import day_bounds

class LearningTests(unittest.TestCase):
    def setUp(self):self.now=datetime(2026,9,20,12,tzinfo=timezone.utc)
    def test_cold_start_is_explicit_and_gaps_not_integrated(self):
        m=Learning();rows,meta=m.load_forecast(self.now,self.now+timedelta(hours=1),self.now,'UTC',.4)
        self.assertEqual(meta['source'],'entered_baseline');self.assertAlmostEqual(sum(r['kwh'] for r in rows),.4)
        v={'load_power':400,'solar_power':0,'grid_power':400,'battery_power':0}
        m.observe(self.now,v,.2,0);m.observe(self.now+timedelta(hours=1),v,.2,0)
        self.assertEqual(m.metrics(self.now,'UTC')['days'],[])
        m.observe(self.now+timedelta(hours=1,seconds=30),v,.2,0)
        self.assertEqual(m.metrics(self.now,'UTC')['days'][0]['coverage_seconds'],30)
    def test_load_learns_only_complete_distinct_days(self):
        m=Learning()
        for day in range(1,4):
            for i in range(4):
                stamp=int((self.now-timedelta(days=day)+timedelta(minutes=i*15)).timestamp())
                m.data['intervals'][str(stamp)]={'seconds':900,'load':.25}
        rows,meta=m.load_forecast(self.now,self.now+timedelta(hours=1),self.now,'UTC',.4)
        self.assertEqual(meta['source'],'learned_profile');self.assertAlmostEqual(sum(r['kwh'] for r in rows),1)
    def test_price_anticipation_requires_history_and_preserves_dst_day(self):
        m=Learning();a,b=day_bounds(date(2026,10,25),'Europe/Amsterdam')
        self.assertEqual(m.anticipate(a,b,'Europe/Amsterdam'),[])
        for day in range(1,5):
            c,d=day_bounds(date(2026,10,25)-timedelta(days=day),'Europe/Amsterdam')
            while c<d:
                m.data['prices'][str(int(c.timestamp()))]={'import':.1*day,'export':.05}
                c+=timedelta(minutes=15)
        rows=m.anticipate(a,b,'Europe/Amsterdam')
        self.assertEqual(len(rows),100);self.assertTrue(all(r['origin']=='anticipated' for r in rows))
        self.assertGreater(rows[0]['import_high'],rows[0]['import'])
        m.learn_prices(rows,self.now)
        self.assertNotIn(str(int(a.timestamp())),m.data['prices'])
    def test_dedicated_efficiency_and_direction_changes(self):
        m=Learning();v={'load_power':500,'solar_power':0,'grid_power':2500,'battery_power':-1800,'battery_ac_power':-2000}
        for i in range(70):m.observe(self.now+timedelta(seconds=30*i),v,.2,0)
        e=m.efficiencies(95,95,True)
        self.assertEqual(e['charge_source'],'measured_dedicated_ac_dc');self.assertAlmostEqual(e['charge'],90)
        self.assertEqual(e['discharge_source'],'entered')
        n=m.data['efficiency']['charge']['samples']
        m.observe(self.now+timedelta(seconds=30*70),{**v,'battery_power':1800},.2,0)
        self.assertEqual(m.data['efficiency']['charge']['samples'],n)
    def test_forecast_error_requires_prior_forecast_complete_interval(self):
        m=Learning();key=str(int(self.now.timestamp()))
        m.data['intervals'][key]={'seconds':900,'load':.3,'pv':.1,'import':.2,'export':0,'cost':.04,'unpriced_export':0}
        m.data['predictions'][key]={'load':.25,'pv':.2,'cost':.05}
        metrics=m.metrics(self.now+timedelta(minutes=15),'UTC')
        self.assertEqual(metrics['compared_intervals'],1)
        self.assertAlmostEqual(metrics['load_forecast_mae_w'],200)
        self.assertAlmostEqual(metrics['solar_forecast_mae_w'],400)

if __name__=='__main__':unittest.main()
