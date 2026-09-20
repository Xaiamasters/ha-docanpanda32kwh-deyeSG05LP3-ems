"""Six-program firmware schedule conversion and independent preflight checks."""
from __future__ import annotations
from .planner import CHARGE, EXPORT, PASS, SLOTS_PER_DAY, Contract, Plan
N_PROGRAMS = 6

def prog(n: int) -> dict:
    return {'time': f'program_{n}_time', 'power': f'program_{n}_power', 'soc': f'program_{n}_soc', 'voltage': f'program_{n}_voltage', 'charging': f'program_{n}_charging'}

def slot_to_hhmm(slot: int) -> str:
    slot %= SLOTS_PER_DAY
    return f'{slot * 15 // 60:02d}:{slot * 15 % 60:02d}:00'

def plan_to_programs(plan: Plan, k: Contract, charge_only: bool=False) -> dict:
    exports = [p for p in plan.periods if p.action == EXPORT]
    if exports and (not charge_only):
        raise ValueError(f"{len(exports)} export period(s) in plan: native-TOU export is unavailable while work_mode is not 'Export First'. Export stays on EXPORT_RECIPE. Pass charge_only=True to write the charge half and leave export alone.")
    if exports:
        pass
    bounds = []
    for p in plan.periods:
        if p.action == CHARGE:
            bounds.append((p.start_slot, {'charging': 'Grid', 'soc': int(p.soc_target), 'power': 8000, 'voltage': k.charge_setpoint_v}))
            bounds.append((p.end_slot, {'charging': 'Disabled', 'soc': int(k.never_empty_pct), 'power': 8000, 'voltage': k.idle_voltage_v}))
    bounds.sort(key=lambda b: b[0])
    if len(bounds) > N_PROGRAMS:
        raise ValueError(f'plan needs {len(bounds)} TOU boundaries, hardware has {N_PROGRAMS}: at most {N_PROGRAMS // 2} separated windows')
    idle = {'charging': 'Disabled', 'soc': int(k.never_empty_pct), 'power': 8000, 'voltage': k.idle_voltage_v}
    spare_slot = SLOTS_PER_DAY - 1
    while len(bounds) < N_PROGRAMS:
        bounds.append((spare_slot, dict(idle)))
        spare_slot -= 1
    bounds.sort(key=lambda b: b[0])
    out = {}
    for i, (slot, cfg) in enumerate(bounds, start=1):
        if slot == 0:
            raise ValueError('period starts at 00:00; TOU Time range is documented 01:00-24:00 and 00:00 is UNPROVEN ; refusing')
        out[i] = dict(cfg, time=slot_to_hhmm(slot))
    return out

def validate(plan: Plan, k: Contract, charge_only: bool=False):
    ps = sorted(plan.periods, key=lambda p: p.start_slot)
    writable = [p for p in ps if p.action == CHARGE] if charge_only else ps
    if len(writable) > k.max_periods:
        raise ValueError(f'{len(writable)} periods exceeds the {k.max_periods} limit')
    prev_end = -1
    for p in ps:
        if p.end_slot <= p.start_slot:
            raise ValueError(f'P{p.index} empty or inverted')
        if p.start_slot < prev_end:
            raise ValueError(f'P{p.index} overlaps the previous period')
        if not (0 <= p.start_slot < SLOTS_PER_DAY and 0 < p.end_slot <= SLOTS_PER_DAY):
            raise ValueError(f'P{p.index} out of range / crosses 00:00 unsplit')
        if p.action == CHARGE and (not 0 <= p.soc_target <= 100):
            raise ValueError(f'P{p.index} SoC target {p.soc_target} out of range')
        prev_end = p.end_slot
    return True

def build_diff(desired: dict, current: dict):
    diff = []
    for n in range(1, N_PROGRAMS + 1):
        ents = prog(n)
        for field, want in desired[n].items():
            have = current['programs'][n].get(field)
            same = False
            if field in ('power', 'soc', 'voltage'):
                try:
                    same = abs(float(have) - float(want)) < 1e-06
                except (TypeError, ValueError):
                    same = False
            else:
                same = str(have)[:5] == str(want)[:5] if field == 'time' else str(have) == str(want)
            if not same:
                diff.append((ents[field], have, want))
    return diff

def preflight(current: dict):
    try:
        p1 = abs(float(current.get('p1') or 0))
    except (TypeError, ValueError):
        p1 = 0.0
    if p1 > 500:
        raise SystemExit(f'REFUSED: |P1| = {p1:.0f} W > 500 W ; an operation looks active. TOU writes happen idle-state only (18 Jul canon).')
    if current.get('tou_enable') in (None, 'unavailable', 'unknown'):
        raise SystemExit('REFUSED: TOU enable entity unavailable ; integration not healthy')
    return True

def _readback_ok(back, entity, new) -> bool:
    ok = str(back)[:5] == str(new)[:5] if entity.endswith('_time') else None
    if ok is None:
        try:
            ok = abs(float(back) - float(new)) < 1e-06
        except (TypeError, ValueError):
            ok = str(back) == str(new)
    return bool(ok)
