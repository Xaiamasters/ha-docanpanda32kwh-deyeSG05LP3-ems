"""Independent physical/accounting invariants for the forecast planner."""
import sys, unittest, itertools
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from custom_components.docan_deye_ems.optimizer import optimize, make_slots, integrate
from custom_components.docan_deye_ems.model import InputError, day_bounds, instant

NOW=datetime(2026,9,20,10,tzinfo=timezone.utc)

def limits(**overrides):
    return dict(capacity_kwh=4,reserve_soc=25,target_soc=50,terminal_soc=50,max_soc=100,
        charge_power_kw=4,discharge_power_kw=4,charge_efficiency=90,discharge_efficiency=90,
        grid_import_limit_kw=10,grid_export_limit_kw=10,cycle_cost_per_kwh=.01,
        allow_battery_export=True,export_on_anticipated=False,**overrides)

def slots(prices,pv=None,load=None):
    return [{'start':(NOW+timedelta(minutes=15*i)).isoformat(),'end':(NOW+timedelta(minutes=15*(i+1))).isoformat(),
             'import_price':buy,'export_price':sell,'buy_objective':buy,'sell_objective':sell,
             'pv_kwh':pv[i] if pv else 0,'load_kwh':load[i] if load else .1,'price_origin':'published'}
            for i,(buy,sell) in enumerate(prices)]

class PlanningTests(unittest.TestCase):
    def test_cheap_import_peak_export_and_exact_energy_balance(self):
        cfg=limits();rows=slots([(.05,.02),(.05,.02),(.8,.7),(.8,.7)])
        result=optimize(rows,50,cfg)
        self.assertEqual(result['status'],'estimate_ready')
        self.assertGreater(result['estimated_export_kwh'],0)
        self.assertGreater(result['horizon'][0]['battery_delta_kwh'],0)
        for r in result['horizon']:
            self.assertAlmostEqual(r['pv_kwh']+r['grid_import_kwh']+r['battery_discharge_ac_kwh'],
                r['load_kwh']+r['grid_export_kwh']+r['battery_charge_ac_kwh'])
            self.assertFalse(r['battery_charge_ac_kwh']>0 and r['battery_discharge_ac_kwh']>0)
            self.assertGreaterEqual(r['soc_end'],25)
            self.assertLessEqual(r['soc_end'],100)
        self.assertAlmostEqual(result['projected_soc'],50)
        self.assertFalse(result['physical_authority'])

    def test_small_horizon_matches_independent_exhaustive_cost(self):
        cfg=limits();rows=slots([(.08,.03),(.55,.45)])
        result=optimize(rows,50,cfg)
        best=float('inf')
        levels=[i/32 for i in range(32,129)]
        for mid,final in itertools.product(levels,repeat=2):
            if final<2:continue
            cost=0;previous=2;ok=True
            for row,end in zip(rows,[mid,final]):
                delta=end-previous
                charge=max(delta,0)/.9;discharge=max(-delta,0)*.9
                if max(charge,discharge)>1+1e-9:ok=False;break
                net=.1+charge-discharge
                cost+=max(net,0)*row['import_price']-max(-net,0)*row['export_price']+max(-delta,0)*.01
                previous=end
            if ok:best=min(best,cost)
        self.assertAlmostEqual(result['estimated_net_cost']+result['estimated_wear_cost'],best)

    def test_solar_forecast_reduces_grid_charging(self):
        cfg=limits();rows=slots([(.2,0)]*4,pv=[1,1,0,0],load=[0]*4)
        result=optimize(rows,25,cfg,instant(rows[-1]['end']))
        self.assertEqual(result['status'],'estimate_ready')
        self.assertAlmostEqual(result['estimated_import_kwh'],0)

    def test_disabled_export_never_sells_battery_and_no_implicit_tariff(self):
        cfg=limits();cfg['allow_battery_export']=False
        result=optimize(slots([(.1,2)]*3),75,cfg)
        self.assertAlmostEqual(result['estimated_export_kwh'],0)
        prices=[{'start':NOW.isoformat(),'end':(NOW+timedelta(minutes=15)).isoformat(),'import':.2,'export':None}]
        energy=[{'start':prices[0]['start'],'end':prices[0]['end'],'kwh':0}]
        with self.assertRaisesRegex(InputError,'export_price_required'):make_slots(prices,energy,energy,NOW)

    def test_anticipated_export_disabled_and_negative_prices_finite(self):
        rows=slots([(-.2,2),(.8,2)]*2)
        for r in rows:r['price_origin']='anticipated'
        result=optimize(rows,50,limits())
        self.assertAlmostEqual(result['estimated_export_kwh'],0)
        self.assertEqual(result['status'],'estimate_ready')

    def test_infeasible_deadline_and_no_invented_curtailment(self):
        cfg=limits();cfg['target_soc']=100
        self.assertEqual(optimize(slots([(.2,.1)]),25,cfg,NOW+timedelta(minutes=15))['status'],'target_not_reachable')
        cfg=limits();cfg['grid_export_limit_kw']=0
        self.assertEqual(optimize(slots([(.2,.1)],pv=[5],load=[0]),100,cfg)['status'],'target_not_reachable')

    def test_dst_and_partial_interval_energy_is_preserved(self):
        for day,count in [(date(2026,3,29),92),(date(2026,10,25),100)]:
            a,b=day_bounds(day,'Europe/Amsterdam');p=[];energy=[];cursor=a
            while cursor<b:
                end=cursor+timedelta(hours=1)
                p.append({'start':cursor.isoformat(),'end':end.isoformat(),'import':.2,'export':.05})
                energy.append({'start':cursor.isoformat(),'end':end.isoformat(),'kwh':1})
                cursor=end
            result=make_slots(p,energy,energy,a)
            self.assertEqual(len(result),count)
            self.assertAlmostEqual(sum(r['pv_kwh'] for r in result),count/4)
            partial=make_slots(p,energy,energy,a+timedelta(minutes=7))
            self.assertAlmostEqual(sum(r['pv_kwh'] for r in partial),count/4-7/60)

    def test_missing_overlap_and_nan_fail(self):
        energy=[{'start':NOW.isoformat(),'end':(NOW+timedelta(minutes=15)).isoformat(),'kwh':1}]
        with self.assertRaises(InputError):integrate(energy,NOW,NOW+timedelta(minutes=30),'kwh')
        with self.assertRaises(InputError):integrate(energy*2,NOW,NOW+timedelta(minutes=15),'kwh')
        cfg=limits();cfg['charge_efficiency']=float('nan')
        with self.assertRaises(InputError):optimize(slots([(.1,.1)]),50,cfg)

if __name__=='__main__':unittest.main()
