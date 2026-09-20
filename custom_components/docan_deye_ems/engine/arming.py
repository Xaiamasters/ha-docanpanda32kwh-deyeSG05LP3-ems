"""Production ceiling selection, opening-state guard and firmware window policy."""
from __future__ import annotations
from dataclasses import replace
from .planner import CHARGE, EXPORT, SLOTS_PER_DAY, Contract, Period, _hhmm_to_slot, armed_window, assert_window_never_sells, plan_day
from . import programs as writer
from .market import exports
DEADMAN_SOC_MAX = 96.0

def forecast_morning_soc(soc_now: float, p50: float, p90: float):
    f50 = max(15.0, soc_now - p50)
    f90 = max(15.0, soc_now - p90)
    basis = f'forecast opening SoC {f50:.0f}% = live {soc_now:.0f}% - P50 draw {p50:.0f} pts (p90 night lands {f90:.0f}%)'
    return (f50, f90, basis)
GUARD_BELOW_P90 = 8.0
GUARD_ABOVE_P50 = 12.0
GUARD_TODAY_BAND = 10.0

def opening_guard(soc0: float, soc_now: float, p50: float, p90: float, tomorrow: bool) -> str:
    if tomorrow:
        lo = soc_now - p90 - GUARD_BELOW_P90
        hi = soc_now - p50 + GUARD_ABOVE_P50
        if not lo <= soc0 <= hi:
            raise SystemExit(f'REFUSED (opening-state guard): planned opening SoC {soc0:.0f}% is outside the reachable envelope [{lo:.0f}%, {hi:.0f}%] = live {soc_now:.0f}% - draw series (P50 {p50:.0f} / p90 {p90:.0f}, guard -{GUARD_BELOW_P90:.0f}/+{GUARD_ABOVE_P50:.0f}). A tomorrow plan built from an unreachable opening state arms the wrong day ; F27.')
        return f'opening guard PASS: {soc0:.0f}% inside [{lo:.0f}%, {hi:.0f}%] (overnight-only envelope; acceptance curve n/a before the window)'
    if abs(soc0 - soc_now) > GUARD_TODAY_BAND:
        raise SystemExit(f"REFUSED (opening-state guard): today's plan opens at {soc0:.0f}% but the pack reads {soc_now:.0f}% ; |delta| > {GUARD_TODAY_BAND:.0f} points. An override that far from the live reading is a wrong-day plan ; F27.")
    return f'opening guard PASS: {soc0:.0f}% within {GUARD_TODAY_BAND:.0f} of live {soc_now:.0f}%'

def stretch_to_armed_window(plan, prices, k):
    start, end, why = armed_window(plan, prices, k)
    assert_window_never_sells(start, end, plan)
    ch = [p for p in plan.periods if p.action == CHARGE]
    if ch and end is not None and (end > ch[-1].end_slot):
        ch[-1].end_slot = end
    return (start, end, why)
EXPORT_KW_METER = 7.0
PACK_TO_METER = 0.929
ACCEPT_KW_FLAT, ACCEPT_FLAT_TO_PCT, ACCEPT_KW_TAPER = (8.45, 62.0, 5.0)
PACK_KWH_PER_PCT = 0.32
DISCHARGE_KWH_PER_PCT = 0.3
PACK_TO_METER = 0.95
HOUSE_KW_IN_WINDOW = 1.0
CONDITION_C_MARGIN_KWH = 1.5

