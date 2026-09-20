"""Independent calendar/tariff fixtures for the production policy."""
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import unittest

from custom_components.docan_deye_ems.engine.market import DeliveryPrices
from custom_components.docan_deye_ems.engine.planner import Contract,plan_day
from custom_components.docan_deye_ems.engine.daily import build_day


def price_rows(day, *, export_peak=.65):
    zone=ZoneInfo('Europe/Amsterdam')
    start=datetime.combine(day,datetime.min.time(),zone).astimezone(timezone.utc)
    end=datetime.combine(day+timedelta(days=1),datetime.min.time(),zone).astimezone(timezone.utc)
    rows=[]
    while start<end:
        local=start.astimezone(zone)
        buy=.1 if 12<=local.hour<16 else .4
        sell=export_peak if 19<=local.hour<21 else .02
        rows.append({'start':start.isoformat(),'end':(start+timedelta(minutes=15)).isoformat(),
                     'import':buy,'export':sell,'origin':'published'})
        start+=timedelta(minutes=15)
    return rows


class DeliveryTests(unittest.TestCase):
    def test_normal_short_and_long_days_preserve_energy_and_firmware_clock(self):
        for day,count in ((date(2026,9,20),96),(date(2026,3,29),92),(date(2026,10,25),100)):
            prices=DeliveryPrices(price_rows(day),day,'Europe/Amsterdam')
            self.assertEqual(len(prices),count)
            self.assertEqual((prices.end-prices.begin).total_seconds()/3600,count/4)
            at=datetime.combine(day,datetime.min.time(),prices.zone).replace(hour=10,minute=30)
            draws={'for_date':str(day),'reserve':{'pct':64,'p50':25,'p90':30,'n':8}}
            plan=build_day(prices,35,at,day,draws)
            self.assertEqual(plan['slot_count'],count)
            starts=[plan['programs'][i]['time'] for i in range(1,7)]
            self.assertEqual(starts,sorted(set(starts)))
            self.assertEqual(plan['charge_window_hhmm'][0][:2],'12')
            for a,b,_ in plan['export_clusters']:
                self.assertGreaterEqual(prices.instant(a).astimezone(prices.zone).hour,18)
                self.assertLessEqual(b,count)

    def test_import_peaks_are_not_export_revenue(self):
        day=date(2026,9,20)
        low=DeliveryPrices(price_rows(day,export_peak=.02),day,'Europe/Amsterdam')
        high=DeliveryPrices(price_rows(day,export_peak=.65),day,'Europe/Amsterdam')
        self.assertEqual(plan_day(low,35,low.contract(Contract())).export_slots,[])
        self.assertTrue(plan_day(high,35,high.contract(Contract())).export_slots)
        disabled=DeliveryPrices(price_rows(day),day,'Europe/Amsterdam',allow_export=False)
        self.assertEqual(plan_day(disabled,35,disabled.contract(Contract())).export_slots,[])

    def test_repeated_clock_hour_has_distinct_instants_and_is_not_programmed(self):
        day=date(2026,10,25)
        prices=DeliveryPrices(price_rows(day),day,'Europe/Amsterdam')
        repeats=[i for i,t in enumerate(prices.starts) if t.astimezone(prices.zone).strftime('%H:%M')=='02:15']
        self.assertEqual(len(repeats),2)
        self.assertEqual(prices.instant(prices.earliest_charge_slot).astimezone(prices.zone).strftime('%H:%M'),'03:00')
        self.assertNotEqual(prices.slot_at(prices.starts[repeats[0]]),prices.slot_at(prices.starts[repeats[1]]))
        plan=plan_day(prices,35,prices.contract(Contract()))
        self.assertTrue(all(i>=prices.earliest_charge_slot for i in plan.charge_slots))

    def test_missing_unpublished_or_unknown_export_price_refuses_control_day(self):
        day=date(2026,9,20)
        for mutate in (lambda r:r.pop(20),lambda r:r[20].update(origin='anticipated'),lambda r:r[20].update(export=None)):
            rows=price_rows(day);mutate(rows)
            with self.assertRaises(ValueError):DeliveryPrices(rows,day,'Europe/Amsterdam')


if __name__=='__main__':unittest.main()
