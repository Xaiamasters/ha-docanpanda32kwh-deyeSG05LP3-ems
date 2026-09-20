"""Synthetic accounting, causality, DST and HA sensor regression cases."""
import asyncio
import copy
from datetime import datetime, date, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.docan_deye_ems.analytics import (
    accuracy, charge_hours, command_observation, comparison_limits,
    historical_metrics, remember_vintage, replay, score_day, solar_profile)
from custom_components.docan_deye_ems.learning import Learning
from custom_components.docan_deye_ems.model import InputError, day_bounds
from custom_components.docan_deye_ems.reporting import Reporting
from custom_components.docan_deye_ems.sensor import async_setup_entry

UTC = timezone.utc
NOW = datetime(2026, 9, 20, 0, tzinfo=UTC)


def limits():
    return {'capacity_kwh': 32, 'reserve_soc': 25, 'terminal_soc': 50, 'target_soc': 50,
            'max_soc': 95, 'charge_power_kw': 4, 'discharge_power_kw': 4,
            'charge_efficiency': 100, 'discharge_efficiency': 100,
            'grid_import_limit_kw': 10, 'grid_export_limit_kw': 5,
            'cycle_cost_per_kwh': 0, 'allow_battery_export': False,
            'export_on_anticipated': False}


def settings():
    return {'installation_id': 'synthetic', 'name': 'Synthetic test', 'model': 'SUN-10K-SG05LP3-EU-SM2',
            'connection':'existing_ha_sensors', 'bindings':{}, 'battery':{'source':'inverter'},
            'solar': {'connection': 'none', 'kwp': 0}, 'pricing':{'currency':'EUR'},
            'plan': {'source': 'production_shadow', 'capacity_kwh': 32, 'fallback_reserve_soc': 50,
                     'round_trip_efficiency': 1, 'export_power_w': 4000, 'wear_cost_per_kwh': 0}}


def full_day(learning, day, zone='UTC'):
    start, end = day_bounds(day, zone)
    while start < end:
        learning.data['intervals'][str(int(start.timestamp()))] = {
            'seconds':900, 'load':.25, 'pv':0, 'pv_ac':0, 'import':.25, 'export':0,
            'cost':.05, 'unpriced_export':0, 'soc_start':50, 'soc_end':50,
            'tariff':[.2,.05], 'discharge_dc':0}
        start += timedelta(minutes=15)


