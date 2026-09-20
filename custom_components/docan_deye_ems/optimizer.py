"""Deterministic, energy-conserving battery simulation. No equipment I/O."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
import math

from .model import InputError, instant, number


def integrate(rows, start, end, key):
    """Integrate interval energy with exact coverage; never fill missing data."""
    cursor, total = start, 0.0
    for row in rows:
        a, b = instant(row['start']), instant(row['end'])
        if b <= start or a >= end:
            continue
        left, right = max(a, start), min(b, end)
        if left != cursor or right <= left:
            raise InputError('forecast_gap_or_overlap')
        energy = number(row[key])
        if energy < 0:
            raise InputError('negative_forecast_energy')
        total += energy * (right-left).total_seconds() / (b-a).total_seconds()
        cursor = right
    if cursor != end:
        raise InputError('forecast_horizon_incomplete')
    return total


def make_slots(prices, pv, load, now, deadline=None):
    """Split prices into <=15-minute physical intervals, including DST/partial slots."""
    slots, cursor = [], now.astimezone(timezone.utc)
    for p in sorted(prices, key=lambda r: instant(r['start'])):
        a, b = instant(p['start']), instant(p['end'])
        if b <= now:
            continue
        a = max(a, now)
        if a != cursor or b <= a:
            raise InputError('price_gap_or_overlap')
        while a < b:
            edge = datetime.fromtimestamp((int(a.timestamp())//900+1)*900, timezone.utc)
            end = min(edge, b)
            if deadline and a < deadline < end:
                end = deadline
            sell = p.get('export')
            if sell is None:
                raise InputError('export_price_required')
            row = {'start': a.isoformat(), 'end': end.isoformat(),
                   'import_price': number(p['import']), 'export_price': number(sell),
                   'pv_kwh': integrate(pv, a, end, 'kwh'),
                   'load_kwh': integrate(load, a, end, 'kwh'),
                   'price_origin': p.get('origin', 'published')}
            # A statistical interval is not a guarantee. Use adverse prices for
            # optimization, but retain the central estimate for displayed costs.
            row['buy_objective'] = number(p.get('import_high', row['import_price']))
            row['sell_objective'] = number(p.get('export_low', row['export_price']))
            slots.append(row)
            a = end
        cursor = b
    if not slots or len(slots) > 200:
        raise InputError('invalid_planning_horizon')
    return slots


def _transition(row, before, after, cfg):
    hours = (instant(row['end'])-instant(row['start'])).total_seconds()/3600
    eta_c, eta_d = cfg['charge_efficiency']/100, cfg['discharge_efficiency']/100
    delta = after-before
    charge = max(delta, 0)/eta_c
    discharge = max(-delta, 0)*eta_d
    if charge > cfg['charge_power_kw']*hours+1e-9 or discharge > cfg['discharge_power_kw']*hours+1e-9:
        return None
    net = row['load_kwh']-row['pv_kwh']+charge-discharge
    if not cfg['allow_battery_export'] and discharge > max(0, row['load_kwh']-row['pv_kwh'])+1e-9:
        return None
    if row['price_origin'] == 'anticipated' and not cfg.get('export_on_anticipated', False):
        if discharge > max(0, row['load_kwh']-row['pv_kwh'])+1e-9:
            return None
    buy, sell = max(net, 0), max(-net, 0)
    if buy > cfg['grid_import_limit_kw']*hours+1e-9 or sell > cfg['grid_export_limit_kw']*hours+1e-9:
        return None
    wear = max(-delta, 0)*cfg['cycle_cost_per_kwh']
    return {'grid_import_kwh': buy, 'grid_export_kwh': sell,
            'battery_charge_ac_kwh': charge, 'battery_discharge_ac_kwh': discharge,
            'battery_delta_kwh': delta,
            'cost': buy*row['import_price']-sell*row['export_price'],
            'wear_cost': wear,
            'objective': buy*row['buy_objective']-sell*row['sell_objective']+wear}


def optimize(slots, soc, settings, deadline=None):
    """Dynamic programming on a bounded SoC lattice with exact transition balance.

    The lattice includes initial/reserve/target/end points. No charge and discharge
    coexist within an interval; export is never valued at the import tariff.
    Optimality is relative to this documented lattice, not a continuous optimum.
    """
    cfg = dict(settings)
    capacity, soc = number(cfg['capacity_kwh']), number(soc)
    numeric = ('reserve_soc', 'target_soc', 'terminal_soc', 'max_soc', 'charge_power_kw',
               'discharge_power_kw', 'charge_efficiency', 'discharge_efficiency',
               'grid_import_limit_kw', 'grid_export_limit_kw', 'cycle_cost_per_kwh')
    for key in numeric:
        cfg[key] = number(cfg[key])
    if not 0 < capacity <= 100 or not 0 <= soc <= 100:
        raise InputError('invalid_planning_limits')
    if not 0 <= cfg['reserve_soc'] <= cfg['terminal_soc'] <= cfg['max_soc'] <= 100:
        raise InputError('invalid_planning_limits')
    if not cfg['reserve_soc'] <= cfg['target_soc'] <= cfg['max_soc']:
        raise InputError('invalid_planning_limits')
    if any(not 0 < cfg[k] <= 100 for k in ('charge_efficiency', 'discharge_efficiency')):
        raise InputError('invalid_planning_limits')
    if any(not 0 < cfg[k] <= 50 for k in ('charge_power_kw', 'discharge_power_kw', 'grid_import_limit_kw')):
        raise InputError('invalid_planning_limits')
    if not 0 <= cfg['grid_export_limit_kw'] <= 50 or not 0 <= cfg['cycle_cost_per_kwh'] <= 10:
        raise InputError('invalid_planning_limits')
    if not slots or len(slots) > 200:
        raise InputError('invalid_planning_horizon')
    initial, floor, ceiling, target, terminal = [capacity*x/100 for x in
        (soc, cfg['reserve_soc'], cfg['max_soc'], cfg['target_soc'], cfg['terminal_soc'])]
    if initial > ceiling+1e-8:
        raise InputError('soc_above_planning_ceiling')
    # 0.25 kWh resolution for the intended 32 kWh pack, with exact special points.
    step = capacity/128
    levels = sorted({round(i*step, 10) for i in range(129) if i*step <= ceiling+1e-9}
                    | {initial, floor, ceiling, target, terminal})
    costs, paths = {levels.index(initial): 0.0}, []
    deadline = instant(deadline) if isinstance(deadline, str) else deadline
    if deadline and not instant(slots[0]['start']) < deadline <= instant(slots[-1]['end']):
        raise InputError('deadline_outside_horizon')
    cursor = instant(slots[0]['start'])
    # Parse once. Inner DP loops receive precomputed durations through a private
    # numeric field; this keeps the event-loop worker bounded.
    for row in slots:
        start, end = instant(row['start']), instant(row['end'])
        if start != cursor or not 0 < (end-start).total_seconds() <= 900:
            raise InputError('invalid_planning_interval')
        cursor = end
        if any(number(row[k]) < 0 for k in ('pv_kwh', 'load_kwh')):
            raise InputError('negative_forecast_energy')
        hours = (end-start).total_seconds()/3600
        max_c = cfg['charge_power_kw']*hours*cfg['charge_efficiency']/100
        max_d = cfg['discharge_power_kw']*hours/(cfg['discharge_efficiency']/100)
        needs_target = deadline is not None and start < deadline <= end
        new, back = {}, {}
        for i, cost in costs.items():
            low = max(min(floor, levels[i]), levels[i]-max_d)
            high = min(ceiling, levels[i]+max_c)
            for j in range(bisect_left(levels, low-1e-9), bisect_right(levels, high+1e-9)):
                if needs_target and levels[j] < target-1e-9:
                    continue
                flow = _transition(row, levels[i], levels[j], cfg)
                if flow is None:
                    continue
                candidate = cost+flow['objective']
                if candidate < new.get(j, math.inf)-1e-10:
                    new[j], back[j] = candidate, i
        paths.append(back)
        costs = new
        if not costs:
            break
    feasible = {j: value for j, value in costs.items() if levels[j] >= terminal-1e-9}
    if len(paths) != len(slots) or not feasible:
        return {'status': 'target_not_reachable', 'mode': 'forecast_shadow', 'physical_authority': False,
                'windows': [], 'horizon': [], 'reason': 'No feasible path within entered limits and forecasts.'}
    j = min(feasible, key=feasible.get)
    schedule = []
    for index in range(len(slots)-1, -1, -1):
        i = paths[index][j]
        row = slots[index]
        flow = _transition(row, levels[i], levels[j], cfg)
        delta = levels[j]-levels[i]
        action = 'charge' if delta > 1e-8 else 'export' if delta < -1e-8 and flow['grid_export_kwh'] > 1e-8 else 'self_consumption' if delta < -1e-8 else 'idle'
        schedule.append({**row, **flow, 'soc_start': levels[i]/capacity*100,
                         'soc_end': levels[j]/capacity*100, 'action': action})
        j = i
    schedule.reverse()
    windows=[]
    for row in schedule:
        if windows and windows[-1]['action']==row['action'] and windows[-1]['price_origin']==row['price_origin'] and windows[-1]['end']==row['start']:
            windows[-1]['end']=row['end']
        else:
            windows.append({key:row[key] for key in ('start','end','action','price_origin')})
    return {'status': 'estimate_ready', 'mode': 'forecast_shadow', 'physical_authority': False,
            'horizon': schedule, 'windows': windows,
            'estimated_import_kwh': sum(r['grid_import_kwh'] for r in schedule),
            'estimated_export_kwh': sum(r['grid_export_kwh'] for r in schedule),
            'estimated_import_cost': sum(r['grid_import_kwh']*r['import_price'] for r in schedule),
            'estimated_export_revenue': sum(r['grid_export_kwh']*r['export_price'] for r in schedule),
            'estimated_net_cost': sum(r['cost'] for r in schedule),
            'estimated_wear_cost': sum(r['wear_cost'] for r in schedule),
            'projected_soc': schedule[-1]['soc_end'], 'target_soc': cfg['target_soc'],
            'target_reachable': True, 'reserve_soc': cfg['reserve_soc'], 'lattice_kwh': step,
            'assumptions': ['Read-only simulation; no schedule is armed.',
                'Solar and load forecasts are estimates. Power limits and tariffs are entered assumptions.',
                'No simultaneous battery charge/discharge; no assumed PV curtailment.',
                'Anticipated prices are statistical estimates; export on anticipated prices is separately controlled.',
                'Net grid cost excludes fixed fees/capital costs and is not a claim of realized savings.']}
