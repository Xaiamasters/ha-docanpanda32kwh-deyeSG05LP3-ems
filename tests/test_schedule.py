"""Dated jobs and first-install plans use no invented measured history."""
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import unittest

from test_delivery_policy import price_rows
from custom_components.docan_deye_ems.engine.market import DeliveryPrices
from custom_components.docan_deye_ems.engine.planner import Contract
from custom_components.docan_deye_ems.engine.schedule import update_schedule
from custom_components.docan_deye_ems.engine.storage import MemoryState
from custom_components.docan_deye_ems.engine.daily import measure_draws


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.day=date(2026,9,20);self.zone=ZoneInfo('Europe/Amsterdam')
        self.today=DeliveryPrices(price_rows(self.day),self.day,str(self.zone))
        self.next_day=self.day+timedelta(days=1)
        self.tomorrow=DeliveryPrices(price_rows(self.next_day),self.next_day,str(self.zone))
        self.contract=Contract(household_profile=True,baseline_load_kw=.5,cap_kwh=32,maximum_soc=80,chg_soc_target=80)
        self.store=MemoryState()

    def at(self,hour,minute=0):return datetime.combine(self.day,datetime.min.time(),self.zone).replace(hour=hour,minute=minute)

    def test_cold_start_uses_only_remaining_intervals_and_declares_estimate(self):
        plan=update_schedule(self.store,self.at(14,45),self.today,None,35,self.contract)
        self.assertEqual(plan['reserve']['n'],0)
        self.assertEqual(plan['reserve_basis'],'configured_household_load_assumption')
        self.assertEqual(plan['ceiling_pct'],80)
        self.assertGreaterEqual(plan['charge_window_hhmm'][0],'15:00')
        self.assertEqual(self.store.data['schedule'][str(self.day)+':repin']['status'],'missed_window')

    def test_morning_repin_and_nightly_plan_are_distinct_and_once_only(self):
        first=update_schedule(self.store,self.at(9),self.today,self.tomorrow,35,self.contract)
        repin=update_schedule(self.store,self.at(10,30),self.today,self.tomorrow,40,self.contract)
        self.assertNotEqual(first['pinned_at'],repin['pinned_at'])
        repeat=update_schedule(self.store,self.at(10,31),self.today,self.tomorrow,41,self.contract)
        self.assertEqual(repin['pinned_at'],repeat['pinned_at'])
        update_schedule(self.store,self.at(23,15),self.today,self.tomorrow,75,self.contract)
        future=self.store.data['plans'][str(self.next_day)]
        self.assertEqual(future['for_date'],str(self.next_day))
        self.assertEqual(future['reserve']['n'],0)
        self.assertEqual(self.store.data['schedule'][str(self.day)+':arm']['status'],'planned')

    def test_missing_tomorrow_is_pending_only_within_scheduled_window(self):
        update_schedule(self.store,self.at(23,15),self.today,None,75,self.contract)
        self.assertNotIn(str(self.next_day),self.store.data['plans'])
        self.assertEqual(self.store.data['schedule_pending']['reason'],'published_tomorrow_prices_required')
        update_schedule(self.store,self.at(23,16),self.today,self.tomorrow,75,self.contract)
        self.assertIn(str(self.next_day),self.store.data['plans'])
        self.assertNotIn('schedule_pending',self.store.data)

    def test_no_firmware_charge_in_repeated_hour_and_export_respects_cutoff(self):
        from dataclasses import replace
        day=date(2026,10,25)
        rows=price_rows(day)
        for row in rows:
            local=datetime.fromisoformat(row['start']).astimezone(self.zone)
            row['import']=.01 if local.hour==2 else .1
            row['export']=.9 if local.hour>=22 else .6
        prices=DeliveryPrices(rows,day,str(self.zone))
        at=datetime.combine(day,datetime.min.time(),self.zone)
        plan=update_schedule(MemoryState(),at,prices,None,35,replace(self.contract,export_end_hhmm='22:30'))
        self.assertTrue(all(p['time']>='03:00' for p in plan['programs'].values()))
        self.assertTrue(all(prices.instant(end).astimezone(self.zone).strftime('%H:%M')<='22:30' for _,end,_ in plan['export_clusters']))

    def test_reserve_history_excludes_clock_transition_nights(self):
        at=datetime(2026,10,26,12,tzinfo=self.zone)
        stamp=at.replace(day=22,hour=0)
        history=[]
        while stamp<=at:
            hour=stamp.hour+stamp.minute/60
            soc=90-(hour+3) if hour<=11 else 90
            history.append({'at':stamp.isoformat(),'soc':soc,
                            'solar_sell_on':20<=hour<21,'grid_charge_on':11<=hour<12})
            stamp+=timedelta(minutes=15)
        result=measure_draws(history,at,at.date())
        refused={row['night']:row['reason'] for row in result['admission']['refused']}
        self.assertEqual(refused['2026-10-24'],'clock_transition_night')
        self.assertTrue(result['admission']['admitted_nights']>=1)
        self.assertFalse(next(row for row in result['nights'] if row['night']=='2026-10-24')['admitted'])