class AnalyticsTests(unittest.TestCase):
    def test_solar_attribution_never_counts_grid_charging_as_solar(self):
        for connection, pv, expected in [('external_ac',0,0), ('external_ac',1500,900), ('deye_dc',1500,944.444444)]:
            # For the DC case, 500 W AC load consumes 555.56 W on the DC bus.
            m=Learning();v={'load_power':500,'solar_power':pv,'grid_power':1000,'battery_power':-1500,'battery_soc':50}
            for i in range(31):m.observe(NOW+timedelta(seconds=30*i),v,.2,.05,90,connection,90)
            row=m.data['intervals'][str(int(NOW.timestamp()))]
            self.assertAlmostEqual(row['solar_charge'],expected*.25/1000,places=6)
            self.assertLessEqual(row['solar_charge'],1500*.25/1000)
        m=Learning()
        m.observe(NOW,v,.2,.05,90,'mixed',90)
        m.observe(NOW+timedelta(seconds=30),v,.2,.05,90,'mixed',90)
        self.assertNotIn('solar_charge',next(iter(m.data['intervals'].values())))

    def test_no_backfill_across_restart_and_tariff_changes_invalidate_oracle(self):
        m=Learning();v={'load_power':500,'solar_power':0,'grid_power':500,'battery_power':0,'battery_soc':50}
        m.observe(NOW,v,.2,.05);m.observe(NOW+timedelta(seconds=30),v,.3,.05)
        m.observe(NOW+timedelta(seconds=60),v,.3,.05)
        self.assertTrue(m.data['intervals'][str(int(NOW.timestamp()))]['tariff_mixed'])
        restored=Learning(copy.deepcopy(m.data));restored.observe(NOW+timedelta(hours=1),v,.2,.05)
        self.assertEqual(sum(r['seconds'] for r in restored.data['intervals'].values()),60)

    def test_setpoints_require_fresh_successful_live_readback(self):
        c={'active':True,'mode':'live','controller':{'at':NOW.isoformat(),'action':'charge','converged':True,
            'active_program':2,'verified_target':{'grid_charge_a':100,'program_2_voltage':55,'program_2_power':4000}}}
        r=command_observation(c,NOW)
        self.assertEqual(r['setpoint_w'],-4000);self.assertEqual(r['charge_current_a'],100)
        self.assertIn('estimated',r['basis'])
        for bad in ({**c,'active':False},{**c,'stop':{'why':'test'}},
                    {**c,'controller':{**c['controller'],'converged':False}}):
            self.assertIsNone(command_observation(bad,NOW)['setpoint_w'])
        self.assertIsNone(command_observation(c,NOW+timedelta(minutes=2))['setpoint_w'])
        c['controller'].update(action='export',verified_target={'export_w':3000})
        self.assertEqual(command_observation(c,NOW)['setpoint_w'],3000)

    def test_plan_hours_are_union_and_exclude_solar_only_charge(self):
        windows=[{'start':NOW.isoformat(),'end':(NOW+timedelta(hours=1)).isoformat(),'action':'charge'}]*2
        self.assertEqual(charge_hours({'status':'ready','windows':windows}),1)
        row={**windows[0],'battery_charge_ac_kwh':1,'pv_kwh':2,'load_kwh':1}
        self.assertEqual(charge_hours({'horizon':[row]}),0)
        self.assertIsNone(charge_hours({'status':'unavailable'}))

    def test_profile_quantiles_and_pinball_have_correct_units(self):
        m=Learning()
        for i,load in enumerate((.1,.2,.3),1):
            m.data['intervals'][str(int((NOW-timedelta(days=i)).timestamp()))]={'seconds':900,'load':load,'pv':0}
        rows,meta=m.load_forecast(NOW,NOW+timedelta(minutes=15),NOW,'UTC',.5)
        self.assertEqual(meta['source'],'learned_profile')
        self.assertAlmostEqual(rows[0]['p50_kwh'],.2);self.assertAlmostEqual(rows[0]['p80_kwh'],.3)
        m.remember_load(rows,NOW)
        m.data['intervals'][str(int(NOW.timestamp()))]={'seconds':900,'load':.4}
        result=accuracy(m,NOW+timedelta(minutes=15))
        self.assertAlmostEqual(result['load_forecast_pinball_p50'],400)
        self.assertAlmostEqual(result['load_forecast_pinball_p80'],320)
        m.data['intervals'][str(int(NOW.timestamp()))]['seconds']=800
        self.assertIsNone(accuracy(m,NOW+timedelta(minutes=15))['load_forecast_pinball_p50'])

    def test_no_p80_invented_at_cold_start(self):
        m=Learning();rows,_=m.load_forecast(NOW,NOW+timedelta(hours=1),NOW,'UTC',.5)
        self.assertTrue(all(row['p80_kwh'] is None for row in rows))
        m.remember_load(rows,NOW+timedelta(minutes=1))
        self.assertNotIn(str(int(NOW.timestamp())),m.data['predictions'])

    def test_horizon_vintages_are_frozen_before_delivery_and_require_complete_coverage(self):
        m=Learning();rows,_=m.load_forecast(NOW,NOW+timedelta(hours=25),NOW,'UTC',1)
        remember_vintage(m,rows,NOW)
        self.assertEqual(len(m.data['vintages']),2)
        # Repeated calculations must never replace the historical forecast.
        changed=[{**row,'kwh':99} for row in rows];remember_vintage(m,changed,NOW)
        for i in range(100):
            m.data['intervals'][str(int(NOW.timestamp())+i*900)]={'seconds':900,'load':.5}
        metrics=accuracy(m,NOW+timedelta(hours=25))
        self.assertAlmostEqual(metrics['horizon_12h_energy_mae'],12)
        self.assertAlmostEqual(metrics['horizon_24h_energy_mae'],24)
        m.data['intervals'].pop(str(int(NOW.timestamp())+8*900))
        self.assertIsNone(accuracy(m,NOW+timedelta(hours=25))['horizon_24h_energy_mae'])

    def test_hindsight_equal_flat_price_baseline_and_no_partial_day_score(self):
        m=Learning();full_day(m,NOW.date())
        result=score_day(m,NOW.date(),'UTC',limits())
        self.assertEqual(result['status'],'scored')
        self.assertAlmostEqual(result['daily_regret'],0,places=7)
        self.assertAlmostEqual(result['over_buy'],0,places=7)
        self.assertAlmostEqual(result['under_buy'],0,places=7)
        self.assertIsNone(result['dp_vs_heuristic_delta'])
        m.data['intervals'][str(int(NOW.timestamp()))]['seconds']=899
        self.assertEqual(score_day(m,NOW.date(),'UTC',limits())['status'],'incomplete_observations')

    def test_hindsight_sees_missed_cheaper_hours_without_relabeling_actual_profit(self):
        m=Learning();full_day(m,NOW.date())
        for index,row in enumerate(m.data['intervals'].values()):
            price=.1 if index<48 else .5
            row['tariff'][0]=price;row['cost']=row['import']*price
        result=score_day(m,NOW.date(),'UTC',limits())
        self.assertGreater(result['daily_regret'],1)
        self.assertLess(result['oracle_cost_with_wear'],result['observed_cost_with_wear'])

    def test_dst_days_require_92_and_100_quarters(self):
        for day,count in ((date(2026,3,29),92),(date(2026,10,25),100)):
            m=Learning();full_day(m,day,'Europe/Amsterdam')
            self.assertEqual(len(m.data['intervals']),count)
            result=score_day(m,day,'Europe/Amsterdam',limits())
            self.assertEqual(result['status'],'scored')
            self.assertEqual(result['coverage_seconds'],count*900)
            self.assertAlmostEqual(result['daily_regret'],0,places=7)

    def test_same_issued_dp_and_heuristic_costs_produce_zero_paired_delta(self):
        m=Learning();full_day(m,NOW.date())
        # Both charge just enough to replace the house draw, so SoC is held.
        for stamp in m.data['intervals']:
            m.data['predictions'][stamp]={'dp_delta':0,'heuristic_action':'idle'}
        # An idle heuristic self-consumes; give the DP that same request.
        for prediction in m.data['predictions'].values():prediction['dp_delta']=-.25
        result=score_day(m,NOW.date(),'UTC',limits())
        self.assertAlmostEqual(result['dp_vs_heuristic_delta'],0,places=7)
        m.data['scores'][str(NOW.date())]=result
        metrics=historical_metrics(m,NOW+timedelta(days=1),'UTC',limits())
        self.assertEqual(metrics['paired_days'],1)
        self.assertAlmostEqual(metrics['dp_vs_heuristic_7d_delta'],0)
        self.assertIsNone(historical_metrics(m,NOW+timedelta(days=9),'UTC',limits())['dp_vs_heuristic_7d_delta'])

    def test_local_solar_needs_history_and_does_not_send_location(self):
        m=Learning()
        with self.assertRaisesRegex(InputError,'three_complete_days'):
            solar_profile(m,NOW,NOW+timedelta(hours=1),NOW,'UTC',True)
        self.assertEqual(sum(r['kwh'] for r in solar_profile(m,NOW,NOW+timedelta(hours=1),NOW,'UTC',False)),0)
        for i in (1,2,3):full_day(m,(NOW-timedelta(days=i)).date())
        self.assertEqual(sum(r['kwh'] for r in solar_profile(m,NOW,NOW+timedelta(hours=1),NOW,'UTC',True)),0)