def schema_v2_extend(payload: dict, prices, k) -> dict:
    payload['schema_version'] = 2
    payload['clusters_detail'] = [{'start': writer.slot_to_hhmm(a,prices), 'end': writer.slot_to_hhmm(b,prices), 'price_ct': round(sum((exports(prices)[i] for i in range(a, b))) / (b - a) * 100, 2)} for a, b, _fl in payload.get('export_clusters') or []]
    cw = payload.get('charge_window')
    soc0 = float(payload.get('soc0', 0.0))
    target = float(getattr(k, 'chg_soc_target', 90.0))
    flat_kw=min(ACCEPT_KW_FLAT,max(v for _,v in k.curve)) if k.household_profile else ACCEPT_KW_FLAT
    taper_kw=min(ACCEPT_KW_TAPER,flat_kw)
    pack_per_pct=k.cap_kwh/100 if k.household_profile else PACK_KWH_PER_PCT
    export_kw=k.export_power_w/1000*k.meter_factor if k.household_profile else EXPORT_KW_METER
    if cw and cw[0] is not None and (soc0 < target):
        need_flat = max(0.0, min(target, ACCEPT_FLAT_TO_PCT) - soc0) * pack_per_pct
        need_taper = max(0.0, target - max(soc0, ACCEPT_FLAT_TO_PCT)) * pack_per_pct
        hours = need_flat / flat_kw + need_taper / taper_kw
        done_slot = min(cw[1], cw[0] + int(hours * 4 + 0.999))
        span = range(cw[0], max(cw[0] + 1, done_slot))
        payload['charge_block'] = {'start': writer.slot_to_hhmm(cw[0],prices), 'end': writer.slot_to_hhmm(cw[1],prices), 'avg_price_ct': round(sum((prices[i] for i in span)) / len(span) * 100, 2), 'est_kwh': round(need_flat + need_taper, 1), 'est_complete_time': writer.slot_to_hhmm(done_slot,prices), 'estimate_basis': f'{flat_kw:g} kW flat to {ACCEPT_FLAT_TO_PCT:.0f}%, ~{taper_kw:g} kW taper assumption; selection-time prices'}
    clusters = payload.get('export_clusters') or []
    floor = min((fl for _a, _b, fl in clusters), default=None)
    slot_kwh = 0.25 * export_kw
    slots = sorted((exports(prices)[i] for a, b, _fl in clusters for i in range(a, b)), reverse=True)
    window_hours = sum((b - a for a, b, _fl in clusters)) * 0.25
    budget_kwh = None
    if floor is not None and soc0 is not None:
        sell_open = max(soc0, target)
        budget_kwh = floor_budget_kwh(sell_open - floor, window_hours,k)
    remaining = budget_kwh if budget_kwh is not None else len(slots) * slot_kwh
    kwh = eur = 0.0
    for p in slots:
        take = min(slot_kwh, remaining)
        if take <= 0:
            break
        kwh += take
        eur += take * p
        remaining -= take
    payload['expected_export_kwh'] = round(kwh, 1)
    payload['expected_export_eur'] = round(eur, 2)
    window_kwh = len(slots) * slot_kwh
    payload['expected_export_basis'] = f"min(window {round(window_kwh, 1)} kWh, floor budget {('n/a' if budget_kwh is None else round(budget_kwh, 1))} kWh) at {export_kw:g} kW assumed meter delivery, highest-priced intervals first; budget subtracts the configured house-load estimate"
    payload['window_kwh'] = round(window_kwh, 2)
    payload['floor_budget_kwh'] = None if budget_kwh is None else round(budget_kwh, 2)
    return payload

def floor_budget_kwh(points: float, window_hours: float,k=None) -> float:
    if k is not None and k.household_profile:
        return max(0,points*k.cap_kwh/100*k.meter_factor-k.baseline_load_kw*window_hours)
    return max(0.0, points * DISCHARGE_KWH_PER_PCT * PACK_TO_METER - HOUSE_KW_IN_WINDOW * window_hours)

def evening_binding(window_kwh, budget_kwh):
    if budget_kwh is None or window_kwh is None:
        return ('unknown', 0.0)
    if budget_kwh < window_kwh:
        return ('floor', window_kwh - budget_kwh)
    return ('window', 0.0)
CEILING_BASE = 90.0
CEILING_TRIAL = 95.0

def _mean(prices, slots):
    slots = list(slots)
    return sum((prices[i] for i in slots)) / len(slots) if slots else None

