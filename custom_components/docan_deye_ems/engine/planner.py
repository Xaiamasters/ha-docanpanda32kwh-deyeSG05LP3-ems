"""Production planning policy. Pure calculations with explicit inputs."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Sequence
from .market import exports as export_tariffs
SLOTS_PER_DAY = 96
SLOT_H = 0.25
CHARGE = 'charge'
EXPORT = 'export'
PASS = 'pass'

def _hhmm_to_slot(hhmm: str, slot_min: int=15) -> int:
    hh, mm = (int(x) for x in str(hhmm).split(':'))
    return (hh * 60 + mm) // slot_min

@dataclass(frozen=True)
class Contract:
    household_profile: bool = False
    maximum_soc: float = 95.0
    program_power_w: int = 8000
    baseline_load_kw: float = 1.0
    meter_factor: float = .95
    day_slots: int = 96
    earliest_charge_slot: int = 0
    window_end_slot: int = 65
    allow_export: bool = True
    max_periods: int = 3
    export_via_tou: bool = False
    max_charge_runs: int = 1
    max_export_clusters: int = 4
    merge_gap_max: int = 3
    merge_price_tol: float = 0.01
    export_min_run: int = 1
    chg_soc_target: float = 90.0
    deadline_slot: int = 72
    rte: float = 0.87
    degradation: float = 0.04
    export_price_deduction: float = 0.0
    min_margin: float = 0.01
    export_power_w: float = 7900.0
    cap_kwh: float = 32.15
    export_start_slot: int = 72
    export_end_slot: int = 96
    export_end_hhmm: str = '24:00'
    export_cap_kwh: float = 12.0
    never_empty_pct: float = 25.0
    charge_setpoint_v: float = 55.2
    hard_bound_v: float = 56.0
    idle_voltage_v: float = 49.0
    curve: tuple = ((12.5, 8.07), (17.5, 8.23), (22.5, 8.29), (27.5, 8.36), (32.5, 8.41), (37.5, 8.44), (42.5, 8.45), (47.5, 8.46), (52.5, 8.47), (57.5, 8.48), (62.5, 8.45), (67.5, 7.89), (72.5, 6.95), (77.5, 6.63), (82.5, 6.44), (87.5, 6.34), (100.0, 6.34))
    reserve_kwh: float = 8.04
    l_overn_kwh: float = 4.7
    b_morn_kwh: float = 3.0
    floor_min_pct: int = 64
    nightly_reserve_pct: int | None = None
    floor_skip_export_pct: int = 70
    overnight_reference_hours: float = 8.5

    @property
    def reserve_floor_pct(self) -> int:
        return int(self.nightly_reserve_pct if self.nightly_reserve_pct is not None else self.floor_min_pct)

    @property
    def export_kwh_per_slot(self) -> float:
        return self.export_power_w / 1000.0 * SLOT_H

    @staticmethod
    def from_dict(d: dict) -> 'Contract':
        p = d.get('planner', {})
        h = d.get('house_load', {})
        cc = d.get('charge_curve', {})
        hhmm = str(p.get('charge_deadline_hhmm', '18:00'))
        hh, mm = (int(x) for x in hhmm.split(':'))
        return Contract(max_periods=int(p.get('max_periods', 3)), export_via_tou=bool(p.get('export_via_tou', False)), max_charge_runs=int(p.get('max_charge_runs', 1)), max_export_clusters=int(p.get('max_export_clusters', 4)), merge_gap_max=int(p.get('merge_gap_max', 3)), merge_price_tol=float(p.get('merge_price_tol', 0.01)), export_min_run=int(p.get('export_min_run', 1)), chg_soc_target=float(p.get('chg_soc_target', 90)), deadline_slot=(hh * 60 + mm) // int(p.get('slot_min', 15)), rte=float(p.get('rte', 0.87)), degradation=float(p.get('degradation', 0.04)), min_margin=float(p.get('min_margin', 0.01)), export_power_w=float(p.get('export_power_w', 7900)), cap_kwh=float(p.get('cap_kwh', 32.15)), export_start_slot=_hhmm_to_slot(p.get('export_window_start_hhmm', '18:00'), int(p.get('slot_min', 15))), never_empty_pct=float(h.get('never_empty_pct', 25)), curve=tuple(((float(s), float(k)) for s, k in cc.get('points', []))) or Contract.curve, reserve_kwh=float(h.get('reserve_kwh', 8.04)), l_overn_kwh=float(h.get('l_overn_kwh', 4.7)), b_morn_kwh=float(h.get('b_morn_kwh', 3.0)), floor_min_pct=int(h.get('floor_min_pct', 25)), floor_skip_export_pct=int(h.get('floor_skip_export_pct', 70)), overnight_reference_hours=float(h.get('overnight_reference_hours', 8.5)))

@dataclass
class Run:
    start: int
    end: int
    action: str

    @property
    def n(self) -> int:
        return self.end - self.start

    @property
    def slots(self) -> range:
        return range(self.start, self.end)

    def key(self) -> tuple:
        return (self.start, self.end, self.action)

@dataclass
class Period:
    index: int
    start_slot: int
    end_slot: int
    action: str
    soc_target: int
    power_w: int
    grid_charge: bool
    sell: bool

    def hhmm(self, slot: int) -> str:
        return f'{slot * 15 // 60:02d}:{slot * 15 % 60:02d}'

    @property
    def start_hhmm(self) -> str:
        return self.hhmm(self.start_slot)

    @property
    def end_hhmm(self) -> str:
        return self.hhmm(self.end_slot % SLOTS_PER_DAY)

@dataclass
class Plan:
    periods: list
    charge_slots: list
    export_slots: list
    soc_end_of_charge: float
    target_reached: bool
    export_skipped_by_floor: bool
    floors: dict
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.periods:
            return 'no periods (self-use all day)'
        return ' | '.join((f'P{p.index} {p.start_hhmm}-{p.end_hhmm} {p.action} -> {p.soc_target}%' for p in self.periods))

def charge_kw(soc_pct: float, curve: Sequence) -> float:
    if not curve:
        raise ValueError('empty charge curve')
    if soc_pct <= curve[0][0]:
        return curve[0][1]
    if soc_pct >= curve[-1][0]:
        return curve[-1][1]
    for (s0, k0), (s1, k1) in zip(curve, curve[1:]):
        if s0 <= soc_pct <= s1:
            if s1 == s0:
                return k1
            t = (soc_pct - s0) / (s1 - s0)
            return k0 + t * (k1 - k0)
    return curve[-1][1]

def integrate_charge(soc0: float, slots: Sequence, k: Contract):
    soc = float(soc0)
    delivered = 0.0
    per_slot = {}
    for i in sorted(slots):
        room_kwh = (k.chg_soc_target - soc) / 100.0 * k.cap_kwh
        if room_kwh <= 1e-09:
            per_slot[i] = 0.0
            continue
        e = min(charge_kw(soc, k.curve) * SLOT_H, room_kwh)
        soc += e / k.cap_kwh * 100.0
        delivered += e
        per_slot[i] = e
    return (soc, delivered, per_slot)

def energy_needed(soc0: float, k: Contract) -> float:
    return max(0.0, (k.chg_soc_target - soc0) / 100.0 * k.cap_kwh)

def floor_pct(slot_end: int, next_charge_start: int | None, k: Contract) -> int:
    if next_charge_start is None:
        hours = k.overnight_reference_hours
    else:
        gap_slots = (next_charge_start - slot_end) % k.day_slots
        hours = gap_slots * SLOT_H
    frac = min(1.0, hours / k.overnight_reference_hours) if k.overnight_reference_hours else 1.0
    l_overn = k.l_overn_kwh * frac
    need = k.reserve_kwh + l_overn + k.b_morn_kwh
    return max(k.reserve_floor_pct, math.ceil(100.0 * need / k.cap_kwh))
B3B_LANDING_PCT = 25.0
B3B_HARD_PCT = 15.0
B3B_SAFETY = 1.05
B3B_MIN_NIGHTS = 5
HOLD_GAP_SLOTS = 1
DEFAULT_WINDOW_END_SLOT = 65

def armed_window(plan, prices: Sequence, k: Contract, default_end: int=DEFAULT_WINDOW_END_SLOT) -> tuple:
    if default_end == DEFAULT_WINDOW_END_SLOT:default_end = k.window_end_slot
    charges = [p for p in plan.periods if p.action == CHARGE]
    if not charges:
        return (None, None, 'no charge block ; nothing to arm')
    start = min((p.start_slot for p in charges))
    block_end = max((p.end_slot for p in charges))
    sells = sorted((p.start_slot for p in plan.periods if p.action == EXPORT))
    if not sells:
        end = max(block_end, default_end)
        return (start, end, f'no export planned ; window ends at the default {_slot_hhmm(default_end)}')
    limit = sells[0] - HOLD_GAP_SLOTS
    if limit <= block_end:
        return (start, block_end, f'first sell {_slot_hhmm(sells[0])} leaves no room after the charge block ends {_slot_hhmm(block_end)} ; no hold')
    hold_slots = range(max(block_end, default_end), limit)
    sell_px = [export_tariffs(prices)[i] for p in plan.periods if p.action == EXPORT for i in range(p.start_slot, p.end_slot)]
    imp_px = [prices[i] for i in hold_slots] or [prices[min(limit, len(prices) - 1)]]
    sell_mean, imp_mean = (_mean(sell_px), _mean(imp_px))
    if sell_mean <= imp_mean:
        end = max(block_end, default_end)
        return (start, end, f'hold does NOT pay: evening {sell_mean * 100:.1f}c <= afternoon {imp_mean * 100:.1f}c ; window ends {_slot_hhmm(end)}')
    return (start, limit, f'hold pays: evening {sell_mean * 100:.1f}c > afternoon {imp_mean * 100:.1f}c; window ends {_slot_hhmm(limit)}, one slot before the first sell at {_slot_hhmm(sells[0])}')

def _slot_hhmm(slot: int) -> str:
    return f'{slot * 15 // 60:02d}:{slot * 15 % 60:02d}'

def assert_window_never_sells(start: int, end: int, plan) -> None:
    if start is None or end is None:
        return
    armed = set(range(start, end))
    for p in plan.periods:
        if p.action != EXPORT:
            continue
        clash = armed & set(range(p.start_slot, p.end_slot))
        if clash:
            raise ValueError(f'armed window {_slot_hhmm(start)}-{_slot_hhmm(end)} overlaps the export at {_slot_hhmm(p.start_slot)}-{_slot_hhmm(p.end_slot)} ({len(clash)} slot(s)) ; the gate would re-charge at peak prices')

def nightly_reserve(draws: Sequence, min_nights: int=B3B_MIN_NIGHTS, fallback_pct: int=64) -> dict:
    xs = [float(d) for d in draws]
    n = len(xs)
    if n < min_nights:
        return {'pct': int(fallback_pct), 'n': n, 'p50': percentile(xs, 50) if xs else 0.0, 'p90': percentile(xs, 90) if xs else 0.0, 'landing': 0.0, 'hard': 0.0, 'binding': f'COLD START ; {n} night(s) < {min_nights}, pinned fallback'}
    p50 = percentile(xs, 50)
    p90 = percentile(xs, 90)
    landing = B3B_LANDING_PCT + p50
    hard = B3B_HARD_PCT + B3B_SAFETY * p90
    raw = max(landing, hard)
    pct = int(math.ceil(max(raw, B3B_LANDING_PCT)))
    binding = 'landing (typical night)' if landing >= hard else 'hard (p90 night vs the 15% shutdown)'
    if raw < B3B_LANDING_PCT:
        binding = f'A8 never-empty clamp ; formula gave {raw:.1f}%, negative draws present'
    return {'pct': pct, 'n': n, 'p50': p50, 'p90': p90, 'landing': landing, 'hard': hard, 'binding': binding}

def blend_cold_start(measured: float | None, default: float, n_valid: int) -> float:
    if measured is None or n_valid < 7:
        return default
    if n_valid >= 28:
        return measured
    theta = n_valid / 28.0
    return theta * measured + (1.0 - theta) * default

def _mean(xs: Sequence) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0

def percentile(xs: Sequence, q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[int(pos)]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)

def run_mean(r: Run, prices: Sequence) -> float:
    rates = export_tariffs(prices) if r.action == EXPORT else prices
    return _mean((rates[i] for i in r.slots))
CHARGE_FALLBACK_FLOOR_PCT = 50.0
CHARGE_FLOOR_CEILING_PCT = 95.0

def charge_floor_from_draw(draws, last_known_floor=None):
    res = nightly_reserve(list(draws or []))
    n = int(res.get('n', 0))
    if n < B3B_MIN_NIGHTS:
        floor = max(float(last_known_floor or 0.0), CHARGE_FALLBACK_FLOOR_PCT)
        known = f', last known {float(last_known_floor):.0f}%' if last_known_floor else ''
        return (floor, f"floor {floor:.0f}% = FALLBACK (fail-closed high): {res.get('binding')}{known}")
    measured = float(res['pct'])
    floor = measured
    if floor > CHARGE_FLOOR_CEILING_PCT:
        return (CHARGE_FLOOR_CEILING_PCT, f'floor {CHARGE_FLOOR_CEILING_PCT:.0f}% = CLAMPED from {floor:.0f}% (A9 ceiling); the night needs more than the pack holds, so tier 1 buys everything available and the margin test cannot bind')
    return (floor, f"floor {floor:.0f}% = B3-b reserve, {res.get('binding')}, {n} nights, p50 {res.get('p50'):.1f} p90 {res.get('p90'):.1f}")

def max_worth_paying(displaced, k):
    return max(0.0, (float(displaced) - k.degradation - k.min_margin) * k.rte)

def evening_overnight_slots(n_slots=SLOTS_PER_DAY, start_hour=17, end_hour=7):
    per_hour = n_slots // 24
    return list(range(start_hour * per_hour, n_slots)) + list(range(0, end_hour * per_hour))

def displaced_price(prices, slots):
    vals = [prices[i] for i in slots if isinstance(i, int) and 0 <= i < len(prices)]
    return sum(vals) / len(vals) if vals else None

def select_charge_slots(prices: Sequence, soc0: float, k: Contract, now_slot: int=0, floor_soc: float | None=None, floor_provenance: str | None=None, discharge_slots: Sequence | None=None):
    if soc0 >= k.chg_soc_target:
        return ([], float(soc0), True)
    limit = min(k.deadline_slot, len(prices))
    start = max(k.earliest_charge_slot, int(now_slot))
    if start >= limit:
        return ([], float(soc0), False)
    order = sorted(range(start, limit), key=lambda i: (prices[i], i))
    if floor_soc is not None:
        return _select_charge_slots_gated(prices, soc0, k, order, floor_soc, floor_provenance, discharge_slots)
    chosen = []
    for i in order:
        chosen.append(i)
        soc, _, per = integrate_charge(soc0, chosen, k)
        if soc >= k.chg_soc_target - 1e-09:
            pruned = [s for s in sorted(chosen) if per.get(s, 0.0) > 1e-09]
            while True:
                soc2, _, per2 = integrate_charge(soc0, pruned, k)
                nxt = [s for s in pruned if per2.get(s, 0.0) > 1e-09]
                if nxt == pruned:
                    break
                pruned = nxt
            soc2, _, _ = integrate_charge(soc0, pruned, k)
            return (pruned, soc2, True)
    soc, _, _ = integrate_charge(soc0, chosen, k)
    return (sorted(chosen), soc, False)

def _select_charge_slots_gated(prices, soc0, k, order, floor_soc, floor_provenance, discharge_slots):
    chosen = []
    for i in order:
        soc, _, _ = integrate_charge(soc0, chosen, k)
        if soc >= floor_soc - 1e-09:
            break
        chosen.append(i)
    safety_n = len(chosen)
    soc, _, _ = integrate_charge(soc0, chosen, k)
    if soc >= k.chg_soc_target - 1e-09:
        return (sorted(chosen), soc, True)
    slots = evening_overnight_slots(len(prices)) if discharge_slots is None else discharge_slots
    ref = displaced_price(prices, slots)
    if ref is None:
        return (sorted(chosen), soc, False)
    ceiling = max_worth_paying(ref, k)
    taken = set(chosen)
    for i in order:
        if i in taken or prices[i] > ceiling:
            continue
        soc, _, _ = integrate_charge(soc0, chosen, k)
        if soc >= k.chg_soc_target - 1e-09:
            break
        chosen.append(i)
    soc, _, per = integrate_charge(soc0, chosen, k)
    pruned = [s for s in sorted(chosen) if per.get(s, 0.0) > 1e-09]
    soc, _, _ = integrate_charge(soc0, pruned, k)
    return (sorted(pruned), soc, soc >= k.chg_soc_target - 1e-09)

def select_export_slots(prices: Sequence, charge_slots: Sequence, k: Contract, soc_start: float, floor_pct_binding: float | None=None, now_slot: int=0):
    if not prices or not k.allow_export:
        return []
    cset = set(charge_slots)
    charge_mean = _mean((prices[i] for i in cset)) if cset else 0.0
    prices = export_tariffs(prices)
    breakeven = charge_mean / k.rte + k.degradation + k.min_margin
    threshold = max(percentile(prices, 75), breakeven)
    earliest = max(k.export_start_slot, max(0, int(now_slot)))
    eligible = [i for i, c in enumerate(prices) if c >= threshold and i not in cset and earliest <= i < k.export_end_slot]
    binding = k.never_empty_pct if floor_pct_binding is None else max(k.never_empty_pct, floor_pct_binding)
    budget_kwh = max(0.0, (soc_start - binding) / 100.0 * k.cap_kwh)
    affordable = math.ceil(budget_kwh / k.export_kwh_per_slot) if k.export_kwh_per_slot else 0
    ranked = sorted(eligible, key=lambda i: (-prices[i], i))
    return sorted(ranked[:affordable])

def allocate_export_energy(runs: list, prices: Sequence, soc_start: float, k: Contract, floor_pct_binding: float | None=None):
    exports = [r for r in runs if r.action == EXPORT]
    others = [r for r in runs if r.action != EXPORT]
    if not exports:
        return (list(runs), {})
    binding = k.never_empty_pct if floor_pct_binding is None else max(k.never_empty_pct, floor_pct_binding)
    budget = max(0.0, (soc_start - binding) / 100.0 * k.cap_kwh)
    alloc = {}
    kept = []
    for r in sorted(exports, key=lambda x: (-run_mean(x, prices), x.start)):
        if budget <= 1e-09:
            break
        take = min(r.n * k.export_kwh_per_slot, budget)
        if take <= 1e-09:
            break
        alloc[r.key()] = take
        budget -= take
        kept.append(r)
    return (sorted(others + kept, key=lambda r: r.start), alloc)

def build_runs(actions: Sequence) -> list:
    runs = []
    i = 0
    n = len(actions)
    while i < n:
        a = actions[i]
        if a == PASS:
            i += 1
            continue
        j = i
        while j < n and actions[j] == a:
            j += 1
        runs.append(Run(i, j, a))
        i = j
    return runs

def merge_runs(runs: list, prices: Sequence, k: Contract) -> list:
    runs = list(runs)
    changed = True
    while changed:
        changed = False
        for a in range(len(runs) - 1):
            r1, r2 = (runs[a], runs[a + 1])
            if r1.action != r2.action:
                continue
            gap = list(range(r1.end, r2.start))
            if not gap or len(gap) > k.merge_gap_max:
                continue
            rates = export_tariffs(prices) if r1.action == EXPORT else prices
            mg = _mean((rates[i] for i in gap))
            mu = _mean((rates[i] for i in list(r1.slots) + list(r2.slots)))
            ok = mg <= mu + k.merge_price_tol if r1.action == CHARGE else mg >= mu - k.merge_price_tol
            if ok:
                runs[a:a + 2] = [Run(r1.start, r2.end, r1.action)]
                changed = True
                break
    return runs

def protected_charge_runs(runs: list, prices: Sequence, soc0: float, k: Contract) -> set:
    charge_runs = [r for r in runs if r.action == CHARGE]
    if not charge_runs:
        return set()
    if soc0 >= k.chg_soc_target:
        return set()
    order = sorted(charge_runs, key=lambda r: (run_mean(r, prices), r.start))
    kept, slots = ([], [])
    for r in order:
        kept.append(r)
        slots.extend(r.slots)
        soc, _, _ = integrate_charge(soc0, slots, k)
        if soc >= k.chg_soc_target - 1e-09:
            break
    return {r.key() for r in kept}

def _soc_at_run_start(runs: list, target_run: Run, soc0: float, k: Contract) -> float:
    soc = float(soc0)
    for r in sorted(runs, key=lambda x: x.start):
        if r.start >= target_run.start:
            break
        if r.action == CHARGE:
            soc, _, _ = integrate_charge(soc, list(r.slots), k)
        else:
            soc -= r.n * k.export_kwh_per_slot / k.cap_kwh * 100.0
    return max(0.0, min(100.0, soc))

def run_value(r: Run, runs: list, prices: Sequence, soc0: float, floors: dict, peak_mean: float, k: Contract) -> float:
    if r.action == EXPORT:
        energy = r.n * k.export_kwh_per_slot
        soc_in = _soc_at_run_start(runs, r, soc0, k)
        room = max(0.0, (soc_in - floors.get(r.end, k.reserve_floor_pct)) / 100.0 * k.cap_kwh)
        energy = min(energy, room)
        return energy * run_mean(r, prices)
    _, _, per = integrate_charge(soc0, list(r.slots), k)
    return sum((per.get(i, 0.0) * (peak_mean - prices[i]) for i in r.slots))

def over_makes_progress(before: list, after: list, k: Contract) -> bool:

    def n(rs):
        return len(rs) if k.export_via_tou else sum((1 for r in rs if r.action == CHARGE))
    return n(after) < n(before)

def _force_merge_once(runs: list, prices: Sequence, k: Contract, only: str | None=None):
    best = None
    for a in range(len(runs) - 1):
        r1, r2 = (runs[a], runs[a + 1])
        if r1.action != r2.action:
            continue
        if only is not None and r1.action != only:
            continue
        gap = list(range(r1.end, r2.start))
        if not gap:
            continue
        rates = export_tariffs(prices) if r1.action == EXPORT else prices
        mg = _mean((rates[i] for i in gap))
        mu = _mean((rates[i] for i in list(r1.slots) + list(r2.slots)))
        penalty = mg - mu if r1.action == CHARGE else mu - mg
        penalty *= len(gap)
        if best is None or penalty < best[0]:
            best = (penalty, a)
    if best is None:
        return None
    a = best[1]
    out = list(runs)
    out[a:a + 2] = [Run(runs[a].start, runs[a + 1].end, runs[a].action)]
    return out

def export_set_value(runs: list, prices: Sequence, budget_kwh: float, k: Contract) -> float:
    per = k.export_kwh_per_slot
    if per <= 0:
        return 0.0
    total, left = (0.0, max(0.0, budget_kwh))
    for p in sorted((export_tariffs(prices)[i] for r in runs if r.action == EXPORT for i in r.slots), reverse=True):
        if left <= 1e-09:
            break
        take = min(per, left)
        total += take * p
        left -= take
    return total

def cap_export_clusters(runs: list, prices: Sequence, budget_kwh: float, k: Contract):
    runs = list(runs)
    warnings = []
    while len([r for r in runs if r.action == EXPORT]) > k.max_export_clusters:
        exports = [r for r in runs if r.action == EXPORT]
        whole = export_set_value(exports, prices, budget_kwh, k)
        scored = [(whole - export_set_value([x for x in exports if x.key() != r.key()], prices, budget_kwh, k), r) for r in exports]
        loss, victim = min(scored, key=lambda sr: (sr[0], run_mean(sr[1], prices), -sr[1].start))
        warnings.append(f'B11 cluster cap: dropped {victim.n}-slot export at {victim.start * 15 // 60:02d}:{victim.start * 15 % 60:02d} (mean {run_mean(victim, prices):.4f}) ; {len(exports)} clusters exceeds the {k.max_export_clusters} allowed; marginal loss EUR {loss:.2f}')
        runs = [r for r in runs if r.key() != victim.key()]
    return (runs, warnings)

def drop_to_limit(runs: list, prices: Sequence, soc0: float, floors: dict, k: Contract):
    runs = list(runs)
    warnings = []
    export_prices = sorted(export_tariffs(prices), reverse=True)[:8]
    peak_mean = _mean(export_prices)
    budget_kwh = max(0.0, (soc0 - max(k.never_empty_pct, k.reserve_floor_pct)) / 100.0 * k.cap_kwh)
    runs, cap_warn = cap_export_clusters(runs, prices, budget_kwh, k)
    warnings.extend(cap_warn)
    limit = k.max_periods if k.export_via_tou else min(k.max_charge_runs, k.max_periods)

    def over(rs):
        return len(rs) > limit if k.export_via_tou else len([r for r in rs if r.action == CHARGE]) > limit
    while over(runs):
        protected = protected_charge_runs(runs, prices, soc0, k)
        pool = runs if k.export_via_tou else [r for r in runs if r.action == CHARGE]
        candidates = [r for r in pool if r.key() not in protected]
        if not candidates:
            forced = _force_merge_once(runs, prices, k, only=None if k.export_via_tou else CHARGE)
            if forced is None or not over_makes_progress(runs, forced, k):
                warnings.append(f'{sum((1 for r in runs if k.export_via_tou or r.action == CHARGE))} protected run(s) exceed the {limit}-period limit and none can be merged ; day refused')
                break
            if not any(('023-E fallback' in w for w in warnings)):
                warnings.append(f'023-E fallback: the protected charge set needed more runs than the {limit}-period limit, so windows were widened to fit (costlier slots ride along; the target is preserved)')
            runs = forced
            continue
        scored = [(run_value(r, runs, prices, soc0, floors, peak_mean, k), r) for r in candidates]
        min_v = min((v for v, _ in scored))
        tied = [r for v, r in scored if abs(v - min_v) < 1e-12]
        if len(tied) > 1:
            exports = [r for r in tied if r.action == EXPORT]
            charges = [r for r in tied if r.action == CHARGE]
            if exports:
                victim = min(exports, key=lambda r: (run_mean(r, prices), -r.start))
            elif charges:
                victim = max(charges, key=lambda r: (run_mean(r, prices), r.start))
            else:
                victim = tied[0]
            same = [r for r in tied if abs(run_mean(r, prices) - run_mean(victim, prices)) < 1e-12]
            if len(same) > 1:
                victim = max(same, key=lambda r: r.start)
        else:
            victim = tied[0]
        runs = [r for r in runs if r.key() != victim.key()]
        runs = merge_runs(runs, prices, k)
    return (runs, warnings)

def emit_periods(runs: list, prices: Sequence, soc0: float, floors: dict, k: Contract, alloc: dict | None=None):
    alloc = alloc or {}
    periods = []
    soc = float(soc0)
    ordered = sorted(runs, key=lambda r: r.start)
    last_charge = max((r.start for r in ordered if r.action == CHARGE), default=None)
    for idx, r in enumerate(ordered, start=1):
        if r.action == CHARGE:
            soc_entry = soc
            soc_end, _, _ = integrate_charge(soc, list(r.slots), k)
            target = int(k.chg_soc_target) if r.start == last_charge else int(min(k.chg_soc_target, math.ceil(soc_end)))
            soc = soc_end
            periods.append(Period(idx, r.start, r.end, CHARGE, target, int(round(charge_kw(soc_entry, k.curve) * 1000)), grid_charge=True, sell=False))
        else:
            f = max(floors.get(r.end, k.reserve_floor_pct), k.never_empty_pct)
            take = alloc.get(r.key(), r.n * k.export_kwh_per_slot)
            soc_after = soc - take / k.cap_kwh * 100.0
            soc = max(f, soc_after)
            periods.append(Period(idx, r.start, r.end, EXPORT, int(math.ceil(soc)), int(k.export_power_w), grid_charge=False, sell=True))
    return periods

def assert_invariants(periods: list, k: Contract):
    n_chg = sum((1 for p in periods if p.action == CHARGE))
    n_exp = sum((1 for p in periods if p.action == EXPORT))
    if k.export_via_tou and len(periods) > k.max_periods:
        raise ValueError(f'{len(periods)} periods exceeds hardware limit {k.max_periods}')
    if n_chg > min(k.max_charge_runs, k.max_periods):
        raise ValueError(f'{n_chg} charge periods exceeds the {min(k.max_charge_runs, k.max_periods)} the TOU block can hold')
    if n_exp > k.max_export_clusters:
        raise ValueError(f'{n_exp} export clusters exceeds the B11 cap {k.max_export_clusters}')
    prev_end = -1
    for p in periods:
        if p.end_slot <= p.start_slot:
            raise ValueError(f'P{p.index} is empty or inverted')
        if p.start_slot < prev_end:
            raise ValueError(f'P{p.index} overlaps the previous period')
        if p.start_slot > k.day_slots or p.end_slot > k.day_slots:
            raise ValueError(f'P{p.index} crosses 00:00 without being split')
        prev_end = p.end_slot

def plan_day(prices: Sequence, soc0: float, k: Contract, now_slot: int=0) -> Plan:
    if len(prices) != k.day_slots:
        raise ValueError(f'expected {k.day_slots} prices, got {len(prices)}')
    warnings = []
    charge_slots, soc_after_charge, reached = select_charge_slots(prices, soc0, k, now_slot=now_slot)
    if not reached:
        warnings.append(f'023-E UNREACHABLE from slot {now_slot}: remaining pre-deadline slots reach only {soc_after_charge:.1f}%, target {k.chg_soc_target:.0f}%')
    first_charge = min(charge_slots) if charge_slots else None
    binding_floor = floor_pct(k.day_slots, first_charge, k)
    export_slots = select_export_slots(prices, charge_slots, k, soc_after_charge, floor_pct_binding=binding_floor, now_slot=now_slot)
    actions = [PASS] * k.day_slots
    for i in charge_slots:
        actions[i] = CHARGE
    for i in export_slots:
        actions[i] = EXPORT
    for r in build_runs(actions):
        if r.action == EXPORT and r.n < k.export_min_run:
            for i in r.slots:
                actions[i] = PASS
    runs = build_runs(actions)
    charge_starts = sorted({r.start for r in runs if r.action == CHARGE})

    def next_charge_after(slot: int):
        for s in charge_starts:
            if s >= slot:
                return s
        return charge_starts[0] if charge_starts else None

    def compute_floors(rs):
        return {r.end: floor_pct(r.end, next_charge_after(r.end), k) for r in rs if r.action == EXPORT}
    floors = compute_floors(runs)
    export_skipped = bool(floors) and min(floors.values()) > k.floor_skip_export_pct
    if export_skipped:
        warnings.append(f'export skipped: floor {min(floors.values())}% > {k.floor_skip_export_pct}% ; economic skip, not an alarm')
        runs = [r for r in runs if r.action != EXPORT]
        export_slots = []
        floors = {}
    runs = merge_runs(runs, prices, k)
    floors = compute_floors(runs)
    runs, drop_warnings = drop_to_limit(runs, prices, soc0, floors, k)
    warnings.extend(drop_warnings)
    floors = compute_floors(runs)
    before = {r.key() for r in runs if r.action == EXPORT}
    runs, alloc = allocate_export_energy(runs, prices, soc_after_charge, k, floor_pct_binding=binding_floor)
    starved = before - {r.key() for r in runs}
    if starved:
        warnings.append(f'{len(starved)} export window(s) dropped: the dearer windows claimed all energy above the {k.never_empty_pct:.0f}% never-empty floor')
    floors = compute_floors(runs)
    periods = emit_periods(runs, prices, soc0, floors, k, alloc)
    assert_invariants(periods, k)
    return Plan(periods=periods, charge_slots=sorted((i for r in runs if r.action == CHARGE for i in r.slots)), export_slots=sorted((i for r in runs if r.action == EXPORT for i in r.slots)), soc_end_of_charge=soc_after_charge, target_reached=reached, export_skipped_by_floor=export_skipped, floors=dict(sorted(floors.items())), warnings=warnings)