class ReportingTests(unittest.IsolatedAsyncioTestCase):
    def hass(self):
        async def run(func,*args):return await asyncio.to_thread(func,*args)
        return SimpleNamespace(config=SimpleNamespace(time_zone='UTC'),async_add_executor_job=run,
            async_create_background_task=lambda coro,name:asyncio.create_task(coro,name=name))

    async def test_sensor_setup_exposes_analytics_in_live_mode_and_preserves_old_ids(self):
        cfg=settings();cfg['plan']['source']='production_shadow'
        c=SimpleNamespace(settings=cfg,last_update_success=True,async_add_listener=lambda *_:None,
            data={'mode':'live','updated_at':NOW.isoformat(),'time_zone':'UTC','values':{},
                  'plan':{'status':'charge','windows':[]},'comparison_plan':{'status':'unavailable'},
                  'metrics':{'days':[]},'command':{'state':'charge','setpoint_w':None}})
        entities=[]
        await async_setup_entry(None,SimpleNamespace(runtime_data=c),entities.extend)
        by_key={e.key:e for e in entities}
        for key in ('plan','operating_mode','daily_statistics','active_load_model','fictive_plan','daily_regret',
                    'over_buy','under_buy','24h_horizon_energy_mae','load_forecast_pinball_p80','setpoint'):
            self.assertIn(key,by_key)
        self.assertEqual(len(by_key),len(entities))
        self.assertEqual(by_key['plan'].unique_id,'synthetic_plan')
        self.assertEqual(by_key['controller_state'].native_value,'charge')
        self.assertFalse(by_key['setpoint'].available)
        self.assertFalse(by_key['fictive_plan'].available)

    async def test_background_work_does_not_block_schedule_or_mutate_observations(self):
        cfg=settings();m=Learning();p=SimpleNamespace(horizon=AsyncMock())
        r=Reporting(self.hass(),cfg,m,p,None)
        gate=asyncio.Event()
        async def wait(*args):await gate.wait()
        with patch.object(r,'refresh',side_effect=wait) as work:
            r.schedule(NOW,{}, {}, {'battery_soc':50},{})
            await asyncio.sleep(0)
            r.schedule(NOW+timedelta(seconds=30),{}, {}, {}, {})
            self.assertEqual(work.await_count,1)
            self.assertFalse(r.task.done())
            await r.close()
            self.assertTrue(r.task.cancelled())

    async def test_production_gets_separate_plan_without_calling_device(self):
        cfg=settings();m=Learning();h=self.hass()
        # Use present time so the issued-forecast gate is exercised realistically.
        now=datetime.now(UTC).replace(microsecond=0)
        end=now+timedelta(hours=2)
        periods=[{'start':now.isoformat(),'end':end.isoformat(),'import':.2,'export':.05}]
        p=SimpleNamespace(horizon=AsyncMock(return_value=periods))
        r=Reporting(h,cfg,m,p,None)
        main={'date':now.date().isoformat(),'status':'charge','inputs_valid':True,'windows':[{'start':now.isoformat(),'end':end.isoformat(),'action':'charge'}]}
        before=copy.deepcopy(main)
        await r.refresh(now,{}, {}, {'battery_soc':60}, main)
        self.assertEqual(main,before)
        self.assertEqual(r.plan['status'],'estimate_ready')
        self.assertFalse(r.plan['physical_authority'])
        self.assertTrue(r.plan['horizon'])
        self.assertTrue(m.data['predictions'])
        self.assertTrue(all(float(stamp)>=now.timestamp() for stamp in m.data['predictions']))
        self.assertTrue(any(p.get('heuristic_action')=='charge' for p in m.data['predictions'].values()))

    async def test_missing_solar_still_records_load_forecasts_and_error_is_redacted(self):
        cfg=settings();cfg['solar']['connection']='deye_dc';m=Learning()
        p=SimpleNamespace(horizon=AsyncMock(return_value=[{'start':NOW.isoformat(),'end':(NOW+timedelta(hours=1)).isoformat(),'import':.2,'export':.05}]))
        r=Reporting(self.hass(),cfg,m,p,None)
        await r.refresh(NOW,{}, {}, {'battery_soc':50},{})
        self.assertEqual(r.plan['status'],'unavailable')
        self.assertIn('three_complete_days',r.plan['reason'])
        self.assertTrue(m.data['predictions'])
        p.horizon.side_effect=RuntimeError('PRIVATE EXCEPTION MUST NOT APPEAR')
        await r.refresh(NOW,{}, {}, {'battery_soc':50},{})
        self.assertNotIn('PRIVATE',str(r.plan))


if __name__=='__main__':unittest.main()
