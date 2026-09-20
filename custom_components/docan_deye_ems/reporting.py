"""Background reporting kept outside the controller heartbeat path."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone

from .analytics import (comparison_limits, historical_metrics, remember_vintage,
                        solar_profile, window_action, heuristic_windows)
from .learning import Learning
from .model import InputError, instant, day_bounds
from .optimizer import integrate, make_slots, optimize


class Reporting:
    def __init__(self, hass, settings, learning, prices, solar_reader):
        self.hass, self.settings, self.learning = hass, settings, learning
        self.prices, self.solar_reader = prices, solar_reader
        self.task = None
        self.last = None
        self.plan = {'status': 'unavailable', 'reason': 'comparison_warming_up', 'physical_authority': False}
        self.metrics = {}

    def schedule(self, now, prices, sources, values, plan):
        if self.task and not self.task.done():
            return
        if self.last and now-self.last < timedelta(minutes=15):
            return
        self.last = now
        self.task = self.hass.async_create_background_task(
            self.refresh(now, copy.deepcopy(prices), dict(sources), dict(values), copy.deepcopy(plan)),
            'docan_deye_ems_reporting')

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def refresh(self, now, prices, sources, values, active_plan):
        zone = self.hass.config.time_zone
        local = Learning(copy.deepcopy(self.learning.data))
        limits = comparison_limits(self.settings)
        cfg = self.settings.get('forecast', {})
        efficiency = local.efficiencies(limits['charge_efficiency'],limits['discharge_efficiency'],cfg.get('learn_efficiency',False))
        limits.update(charge_efficiency=efficiency['charge'],discharge_efficiency=efficiency['discharge'])
        baseline = cfg.get('baseline_load_kw', self.settings.get('control', {}).get('baseline_load_kw', .5))
        horizon_end = datetime.fromtimestamp((int(now.timestamp())//3600+1)*3600, timezone.utc)+timedelta(hours=24)
        result = {'status': 'unavailable', 'physical_authority': False, 'horizon': [], 'updated_at': now.isoformat(),
                  'efficiency':efficiency}
        try:
            # Forecast-error vintages do not require prices or a solar provider.
            long_load, meta = local.load_forecast(now, horizon_end, now, zone, baseline)
            remember_vintage(local, long_load, now)
            local.remember_load(long_load, now)
            result['load_model'] = meta
            if prices is None:
                raise InputError('comparison_prices_unavailable')
            periods = await self.prices.horizon(now, prices, sources.get('price_curve'))
            local.learn_prices(periods,now)
            from zoneinfo import ZoneInfo
            day=now.astimezone(ZoneInfo(zone)).date()
            if cfg.get('anticipate_prices') and instant(periods[-1]['end'])==day_bounds(day,zone)[1]:
                periods+=local.anticipate(instant(periods[-1]['end']),day_bounds(day+timedelta(days=1),zone)[1],zone)
            end = instant(periods[-1]['end'])
            load, meta = local.load_forecast(now, end, now, zone, baseline)
            if cfg:
                solar = await self.solar_reader.read(now, now, end, sources.get('solar_forecast'))
                solar_source = cfg['solar_source']
            else:
                connected = self.settings['solar']['connection'] != 'none'
                solar = solar_profile(local, now, end, now, zone, connected)
                solar_source = 'local_historical_profile' if connected else 'no_solar'
            slots = make_slots(periods, solar, load, now)
            if values.get('battery_soc') is None:
                raise InputError('comparison_soc_unavailable')
            if self.settings['plan']['source'] == 'forecast_shadow':
                result = copy.deepcopy(active_plan)
            else:
                result = await self.hass.async_add_executor_job(optimize, slots, values['battery_soc'], limits)
            schedules = {}
            if self.settings['plan']['source']=='production_shadow':
                schedules = await self.hass.async_add_executor_job(heuristic_windows,self.settings,periods,
                    values['battery_soc'],now,active_plan,zone)
            from zoneinfo import ZoneInfo
            for row in result.get('horizon', []):
                a, b = instant(row['start']), instant(row['end'])
                row['p50_kwh'] = integrate(load, a, b, 'p50_kwh')
                overlapping = [r for r in load if instant(r['start']) < b and instant(r['end']) > a]
                row['p80_kwh'] = integrate(load, a, b, 'p80_kwh') if all(r['p80_kwh'] is not None for r in overlapping) else None
                schedule=schedules.get(a.astimezone(ZoneInfo(zone)).date().isoformat())
                if schedule is not None:
                    row['heuristic_action'] = window_action(schedule, a)
            result.update(updated_at=now.isoformat(), physical_authority=False, load_model=meta,
                solar_source=solar_source, efficiency=efficiency,
                forecast_load_kwh=sum(row['load_kwh'] for row in slots),
                forecast_solar_kwh=sum(row['pv_kwh'] for row in slots),
                comparison_policy='Independent DP simulation. Does not change the active controller or its guards.')
            # Issued means calculation completed, not when an expensive task began.
            issued = datetime.now(timezone.utc)
            local.remember_plan(result, max(now, issued))
        except InputError as error:
            result.update(status='unavailable', reason=str(error), horizon=[])
        except Exception:
            # No provider payloads, site identifiers or exception text enter HA.
            result.update(status='unavailable', reason='comparison_calculation_failed', horizon=[])
        try:
            metrics = await self.hass.async_add_executor_job(historical_metrics, local, now, zone, limits)
            self.metrics = metrics
        except Exception:
            self.metrics = {'regret_status': 'scoring_unavailable'}
        self.plan = result
        for key in ('predictions', 'vintages', 'scores', 'prices'):
            # Live observations continue while this worker runs. Merge only its
            # immutable issued forecasts/scores, never its observation snapshot.
            for stamp, row in local.data[key].items():
                if key=='predictions':
                    target=self.learning.data[key].setdefault(stamp,{})
                    for name,value in row.items():
                        target.setdefault(name,value)
                else:
                    self.learning.data[key].setdefault(stamp, row)
        self.learning.prune(now)