def choose_ceiling(prices, soc0, k, plan_base, plan_trial):
    if soc0 >= CEILING_BASE:
        return (CEILING_BASE, f'ceiling 90: opening SoC {soc0:.0f}% is already at or above the base ceiling ; no charge to extend')
    if not getattr(plan_trial, 'target_reached', False):
        return (CEILING_BASE, f"ceiling 90: condition (a) FAILED ; the planner does not reach {CEILING_TRIAL:.0f}% inside the cheap block (ends at {getattr(plan_trial, 'soc_end_of_charge', 0):.0f}%)")
    added = sorted(set(plan_trial.charge_slots) - set(plan_base.charge_slots))
    priced_from = added or sorted(plan_trial.charge_slots)
    if not priced_from:
        return (CEILING_BASE, 'ceiling 90: condition (a) FAILED ; no charge block at all, so there is no marginal kWh to price')
    free_marginal = not added
    marginal_buy = max((prices[i] for i in priced_from))
    export_runs = [p for p in plan_trial.periods if p.action == EXPORT]
    sell = _mean(exports(prices), [i for p in export_runs for i in range(p.start_slot, p.end_slot)])
    if sell is None:
        return (CEILING_BASE, 'ceiling 90: condition (b) FAILED ; no export planned, so the marginal kWh has nothing to be sold into')
    ex_slots = [i for p in export_runs for i in range(p.start_slot, p.end_slot)]
    window_hours = len(ex_slots) * 0.25
    window_kwh = window_hours * (k.export_power_w/1000*k.meter_factor if k.household_profile else EXPORT_KW_METER)
    base_budget = floor_budget_kwh(CEILING_BASE - k.reserve_floor_pct, window_hours,k)
    binding, unsold = evening_binding(window_kwh, base_budget)
    if binding != 'floor':
        return (CEILING_BASE, f'ceiling 90: condition (c) FAILED ; the evening is {binding}-bound (window {window_kwh:.1f} kWh vs budget {base_budget:.1f} kWh), so extra charge would be stranded rather than sold')
    if unsold < CONDITION_C_MARGIN_KWH:
        return (CEILING_BASE, f'ceiling 90: condition (c) FAILED ; floor-bound but only {unsold:.1f} kWh of window left unsold, under the {CONDITION_C_MARGIN_KWH:.1f} kWh margin')
    net = sell - marginal_buy / k.rte - k.degradation
    extra_kwh = (CEILING_TRIAL - CEILING_BASE) * DISCHARGE_KWH_PER_PCT * PACK_TO_METER
    money = f'marginal {extra_kwh:.1f} kWh: sell {sell * 100:.1f}c - buy {marginal_buy * 100:.1f}c/{k.rte:.2f} - degradation {k.degradation * 100:.0f}c = {net * 100:+.2f}c/kWh ({net * extra_kwh:+.2f} EUR)'
    if net <= 1e-09:
        return (CEILING_BASE, f'ceiling 90: condition (b) FAILED ; {money}')
    how = 'at no extra slot ; the chosen block already covers both targets, so the marginal kWh is priced at its dearest committed slot' if free_marginal else f'using {len(added)} added slot(s)'
    return (CEILING_TRIAL, f'ceiling {CEILING_TRIAL:.0f} (within the installation limit): (a) planner reaches {CEILING_TRIAL:.0f}% inside the cheap block {how}; (b) {money}; (c) evening is floor-bound with {unsold:.1f} kWh of window unsold')

def charge_window(plan):
    ch = [x for x in plan.periods if x.action == CHARGE]
    if not ch:
        return None
    return (min((x.start_slot for x in ch)), max((x.end_slot for x in ch)))

def apply_owner_window(plan, spec: str, k: Contract):
    try:
        a, b = spec.split('-')
        s0, s1 = (_hhmm_to_slot(a.strip()), _hhmm_to_slot(b.strip()))
    except Exception:
        raise SystemExit(f'--window wants HH:MM-HH:MM, got {spec!r}')
    if not 0 < s0 < s1 <= SLOTS_PER_DAY:
        raise SystemExit(f'--window {spec} is empty, inverted, or crosses midnight')
    kept = [p for p in plan.periods if p.action != CHARGE]
    if any((p.start_slot < s1 and s0 < p.end_slot for p in kept)):
        raise SystemExit(f'--window {spec} overlaps a non-charge period ; refusing')
    was = len(plan.charge_slots)
    directed = Period(index=0, start_slot=s0, end_slot=s1, action=CHARGE, soc_target=int(k.chg_soc_target), power_w=8000, grid_charge=True, sell=False)
    plan.periods = sorted(kept + [directed], key=lambda p: p.start_slot)
    for i, p in enumerate(plan.periods, start=1):
        p.index = i
    plan.charge_slots = list(range(s0, s1))
    plan.warnings.append(f"OWNER-DIRECTED WINDOW {spec} = {s1 - s0} slots replaced the planner's {was}-slot charge block (planner sizes to SoC at plan time ; F2)")
    return plan
