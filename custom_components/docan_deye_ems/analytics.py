"""Local comparison and scoring. No device access and no command authority.

Forecasts are frozen before delivery. Hindsight scores require every quarter of
a completed local day. All candidate replays use the same battery physics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import statistics
from zoneinfo import ZoneInfo

from .model import InputError, day_bounds, instant
from .optimizer import optimize, _transition

UTC = timezone.utc


def comparison_limits(settings):
    plan = settings['plan']
    if plan['source'] == 'forecast_shadow':
        return dict(plan)
    from .control_profile import defaults, MODELS
    c = {**defaults(settings['model']), **settings.get('control', {})}
    efficiency = math.sqrt(plan.get('round_trip_efficiency', .9025))*100
    minimum = c['minimum_soc']
    terminal = max(minimum, min(c['max_soc'], plan.get('fallback_reserve_soc', plan.get('reserve_soc', minimum))))
    return {'capacity_kwh': plan.get('capacity_kwh', 32), 'reserve_soc': minimum,
            'terminal_soc': terminal, 'target_soc': terminal, 'max_soc': c['max_soc'],
            'charge_power_kw': min(c['program_power_w'], c['charge_current_a']*c['charge_voltage'])/1000,
            'discharge_power_kw': min(MODELS[settings['model']], plan.get('export_power_w', 5000)/1000),
            'charge_efficiency': efficiency, 'discharge_efficiency': efficiency,
            'grid_import_limit_kw': MODELS[settings['model']],
            'grid_export_limit_kw': plan.get('export_power_w', 5000)/1000,
            'cycle_cost_per_kwh': plan.get('wear_cost_per_kwh', .02),
            'allow_battery_export': c['allow_export'], 'export_on_anticipated': False}


def charge_hours(plan, now=None):
    """Union of charge windows; no double counting overlapping supplied windows."""
    if plan.get('status') in ('not_ready', 'target_not_reachable', 'unavailable', 'no_complete_target') or plan.get('inputs_valid') is False:
        return None
    spans = []
    for row in plan.get('horizon') or plan.get('windows', []):
        if row.get('action') != 'charge':
            continue
        if 'battery_charge_ac_kwh' in row:
            surplus = max(0, row['pv_kwh']-row['load_kwh'])
            if row['battery_charge_ac_kwh'] <= surplus+1e-8:
                continue  # Solar-only charging is not grid-charge time.
        a, b = instant(row['start']), instant(row['end'])
        if now is not None:
            a = max(a, now)
        if a < b:
            spans.append((a, b))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return sum((b-a).total_seconds()/3600 for a, b in merged)


def command_observation(control, now, max_age=90):
    """Only expose verified commands from the current live session.

    Deye grid charging is current-controlled. The W sensor is an explicitly
    labelled equivalent ceiling, not a claim that a W command was sent.
    """
    state = control.get('controller') or {}
    result = {'state': state.get('action') if control.get('active') else control.get('mode', 'shadow'),
              'setpoint_w': None, 'charge_current_a': None, 'basis': 'no_verified_live_command'}
    if not control.get('active') or control.get('stop') or not state.get('converged'):
        return result
    try:
        if not 0 <= (now-instant(state['at'])).total_seconds() <= max_age:
            return result
        want = state['verified_target']
        action = state['action']
        result['verified_at'] = state['at']
        if action == 'charge':
            current = float(want['grid_charge_a'])
            program = state['active_program']
            voltage = float(want[f'program_{program}_voltage'])
            ceiling = float(want[f'program_{program}_power'])
            result.update(setpoint_w=-min(current*voltage, ceiling), charge_current_a=current,
                          basis='estimated_charge_ceiling_from_verified_A_V_and_program_W')
        elif action == 'export':
            result.update(setpoint_w=float(want['export_w']), charge_current_a=0,
                          basis='verified_export_limit_W')
        else:
            # Zero means no forced grid-charge/export request; self-consumption
            # and solar charging can still produce nonzero measured power.
            result.update(setpoint_w=0, charge_current_a=0, basis='no_forced_grid_transfer')
    except (KeyError, ValueError, TypeError, InputError):
        return {'state': result['state'], 'setpoint_w': None, 'charge_current_a': None,
                'basis': 'no_verified_live_command'}
    return result


def solar_profile(learning, start, end, now, zone, connected):
    """Local same-quarter median, never fabricate sunshine at cold start."""
    tz = ZoneInfo(zone)
    profile = {}
    today = now.astimezone(tz).date()
    for stamp, row in learning.data['intervals'].items():
        at = datetime.fromtimestamp(int(stamp), UTC).astimezone(tz)
        if row['seconds'] >= 899.999 and at.date() < today and not row.get('pv_basis_unknown'):
            profile.setdefault((at.hour, at.minute), {})[at.date()] = row.get('pv_ac',row['pv'])
    a, result = start, []
    while a < end:
        b = min(end, datetime.fromtimestamp((int(a.timestamp())//900+1)*900, UTC))
        local = a.astimezone(tz)
        samples = profile.get((local.hour, local.minute//15*15), {})
        if connected and len(samples) < 3:
            raise InputError('solar_history_requires_three_complete_days_or_configured_forecast')
        energy = statistics.median(samples.values()) if connected else 0
        result.append({'start': a.isoformat(), 'end': b.isoformat(), 'kwh': energy*(b-a).total_seconds()/900})
        a = b
    return result


def remember_vintage(learning, rows, issued):
    """Freeze at most one 12/24h forecast per UTC hour, made before its start."""
    start = datetime.fromtimestamp((int(issued.timestamp())//3600+1)*3600, UTC)
    by_start = {instant(row['start']): row for row in rows}
    for hours in (12, 24):
        key = start.isoformat()+':'+str(hours)
        if key in learning.data['vintages']:
            continue
        selected = [by_start.get(start+timedelta(minutes=15*i)) for i in range(hours*4)]
        if any(row is None or (instant(row['end'])-instant(row['start'])).total_seconds() != 900 for row in selected):
            continue
        learning.data['vintages'][key] = {'issued': issued.timestamp(), 'start': start.timestamp(),
            'hours': hours, 'load_kwh': sum(row['kwh'] for row in selected)}


def accuracy(learning, now):
    losses = {50: [], 80: []}
    for stamp, pred in learning.data['predictions'].items():
        row = learning.data['intervals'].get(stamp)
        if not row or row['seconds'] < 899.999 or int(stamp)+900 > now.timestamp():
            continue
        for percentile in losses:
            prediction = pred.get('p'+str(percentile))
            if prediction is not None:
                error = (row['load']-prediction)*4000
                q = percentile/100
                losses[percentile].append(max(q*error, (q-1)*error))
    result = {}
    for q, rows in losses.items():
        result[f'load_forecast_pinball_p{q}'] = statistics.mean(rows) if rows else None
        result[f'pinball_p{q}_samples'] = len(rows)
    for hours in (12, 24):
        errors = []
        for v in learning.data['vintages'].values():
            if v['hours'] != hours or v['start']+hours*3600 > now.timestamp() or v['issued'] >= v['start']:
                continue
            rows = [learning.data['intervals'].get(str(int(v['start'])+900*i)) for i in range(hours*4)]
            if all(row and 899.999 <= row['seconds'] <= 900.001 for row in rows):
                errors.append(abs(sum(row['load'] for row in rows)-v['load_kwh']))
        result[f'horizon_{hours}h_energy_mae'] = statistics.mean(errors) if errors else None
        result[f'horizon_{hours}h_samples'] = len(errors)
    return result


def window_action(plan, start):
    for window in plan.get('windows', []):
        if instant(window['start']) <= start < instant(window['end']) and window['action'] == 'export':
            return 'export'
    for window in plan.get('windows', []):
        if instant(window['start']) <= start < instant(window['end']) and window['action'] == 'charge':
            return 'charge'
    return 'self_consumption'


def heuristic_windows(settings, periods, soc, now, active_plan, zone):
    """Issued comparison schedules, including published future delivery days.

    Today's saved controller windows are preferred. Future days use the same
    pure price-window planner with the current SoC and entered reserve. They
    are a comparator forecast, never an assertion that firmware was programmed.
    """
    from .control_profile import contract
    from .engine.market import DeliveryPrices
    from .engine.planner import plan_day
    tz = ZoneInfo(zone)
    days = {instant(p['start']).astimezone(tz).date() for p in periods}
    result = {}
    for day in days:
        if str(day)==active_plan.get('date') and active_plan.get('inputs_valid'):
            result[str(day)] = active_plan
            continue
        if day < now.astimezone(tz).date():
            continue
        try:
            c = contract(settings)
            prices = DeliveryPrices(periods,day,zone,allow_export=c.allow_export)
            scheduled = plan_day(prices,soc,prices.contract(c))
            windows = [{'start':prices.instant(p.start_slot).isoformat(),
                        'end':prices.instant(p.end_slot).isoformat(),'action':p.action}
                       for p in scheduled.periods if p.action in ('charge','export')]
            result[str(day)] = {'windows':windows}
        except (ValueError, InputError, KeyError):
            continue
    return result


def replay(slots, predictions, soc, cfg, strategy):
    """Replay issued requests against realized load/PV; never replan hindsight.

    The heuristic comparator is the saved controller window schedule, not a
    simulation of its BMS guards, taper curve or firmware. This distinction is
    included in sensor attributes and documentation.
    """
    energy = cfg['capacity_kwh']*soc/100
    floor, cap = [cfg['capacity_kwh']*cfg[k]/100 for k in ('reserve_soc', 'max_soc')]
    eta_c, eta_d = cfg['charge_efficiency']/100, cfg['discharge_efficiency']/100
    total = 0
    for row, prediction in zip(slots, predictions):
        hours = (instant(row['end'])-instant(row['start'])).total_seconds()/3600
        net = row['load_kwh']-row['pv_kwh']
        if strategy == 'dp':
            request = prediction['dp_delta']
        else:
            action = prediction['heuristic_action']
            request = (cfg['charge_power_kw']*hours*eta_c if action == 'charge' else
                       -cfg['discharge_power_kw']*hours/eta_d if action == 'export' else
                       -net/eta_d if net > 0 else -net*eta_c)
        low = max(floor-energy, -cfg['discharge_power_kw']*hours/eta_d)
        high = min(cap-energy, cfg['charge_power_kw']*hours*eta_c)
        # Grid limits constrain admissible signed battery energy deltas.
        grid_low = -cfg['grid_export_limit_kw']*hours-net
        grid_high = cfg['grid_import_limit_kw']*hours-net
        convert = lambda value: value*eta_c if value >= 0 else value/eta_d
        low, high = max(low, convert(grid_low)), min(high, convert(grid_high))
        if not cfg['allow_battery_export']:
            low = max(low, -max(0, net)/eta_d)
        if low > high+1e-8:
            return None
        delta = min(high, max(low, request))
        flow = _transition(row, energy, energy+delta, cfg)
        if flow is None:
            return None
        total += flow['cost']+flow['wear_cost']
        energy += delta
    # Identical terminal inventory value for both replays removes the advantage
    # from merely ending emptier. Negative export prices give no inventory credit.
    value = max(0, slots[-1]['export_price'])*eta_d
    return total-max(0, energy-floor)*value


def score_day(learning, day, zone, cfg):
    begin, end = day_bounds(day, zone)
    stamps = [str(int(begin.timestamp())+900*i) for i in range(int((end-begin).total_seconds()/900))]
    observed = [learning.data['intervals'].get(stamp) for stamp in stamps]
    result = {'date': day.isoformat(), 'end': end.timestamp(), 'status': 'incomplete_observations'}
    if not all(row and 899.999 <= row['seconds'] <= 900.001 and row.get('soc_start') is not None
               and row.get('soc_end') is not None and row.get('tariff') and row['tariff'][1] is not None
               and not row.get('tariff_mixed') and not row.get('pv_basis_unknown') for row in observed):
        return result
    if any(not cfg['reserve_soc'] <= row[key] <= cfg['max_soc'] for row in observed for key in ('soc_start', 'soc_end')):
        return {**result, 'status': 'observed_soc_outside_comparison_limits'}
    slots = []
    for stamp, row in zip(stamps, observed):
        at = datetime.fromtimestamp(int(stamp), UTC)
        buy, sell = row['tariff']
        slots.append({'start': at.isoformat(), 'end': (at+timedelta(minutes=15)).isoformat(),
            'pv_kwh': row.get('pv_ac',row['pv']), 'load_kwh': row['load'], 'import_price': buy, 'export_price': sell,
            'buy_objective': buy, 'sell_objective': sell, 'price_origin': 'published'})
    # Hindsight cannot spend energy that the real installation kept for tomorrow.
    oracle_cfg = {**cfg, 'terminal_soc': observed[-1]['soc_end']}
    oracle = optimize(slots, observed[0]['soc_start'], oracle_cfg)
    if not oracle.get('horizon'):
        return {**result, 'status': 'oracle_infeasible'}
    actual = sum(row['cost']+row.get('discharge_dc', 0)*cfg['cycle_cost_per_kwh'] for row in observed)
    optimum = oracle['estimated_net_cost']+oracle['estimated_wear_cost']
    difference = sum(row['import'] for row in observed)-oracle['estimated_import_kwh']
    result.update(status='scored', daily_regret=actual-optimum, over_buy=max(0, difference),
                  under_buy=max(0, -difference), observed_cost_with_wear=actual,
                  oracle_cost_with_wear=optimum, oracle_lattice_kwh=oracle['lattice_kwh'],
                  coverage_seconds=(end-begin).total_seconds(), dp_vs_heuristic_delta=None)
    predictions = [learning.data['predictions'].get(stamp) for stamp in stamps]
    if all(p and p.get('dp_delta') is not None and p.get('heuristic_action') is not None for p in predictions):
        dp = replay(slots, predictions, observed[0]['soc_start'], cfg, 'dp')
        heuristic = replay(slots, predictions, observed[0]['soc_start'], cfg, 'heuristic')
        if dp is not None and heuristic is not None:
            result['dp_vs_heuristic_delta'] = dp-heuristic
    return result


def historical_metrics(learning, now, zone, cfg):
    """Cache completed days; missing samples cannot be backfilled by this store."""
    today = now.astimezone(ZoneInfo(zone)).date()
    days = {datetime.fromtimestamp(int(stamp), UTC).astimezone(ZoneInfo(zone)).date()
            for stamp in learning.data['intervals']}
    # Bound work after upgrades: at most one new oracle solve per refresh.
    for day in sorted(days, reverse=True):
        if day < today and day.isoformat() not in learning.data['scores']:
            learning.data['scores'][day.isoformat()] = score_day(learning, day, zone, cfg)
            break
    completed = sorted(learning.data['scores'].values(), key=lambda row: row['date'])
    latest_day = (today-timedelta(days=1)).isoformat()
    latest = learning.data['scores'].get(latest_day, {'date': latest_day, 'status': 'no_complete_day'})
    paired = [row for row in completed if (today-timedelta(days=7)).isoformat() <= row['date'] < today.isoformat()
              and row.get('dp_vs_heuristic_delta') is not None]
    return {**accuracy(learning, now), 'daily_regret': latest.get('daily_regret'),
            'over_buy': latest.get('over_buy'), 'under_buy': latest.get('under_buy'),
            'regret_date': latest['date'], 'regret_status': latest['status'],
            'dp_vs_heuristic_7d_delta': sum(row['dp_vs_heuristic_delta'] for row in paired) if paired else None,
            'paired_days': len(paired), 'scored_days': completed,
            'regret_basis': 'Observed grid cost plus estimated wear minus lattice hindsight optimum; same initial SoC and at least observed final SoC.',
            'comparison_basis': 'DP minus controller-window replay cost including wear and common terminal export value; negative favours DP. Not measured savings.'}
