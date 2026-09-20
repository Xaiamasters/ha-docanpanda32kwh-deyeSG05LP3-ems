"""Bounded local observation history, transparent forecasts and measured metrics."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import statistics
from zoneinfo import ZoneInfo
from .model import instant, number

UTC = timezone.utc


class Learning:
    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) and data.get('schema') == 1 else {
            'schema': 1, 'intervals': {}, 'prices': {}, 'predictions': {}, 'efficiency': {}}
        for key in ('intervals', 'prices', 'predictions', 'efficiency', 'vintages', 'scores'):
            self.data.setdefault(key, {})
        self.previous = None  # Never integrate over downtime or a restart.

    def prune(self, now):
        cutoff = (now-timedelta(days=30)).timestamp()
        for key in ('intervals', 'prices', 'predictions'):
            self.data[key] = {k: v for k, v in self.data[key].items() if float(k) >= cutoff}
        self.data['vintages'] = {k: v for k, v in self.data['vintages'].items() if v['issued'] >= cutoff}
        self.data['scores'] = {k: v for k, v in self.data['scores'].items() if v['end'] >= cutoff}

    def observe(self, now, values, buy, sell, charge_efficiency=95, solar_connection='external_ac', discharge_efficiency=95):
        required = ('load_power', 'solar_power', 'grid_power', 'battery_power')
        if any(values.get(k) is None for k in required) or buy is None:
            self.previous = None
            return
        current = {'time': now, 'values': dict(values), 'buy': buy, 'sell': sell}
        old, self.previous = self.previous, current
        if not old:
            return
        seconds = (now-old['time']).total_seconds()
        if not 0 < seconds <= 90:
            return
        # Endpoint quality is required; left-held telemetry is integrated only
        # across short, uninterrupted observations. Costs are estimates from
        # telemetry, not a utility invoice or full-day data when gaps exist.
        a = old['time']
        while a < now:
            start = int(a.timestamp())//900*900
            b = min(now, datetime.fromtimestamp(start+900, UTC))
            duration = (b-a).total_seconds()
            row = self.data['intervals'].setdefault(str(start), {'seconds': 0, 'load': 0,
                'pv': 0, 'import': 0, 'export': 0, 'cost': 0, 'unpriced_export': 0})
            v = old['values']
            k = duration/3600000
            row['seconds'] += duration
            row['load'] += max(0, v['load_power'])*k
            row['pv'] += max(0, v['solar_power'])*k
            # Convert DC PV to the common AC basis used by the simulator. Mixed
            # PV without a split cannot be assigned a defensible conversion.
            charge = max(0, -v['battery_power'])
            pv_ac = v['solar_power']*discharge_efficiency/100 if solar_connection=='deye_dc' else v['solar_power']
            if solar_connection=='mixed':
                row['pv_basis_unknown'] = True
            else:
                row['pv_ac'] = row.get('pv_ac', 0)+max(0, pv_ac)*k
                surplus = max(0, v['solar_power']-v['load_power']/(discharge_efficiency/100)) if solar_connection=='deye_dc' else max(0, pv_ac-v['load_power'])*charge_efficiency/100
                row['solar_charge'] = row.get('solar_charge', 0)+min(charge, surplus)*k
                row['solar_charge_seconds'] = row.get('solar_charge_seconds', 0)+duration
            for key, value in (('discharge_dc', max(0, v['battery_power'])),):
                row[key] = row.get(key, 0) + value*k
            first = a == datetime.fromtimestamp(start, UTC)
            if 'soc_start' not in row:
                row['soc_start'] = v.get('battery_soc') if first else None
            row['soc_end'] = values.get('battery_soc')
            if v.get('battery_soc') is not None and values.get('battery_soc') is not None:
                fraction = (b-old['time']).total_seconds()/seconds
                row['soc_end'] = v['battery_soc']+(values['battery_soc']-v['battery_soc'])*fraction
                if first:
                    fraction = (a-old['time']).total_seconds()/seconds
                    row['soc_start'] = v['battery_soc']+(values['battery_soc']-v['battery_soc'])*fraction
            tariff = [old['buy'], old['sell']]
            row.setdefault('tariff', tariff)
            if row['tariff'] != tariff:
                row['tariff_mixed'] = True
            row['import'] += max(0, v['grid_power'])*k
            row['export'] += max(0, -v['grid_power'])*k
            row['cost'] += max(0, v['grid_power'])*k*old['buy']
            if old['sell'] is None:
                row['unpriced_export'] += max(0, -v['grid_power'])*k
            else:
                row['cost'] -= max(0, -v['grid_power'])*k*old['sell']
            a = b
        dc, ac = old['values']['battery_power'], old['values'].get('battery_ac_power')
        new_dc, new_ac = values['battery_power'], values.get('battery_ac_power')
        if ac is not None and new_ac is not None and abs(dc) >= 100 and abs(ac) >= 100:
            # Dedicated battery AC branch only; inverter total/grid power are not
            # interchangeable. Reject direction changes and implausible ratios.
            if dc*ac > 0 and dc*new_dc > 0 and ac*new_ac > 0:
                direction = 'discharge' if dc > 0 else 'charge'
                ratio = ac/dc if dc > 0 else dc/ac
                if .5 <= ratio <= 1:
                    e = self.data['efficiency'].setdefault(direction, {'input': 0, 'output': 0, 'samples': 0})
                    incoming, outgoing = (abs(dc), abs(ac)) if dc > 0 else (abs(ac), abs(dc))
                    e['input'] += incoming*seconds/3600000
                    e['output'] += outgoing*seconds/3600000
                    e['samples'] += 1
        self.prune(now)

    def learn_prices(self, periods, now):
        for p in periods:
            if p.get('origin', 'published') == 'published':
                a, b = instant(p['start']), instant(p['end'])
                while a < b:
                    self.data['prices'][str(int(a.timestamp()))] = {
                        'import': p['import'], 'export': p.get('export')}
                    a += timedelta(minutes=15)
        self.prune(now)

    def anticipate(self, start, end, zone):
        """Same-local-quarter historical average; spread is not a guarantee."""
        tz, result = ZoneInfo(zone), []
        a = start
        while a < end:
            local = a.astimezone(tz)
            samples = []
            for stamp, row in self.data['prices'].items():
                past = datetime.fromtimestamp(int(stamp), UTC).astimezone(tz)
                if past.date() < local.date() and (past.hour, past.minute) == (local.hour, local.minute):
                    samples.append((past.date(), row))
            # Do not bootstrap estimates from a single known day or count DST
            # duplicate quarters as independent days.
            by_day = {day: row for day, row in samples}
            recent = sorted(by_day.items(), reverse=True)[:14]
            if len(recent) < 3 or any(r.get('export') is None for _, r in recent):
                return []
            buy = [r['import'] for _, r in recent]
            sell = [r['export'] for _, r in recent]
            mean, spread = statistics.mean(buy), statistics.pstdev(buy)
            sell_mean, sell_spread = statistics.mean(sell), statistics.pstdev(sell)
            b = min(a+timedelta(minutes=15), end)
            result.append({'start': a.isoformat(), 'end': b.isoformat(), 'import': mean,
                'export': sell_mean, 'import_high': mean+spread, 'export_low': sell_mean-sell_spread,
                'origin': 'anticipated', 'sample_days': len(recent), 'spread': spread})
            a = b
        return result

    def load_forecast(self, start, end, now, zone, baseline_kw):
        tz = ZoneInfo(zone)
        profiles = {}
        for stamp, row in self.data['intervals'].items():
            at = datetime.fromtimestamp(int(stamp), UTC).astimezone(tz)
            if row['seconds'] >= 850 and at.date() < now.astimezone(tz).date():
                profiles.setdefault((at.hour, at.minute), {})[at.date()] = row['load']*900/row['seconds']
        learned = {key: statistics.median(v.values()) for key, v in profiles.items() if len(v) >= 3}
        ratios = []
        for stamp, row in self.data['intervals'].items():
            at = datetime.fromtimestamp(int(stamp), UTC)
            if now-timedelta(hours=4) <= at < now and row['seconds'] >= 850:
                local = at.astimezone(tz)
                expected = learned.get((local.hour, local.minute))
                if expected and expected > .025:
                    ratios.append(row['load']*900/row['seconds']/expected)
        adjustment = min(1.5, max(.5, statistics.median(ratios))) if len(ratios) >= 8 else 1.0
        a, rows, learned_count = start, [], 0
        while a < end:
            edge = datetime.fromtimestamp((int(a.timestamp())//900+1)*900, UTC)
            b = min(edge, end)
            local = a.astimezone(tz)
            key = (local.hour, local.minute//15*15)
            value = learned.get(key)
            learned_count += value is not None
            kw = value*4*adjustment if value is not None else number(baseline_kw)
            hours = (b-a).total_seconds()/3600
            samples = sorted(profiles.get(key, {}).values())
            p80 = samples[min(len(samples)-1, int(.8*(len(samples)-1)+.999999))]*4*adjustment if len(samples) >= 3 else None
            rows.append({'start': a.isoformat(), 'end': b.isoformat(), 'kwh': kw*hours,
                         'p50_kwh': kw*hours, 'p80_kwh': p80*hours if p80 is not None else None})
            a = b
        return rows, {'source': 'learned_profile' if learned_count == len(rows) else 'mixed_profile' if learned_count else 'entered_baseline',
                      'learned_slots': learned_count, 'total_slots': len(rows), 'intraday_multiplier': adjustment}

    def efficiencies(self, charge, discharge, enabled):
        result = {'charge': charge, 'discharge': discharge, 'charge_source': 'entered', 'discharge_source': 'entered'}
        if enabled:
            for direction, row in self.data['efficiency'].items():
                if row['samples'] >= 60 and row['input'] >= .5:
                    result[direction] = 100*row['output']/row['input']
                    result[direction+'_source'] = 'measured_dedicated_ac_dc'
        return result

    def remember_plan(self, plan, now):
        for row in plan.get('horizon', []):
            a, b = instant(row['start']), instant(row['end'])
            if a >= now and int(a.timestamp()) % 900 == 0 and (b-a).total_seconds() == 900:
                record = self.data['predictions'].setdefault(str(int(a.timestamp())), {})
                fields = {
                    'pv': row['pv_kwh'], 'load': row['load_kwh'], 'cost': row['cost'],
                    'p50': row.get('p50_kwh', row['load_kwh']), 'p80': row.get('p80_kwh'),
                    'dp_delta': row.get('battery_delta_kwh'), 'heuristic_action': row.get('heuristic_action')}
                for key, value in fields.items():
                    if value is not None:
                        record.setdefault(key, value)

    def remember_load(self, rows, now):
        for row in rows:
            a, b = instant(row['start']), instant(row['end'])
            if a >= now and int(a.timestamp()) % 900 == 0 and (b-a).total_seconds() == 900:
                record = self.data['predictions'].setdefault(str(int(a.timestamp())), {})
                for key, value in (('load', row['kwh']), ('p50', row['p50_kwh']), ('p80', row['p80_kwh'])):
                    record.setdefault(key, value)

    def metrics(self, now, zone):
        days, pv_errors, load_errors, cost_errors = {}, [], [], []
        for stamp, row in self.data['intervals'].items():
            at = datetime.fromtimestamp(int(stamp), UTC)
            day = at.astimezone(ZoneInfo(zone)).date().isoformat()
            summary = days.setdefault(day, {'date': day, 'coverage_seconds': 0, 'import_kwh': 0,
                'export_kwh': 0, 'observed_net_cost': 0, 'unpriced_export_kwh': 0,
                'solar_charge_estimate_kwh': 0, 'solar_charge_coverage_seconds': 0})
            for to, source in [('coverage_seconds', 'seconds'), ('import_kwh', 'import'),
                               ('export_kwh', 'export'), ('observed_net_cost', 'cost'), ('unpriced_export_kwh', 'unpriced_export')]:
                summary[to] += row[source]
            summary['solar_charge_estimate_kwh'] += row.get('solar_charge', 0)
            summary['solar_charge_coverage_seconds'] += row.get('solar_charge_seconds', 0)
            prediction = self.data['predictions'].get(stamp)
            if prediction and row['seconds'] >= 850 and at+timedelta(minutes=15) <= now:
                if not row.get('pv_basis_unknown') and prediction.get('pv') is not None:
                    pv_errors.append(abs(row.get('pv_ac',row['pv'])*900/row['seconds']-prediction['pv'])*4000)
                load_errors.append(abs(row['load']*900/row['seconds']-prediction['load'])*4000)
                if row['unpriced_export'] == 0 and prediction.get('cost') is not None:
                    cost_errors.append(abs(row['cost']*900/row['seconds']-prediction['cost']))
        return {'days': sorted(days.values(), key=lambda r: r['date']),
                'solar_forecast_mae_w': statistics.mean(pv_errors) if pv_errors else None,
                'load_forecast_mae_w': statistics.mean(load_errors) if load_errors else None,
                'plan_cost_mae': statistics.mean(cost_errors) if cost_errors else None,
                'compared_intervals': len(load_errors),
                'accounting': 'Whole-home observed grid costs, partial coverage; not battery profit or proven savings.'}
