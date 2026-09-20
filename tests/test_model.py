import importlib.util,unittest
from pathlib import Path
from datetime import datetime,date,timedelta,timezone
P=Path(__file__).resolve().parents[1]/'custom_components/docan_deye_ems/model.py'
s=importlib.util.spec_from_file_location('model',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class ModelTests(unittest.TestCase):
 def rows(self,day,step=15):
  a,b=m.day_bounds(day,'Europe/Amsterdam');out=[]
  while a<b:out.append({'start':a.isoformat(),'end':(a+timedelta(minutes=step)).isoformat(),'value':-0.1 if a.hour<8 else 0.3});a+=timedelta(minutes=step)
  return out
 def test_day_length_dst_and_prices(self):
  for d,n in [(date(2026,3,29),92),(date(2026,9,19),96),(date(2026,10,25),100)]:
   p=m.normalize_periods(self.rows(d),d,'Europe/Amsterdam');self.assertEqual(len(p),n);self.assertLess(min(r['import'] for r in p),0)
 def test_gaps_overlaps_and_missing_prices_refused(self):
  d=date(2026,9,19);r=self.rows(d)
  for broken in [r[1:],r[:-1],r+[r[0]],r[:4]+r[5:],[{**x,'value':float('nan')} for x in r]]:
   with self.assertRaises(m.InputError):m.normalize_periods(broken,d,'Europe/Amsterdam')
 def test_tariff_unit_conversion_and_no_double_tax(self):
  d=date(2026,9,19);r=[{**x,'value':100} for x in self.rows(d)]
  p=m.normalize_periods(r,d,'Europe/Amsterdam',unit='EUR/MWh',basis='spot',tax=.1,fee=.01,vat=21)
  self.assertAlmostEqual(p[0]['import'],.2541);self.assertIsNone(p[0]['export'])
  with self.assertRaises(m.InputError):m.normalize_periods(r,d,'Europe/Amsterdam',basis='all_in',tax=.1)
 def test_unit_sign_and_unknown(self):
  self.assertEqual(m.normalize(-2,'kW','W',-1),2000)
  for bad in ('unknown','unavailable',float('inf'),True):
   with self.assertRaises(m.InputError):m.normalize(bad,'W','W')
 def test_shadow_energy_and_deadline(self):
  now=datetime(2026,9,19,8,tzinfo=timezone.utc);p=m.normalize_periods(self.rows(now.date()),now.date(),'Europe/Amsterdam')
  k={'capacity_kwh':32,'target_soc':80,'reserve_soc':20,'charge_power_kw':4,'charge_efficiency':95,'charge_deadline':'18:00'}
  result=m.shadow_plan(p,50,k,now,'Europe/Amsterdam');self.assertTrue(result['target_reachable']);self.assertFalse(result['physical_authority']);self.assertAlmostEqual(result['estimated_import_kwh'],9.6/.95,places=2)
  self.assertTrue(all(m.instant(w['end']).hour<=16 for w in result['windows']))
  k['charge_deadline']='10:01';self.assertFalse(m.shadow_plan(p,50,k,now,'Europe/Amsterdam')['target_reachable'])
 def test_observed_plan_requires_fresh_same_day_and_no_overlap(self):
  now=datetime(2026,9,19,8,tzinfo=timezone.utc);a={'date':'2026-09-19','updated_at':now.isoformat(),'windows':[{'start':now.isoformat(),'end':(now+timedelta(hours=1)).isoformat(),'action':'charge'}]}
  self.assertEqual(m.observed_plan('WAITING',a,now,'Europe/Amsterdam')['mode'],'observed')
  for b in [{**a,'date':'2026-09-18'},{**a,'updated_at':(now-timedelta(minutes=5)).isoformat()},{**a,'windows':a['windows']*2}]:
   with self.assertRaises(m.InputError):m.observed_plan('WAITING',b,now,'Europe/Amsterdam')
if __name__=='__main__':unittest.main(verbosity=2)
