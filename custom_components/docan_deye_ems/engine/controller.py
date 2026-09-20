"""Production controller and verified-write sequence with injected transport and state."""
from __future__ import annotations
import math
import re
from dataclasses import replace
from .planner import CHARGE, EXPORT, Contract, plan_day
from .context import PolicyContext
from .market import exports

class ConvergeError(RuntimeError):
    pass

class Controller(PolicyContext):
    HOLD, IDLE = ('HOLD', 'IDLE')
    N_PROGRAMS = 6
    E_GRID_CHARGE = 'grid_charge'
    E_GRID_CHARGE_A = 'grid_charge_a'
    E_WORK_MODE = 'work_mode'
    E_SOLAR_SELL = 'solar_sell'
    E_ENERGY_PATTERN = 'energy_pattern'
    E_EXPORT_W = 'export_w'
    E_GRID_EXPORT_W = 'grid_export_w'
    THERMAL = [('mos_temperature', 70.0, 80.0), ('environment_temperature', 50.0, 50.0), ('probe_1_temperature', 45.0, 45.0), ('probe_2_temperature', 45.0, 45.0), ('probe_3_temperature', 45.0, 45.0), ('probe_4_temperature', 45.0, 45.0)]
    CHARGE_V = 55.2
    HARD_V = 56.0
    PACK_LOW_V = 49.0
    IDLE_V = 49.0
    CHARGE_A = 160
    IDLE_A = 40
    EXPORT_W = 7900
    EXPORT_WORK_MODE = 'Export First'
    SAFE_WORK_MODE = 'Zero Export To CT'
    SAFE_ENERGY_PATTERN = 'Load First'
    NEVER_EMPTY = 25.0
    SOC_STOP = 90.0
    SOC_MAX = 95.0
    PROGRAM_W = 8000.0
    MIN_RUN_MIN = 5
    CEILING_BAND_POINTS = 2.0

    def ask_contract(self, k, soc_stop: float):
        return replace(k, chg_soc_target=soc_stop)

    def restart_threshold(self, soc_stop: float) -> float:
        return soc_stop - self.CEILING_BAND_POINTS

    def start_guards(self, s, plan, now_min=None, soc_stop=None):
        out = []
        stop = self.SOC_STOP if soc_stop is None else soc_stop
        restart = self.restart_threshold(stop)
        if not s.get('grid_charge_on') and s['soc'] is not None and (s['soc'] > restart):
            out.append(f"F20 dead band ; {s['soc']:.0f}% is above the {restart:.0f}% restart threshold (stop {stop:.0f}%, resume at or below {restart:.0f}%)")
        if not s.get('grid_charge_on'):
            left = self.minutes_to_window_close(s, plan, now_min)
            if left is not None and left < self.MIN_RUN_MIN:
                out.append(f'F20 no-start guard ; {left:.0f} min to window close, under the {self.MIN_RUN_MIN} min minimum run; a top-up begun now is pure churn')
        return out

    def minutes_to_window_close(self, s, plan, now_min=None):
        if now_min is None:
            n = self.now()
            now_min = n.hour * 60 + n.minute
        starts = []
        for p, prog in (s.get('programs') or {}).items():
            t = (prog or {}).get('time')
            if not t:
                continue
            try:
                hh, mm = str(t).split(':')[:2]
                starts.append(int(hh) * 60 + int(mm))
            except (ValueError, TypeError):
                continue
        later = [m for m in sorted(starts) if m > now_min]
        if not later:
            return None
        return later[0] - now_min
    MAX_AGE_S = {'docan': 300.0, 'deye': 120.0, 'p1': 120.0}

    def _hhmm(self, slot: int) -> str:
        return f'{slot * 15 // 60:02d}:{slot * 15 % 60:02d}'

    def validity(self, s):
        bad = []
        if s['stop']:
            bad.append('owner emergency stop')
        if self.storage.latch is not None:
            bad.append('STOP LATCH: ' + str(self.storage.latch.get('why', 'unreadable')))
        for field in ('stop', 'grid_charge_on', 'solar_sell_on'):
            if s[field] is None:
                bad.append(f'{field} unknown')
        if s['p1_w'] is None or not math.isfinite(s['p1_w']):
            bad.append('fiscal power unreadable')
        if s['alarm'] != 'OK':
            bad.append(f"device alarm {s['alarm']}")
        if s['batt_alarm'] != 'off':
            bad.append('battery alarm')
        if s['soc'] is None or not 0 <= s['soc'] <= 100:
            bad.append(f"SoC unusable ({s['soc']})")
        if s['pack_v'] is None or not 40 <= s['pack_v'] <= 60:
            bad.append(f"pack voltage unusable ({s['pack_v']})")
        if s['tou'] in (None, 'unavailable', 'unknown'):
            bad.append('TOU entity unavailable ; integration unhealthy')
        if not s['docan_fresh']:
            bad.append('docan data_fresh is off')
        if not s['truth_ready']:
            bad.append('battery truth_ready is off ; SoC truth not established')
        for src, limit in self.MAX_AGE_S.items():
            age = s['ages'].get(src)
            if age is None or not math.isfinite(age) or age < 0:
                bad.append(f'{src} age unknown')
            elif age > limit:
                bad.append(f'{src} stale {age:.0f}s > {limit:.0f}s')
        return bad

    def net_export_value(self, price, k):
        return price - k.export_price_deduction - k.degradation

    def marginal_value(self, s, k, floor_pct_now, slot=0):
        prices = s['prices']
        ex = exports(prices)[k.export_start_slot:]
        if not ex:
            return (0.0, 'no export slots remain')
        net = max((x-k.degradation if hasattr(prices,'exports') else self.net_export_value(x,k) for x in ex))
        ahead = [p for p in prices[slot:k.export_start_slot] if p is not None]
        ceiling = min(ahead) / k.rte if ahead and k.rte else None

        def capped(v, why):
            if ceiling is not None and ceiling < v:
                return (ceiling, f'{why}; CAPPED at replacement cost {ceiling * 100:.1f}c (cheapest refill {min(ahead) * 100:.1f}c / rte {k.rte})')
            return (v, why)
        sellable = max(0.0, (s['soc'] - floor_pct_now) / 100.0 * k.cap_kwh)
        if sellable < k.export_cap_kwh:
            return capped(net, f'energy-bound: {sellable:.1f} kWh above floor {floor_pct_now}% < {k.export_cap_kwh:.1f} kWh cap ; sells at {net * 100:.1f}c')
        tail = list(prices[min(len(prices) - 1, k.export_start_slot + 18):])
        tom = s.get('prices_tomorrow')
        if tom:
            cheap_at = min(range(len(tom)), key=lambda i: tom[i])
            tail += list(tom[:max(1, cheap_at)])
            span = 'tonight + tomorrow to the trough'
        else:
            span = "tonight only ; tomorrow's prices not published yet"
        offset = min(tail) if tail else net
        return capped(offset, f'CAP-bound: {sellable:.1f} kWh above floor {floor_pct_now}% >= {k.export_cap_kwh:.1f} kWh cap ; will not sell tonight; offsets a purchase at {offset * 100:.1f}c ({span})')

    def decide(self, s, k):
        if s['prices'] is None:
            return (self.IDLE, 'prices unavailable ; planner admission denied (never a physical veto)')
        if hasattr(s['prices'],'contract'):k=s['prices'].contract(k)
        slot = s['prices'].slot_at(self.now()) if hasattr(s['prices'],'slot_at') else min(95, (self.now().hour * 60 + self.now().minute) // 15)
        p_now = s['prices'][slot]
        if s['discharge_now'] and k.allow_export:
            return (EXPORT, 'owner SELL NOW')
        soc_stop, ceiling_src = self.plan_ceiling()
        plan = plan_day(s['prices'], s['soc'], self.ask_contract(k, soc_stop), now_slot=slot)
        clusters, pin_src = self.pinned_export(self.DAY_PLAN)
        if not k.allow_export:clusters=[]
        if clusters is not None:
            for st_slot, en_slot, fl in clusters:
                if st_slot <= slot < en_slot:
                    if s['soc'] <= fl:
                        return (self.IDLE, f"pinned export {self._hhmm(st_slot)}-{self._hhmm(en_slot)} but SoC {s['soc']}% is at its reserve floor {fl}% ; {pin_src}")
                    return (EXPORT, f'pinned export {self._hhmm(st_slot)}-{self._hhmm(en_slot)}, floor {fl}% ; {pin_src}')
            ex_runs, floor_now = ([], clusters[0][2] if clusters else k.reserve_floor_pct)
        else:
            ex_runs = [p for p in plan.periods if p.action == EXPORT]
            floor_now = plan.floors.get(ex_runs[0].end_slot, k.reserve_floor_pct) if ex_runs else k.reserve_floor_pct
            for p in plan.periods:
                if p.start_slot <= slot < p.end_slot and p.action == EXPORT:
                    return (EXPORT, f'P{p.index} {p.start_hhmm}-{p.end_hhmm} (unpinned ; {pin_src})')
        active = self.active_program(s)
        window_open = bool(active and str((s['programs'].get(active) or {}).get('charging')) == 'Grid')
        if not window_open:
            n = self.active_program(s)
            mode = (s['programs'].get(n) or {}).get('charging') if n else None
            return (self.IDLE, f"outside the armed window ; active period P{n} is '{mode}'. Point releases to idle; the battery serves the house.")
        if s.get('pause_charge'):
            return (self.HOLD, f"owner PAUSE-CHARGE is on ; {s['soc']:.0f}%, holding inside the window; point stays up, resumes when the toggle clears")
        plan_wants_now = any((p.start_slot <= slot < p.end_slot and p.action == CHARGE for p in plan.periods))
        at_target = s['soc'] >= soc_stop
        if plan_wants_now and (not at_target):
            blocked = self.start_guards(s, plan, soc_stop=soc_stop)
            if blocked:
                return (self.HOLD, f"{s['soc']:.0f}% below target and the slot is in the planned block, but not starting: {blocked} [{ceiling_src}]")
            return (CHARGE, f"executing the plan: {s['soc']:.0f}% < {soc_stop:.0f}%, slot is in the planned charge block [{ceiling_src}]")
        if at_target:
            return (self.HOLD, f"at target {s['soc']:.0f}%; armed window still open ; hold, point stays up, PV first and grid covers the rest [{ceiling_src}]")
        return (self.HOLD, f"{s['soc']:.0f}% below target but this slot is not in the planned block; armed window open so the point STAYS UP ; the firmware keeps a valid window whatever happens to this process (F18)")

    def action_gate(self, s, action):
        veto = []
        charging = action in (CHARGE,)
        discharging = action in (EXPORT,)
        if s['pack_v'] is not None and s['pack_v'] >= self.HARD_V and charging:
            veto.append(f"pack {s['pack_v']} V >= hard bound {self.HARD_V} V")
        if s['pack_v'] is not None and s['pack_v'] <= self.PACK_LOW_V and discharging:
            veto.append(f"pack {s['pack_v']} V <= floor {self.PACK_LOW_V} V")
        if s['soc'] is not None and s['soc'] <= self.NEVER_EMPTY and discharging:
            veto.append(f"SoC {s['soc']}% <= never-empty {self.NEVER_EMPTY}%")
        if s['soc'] is not None and s['soc'] >= self.SOC_MAX and charging:
            veto.append(f"SoC {s['soc']}% >= A9 max {self.SOC_MAX}%")
        for ent, derate, stop in self.THERMAL:
            name='MOS_STOP_C' if ent=='mos_temperature' else 'ENVIRONMENT_STOP_C' if ent=='environment_temperature' else 'PROBE_STOP_C'
            stop=min(stop,getattr(self,name,stop));derate=min(derate,stop)
            t = s['temps'].get(ent)
            if t is None or not math.isfinite(t):
                veto.append(f"{ent.rsplit('_', 2)[-2]} temperature unreadable")
            elif charging and t >= derate:
                veto.append(f"{ent.rsplit('_', 2)[-2]} {t} >= charge limit {derate}")
            elif discharging and t >= stop:
                veto.append(f"{ent.rsplit('_', 2)[-2]} {t} >= discharge limit {stop}")
        if action == self.HOLD and s['pack_v'] is not None and (s['pack_v'] >= self.HARD_V):
            veto.append(f"pack {s['pack_v']} V >= hard bound while holding")
        return veto

    def target_vector(self, action, s):
        active = self.active_program(s)
        if active is None:
            return (None, 'no active TOU program ; cannot address a voltage point')
        pv = f'program_{active}_voltage'
        off = {self.E_SOLAR_SELL: 'off', self.E_WORK_MODE: self.SAFE_WORK_MODE, self.E_ENERGY_PATTERN: self.SAFE_ENERGY_PATTERN}
        if action == CHARGE:
            return ({**off, self.E_GRID_CHARGE: 'on', self.E_GRID_CHARGE_A: float(self.CHARGE_A), pv: self.CHARGE_V, f'program_{active}_power': self.PROGRAM_W}, None)
        if action == self.HOLD:
            return ({**off, self.E_GRID_CHARGE: 'off', self.E_GRID_CHARGE_A: float(self.IDLE_A), pv: self.CHARGE_V, f'program_{active}_power': self.PROGRAM_W}, None)
        if action == self.IDLE:
            return ({**off, self.E_GRID_CHARGE: 'off', self.E_GRID_CHARGE_A: float(self.IDLE_A), pv: self.IDLE_V, f'program_{active}_power': self.PROGRAM_W}, None)
        if action == EXPORT:
            return ({self.E_GRID_CHARGE: 'off', self.E_GRID_CHARGE_A: float(self.IDLE_A), self.E_EXPORT_W: float(self.EXPORT_W), self.E_GRID_EXPORT_W: float(self.EXPORT_W), self.E_ENERGY_PATTERN: self.SAFE_ENERGY_PATTERN, self.E_WORK_MODE: self.EXPORT_WORK_MODE, self.E_SOLAR_SELL: 'on'}, None)
        return (None, f'unknown action {action}')

    def active_program(self, s):
        mins = self.now().hour * 60 + self.now().minute
        progs = []
        for n in range(1, self.N_PROGRAMS + 1):
            t = s['programs'][n]['time']
            try:
                h, m = str(t).split(':')[:2]
                progs.append((n, int(h) * 60 + int(m)))
            except (ValueError, AttributeError):
                return None
        for i, (n, t) in enumerate(progs):
            nxt = progs[(i + 1) % self.N_PROGRAMS][1]
            if t <= mins < nxt if t < nxt else mins >= t or mins < nxt:
                return n
        return None
    SNAP_FIELD = {E_SOLAR_SELL: lambda s: 'on' if s['solar_sell_on'] else 'off', E_WORK_MODE: lambda s: s['work_mode'], E_ENERGY_PATTERN: lambda s: s['energy_pattern'], E_EXPORT_W: lambda s: s['export_w'], E_GRID_EXPORT_W: lambda s: s['grid_export_w']}

    def observed_vector(self, s, want):
        cur = {}
        for ent in want:
            if ent == self.E_GRID_CHARGE:
                cur[ent] = 'on' if s['grid_charge_on'] else 'off'
            elif ent == self.E_GRID_CHARGE_A:
                cur[ent] = s['grid_charge_a']
            elif ent in self.SNAP_FIELD:
                cur[ent] = self.SNAP_FIELD[ent](s)
            elif 'program_' in ent:
                n = int(ent.rsplit('_', 2)[-2])
                cur[ent] = s['programs'][n][ent.rsplit('_', 1)[-1]]
            else:
                raise ConvergeError(f'observed_vector has no mapping for {ent} ; refusing to report it as absent, which would rewrite it on every tick')
        return cur

    def diff_vector(self, want, have):
        out = []
        for ent, w in want.items():
            h = have.get(ent)
            same = str(h) == str(w) if isinstance(w, str) else h is not None and abs(float(h) - float(w)) < 0.05
            if not same:
                out.append((ent, h, w))
        return out
    SETTLE_S = 2.0

    def _same(self, a, b):
        if isinstance(b, str):
            return str(a) == str(b)
        try:
            return abs(float(a) - float(b)) < 0.05
        except (TypeError, ValueError):
            return str(a) == str(b)

    def _write_rank(self, ent, old, new):
        if ent == self.E_GRID_CHARGE:
            return 0 if str(new) != 'on' else 4
        if ent == self.E_GRID_CHARGE_A:
            try:
                return 1 if float(new) < float(old) else 3
            except (TypeError, ValueError):
                return 3
        if ent == self.E_SOLAR_SELL:
            return -1 if str(new) != 'on' else 5
        return 2
    READBACK_RETRIES = 2
    READBACK_RETRY_S = 2.0

    def _retryable_readback(self, ent):
        return ent in (self.E_GRID_CHARGE, self.E_SOLAR_SELL)

    def _reread_after_mismatch(self, ent, want, first):
        if not self._retryable_readback(ent):
            return first
        back = first
        for attempt in range(1, self.READBACK_RETRIES + 1):
            self.transport.sleep(self.READBACK_RETRY_S)
            back = self.read_one(ent)
            if self._same(back, want):
                self.log(f'    readback OK {ent} = {back!r} on re-read {attempt}/{self.READBACK_RETRIES} ; the device was slow to report, not refusing')
                return back
            self.log(f'    readback still {back!r} on re-read {attempt}/{self.READBACK_RETRIES}, wanted {want!r}')
        return back

    def converge(self, s, want, have, action):
        delta = self.diff_vector(want, have)
        if not delta:
            return (True, 'already converged ; nothing to write')
        ordered = sorted(delta, key=lambda d: self._write_rank(*d))
        written = []
        for ent, old, new in ordered:
            try:
                self.log(f'  WRITE {ent}: {old!r} -> {new!r}')
                self.journal({'phase': 'WRITE_PREPARED', 'action': action, 'entity': ent, 'old': repr(old), 'value': new, 'clean': False})
                written.append((ent, old, new))
                self.set_entity(ent, new)
                self.transport.sleep(self.SETTLE_S)
                back = self.read_one(ent)
                if not self._same(back, new):
                    back = self._reread_after_mismatch(ent, new, back)
                if not self._same(back, new):
                    raise ConvergeError(f'readback {ent}: wanted {new!r}, got {back!r} (confirmed over {self.READBACK_RETRIES} re-read(s))' if self._retryable_readback(ent) else f'readback {ent}: wanted {new!r}, got {back!r}')
                self.log(f'    readback OK {ent} = {back!r}')
                self.journal({'phase': 'WRITE_READBACK_VERIFIED', 'entity': ent, 'value': new})
            except (Exception, SystemExit) as exc:
                return self._restore(written, type(exc).__name__)
        return (True, f'converged {len(written)} field(s) for {action}')

    def _restore(self, written, why):
        latched = self.latch_stop(f'CONVERGE FAILED; recovery DIRTY ; {why}')
        self._minimal_stop(f'convergence failure: {why}; durable_latch={latched}', written)
        return (False, 'DIRTY: minimal stop requested; no active baseline restored')
    _CMP = re.compile('([\\d.]+)c\\s*(<|>=)\\s*(?:marginal\\s*)?([\\d.]+)c(?:\\s*−\\s*([\\d.]+)c)?')

    def reason_selfcheck(self, reason):
        m = self._CMP.search(reason or '')
        if not m:
            return None
        lhs, op, rhs, margin = m.groups()
        try:
            lhs, rhs = (float(lhs), float(rhs))
            rhs -= float(margin) if margin else 0.0
        except (TypeError, ValueError):
            return None
        holds = lhs < rhs if op == '<' else lhs >= rhs
        if holds:
            return None
        return f'REASON ARITHMETIC FALSE: stated {lhs}c {op} {rhs}c'

    def classify_tick(self, want, have, fresh):
        delta = self.diff_vector(want, have)
        delta_fresh = self.diff_vector(want, fresh)
        raced = any((not self._same(fresh.get(e), have.get(e)) for e in want))
        return {'delta': delta, 'delta_fresh': delta_fresh, 'raced': raced, 'clean': not delta and (not delta_fresh)}

    def observed_now(self, want):
        return {ent: self.read_one(ent) for ent in want}

    def _minimal_stop(self, reason, attempted=()):
        targets = [(self.E_SOLAR_SELL, 'off'), (self.E_GRID_CHARGE, 'off'), (self.E_WORK_MODE, self.SAFE_WORK_MODE), (self.E_ENERGY_PATTERN, self.SAFE_ENERGY_PATTERN)]
        outcomes = []
        try:
            self.journal({'phase': 'STOP_REQUESTED', 'reason': reason, 'clean': False, 'attempted_fields': [e for e, _old, _new in attempted]})
            for ent, value in targets:
                self.journal({'phase': 'STOP_PREPARED', 'entity': ent, 'value': value, 'clean': False})
                row = {'entity': ent, 'value': value, 'transport_returned': False}
                outcomes.append(row)
                try:
                    self.set_entity(ent, value)
                    row['transport_returned'] = True
                except (Exception, SystemExit) as exc:
                    row['transport_error'] = type(exc).__name__
            self.transport.sleep(self.SETTLE_S)
            for row in outcomes:
                try:
                    back = self.read_one(row['entity'])
                    row['observed'] = repr(back)
                    row['readback_matches'] = self._same(back, row['value'])
                except (Exception, SystemExit) as exc:
                    row['readback_matches'] = False
                    row['readback_error'] = type(exc).__name__
            verified = all((r['transport_returned'] and r['readback_matches'] for r in outcomes))
            self.journal({'phase': 'STOP_RESULT', 'reason': reason, 'fields': outcomes, 'minimal_readback_verified': verified, 'clean': False, 'physical_completion_proven': False, 'unresolved_fields': [e for e, _old, _new in attempted]})
            self.persist({'at': self.now().isoformat(timespec='seconds'), 'action': self.IDLE, 'reason': reason, 'converged': False, 'note': 'minimal STOP; completion unproven', 'minimal_readback_verified': verified, 'physical_completion_proven': False, 'shadow': False})
        except (Exception, SystemExit) as exc:
            self.log(f'STOP INCOMPLETE: durable record/diagnostic failure {type(exc).__name__}')
            return False
        if not verified:
            self.latch_stop('STOP RESULT DIRTY: one or more minimal requests unverified')
        self.log(f'STOP RESULT: minimal_readback_verified={verified}; physical completion unproven')
        return verified

    def tick(self, live: bool):
        if live and self.storage.latch is not None:
            self._minimal_stop('controller-local STOP latch present; context not read')
            return
        try:
            s = self.snapshot()
        except (Exception, SystemExit) as exc:
            if not live:
                raise
            self._minimal_stop(f'snapshot unavailable: {type(exc).__name__}')
            return
        if live and s.get('stop'):
            self._minimal_stop('owner emergency stop; TOU/price diagnostics bypassed')
            return
        bad = self.validity(s)
        if bad:
            action, reason, veto = (self.IDLE, '; '.join(bad), [])
        else:
            try:
                k, floor_src = self.contract_for_today(self.DAY_PLAN)
                action, reason = self.decide(s, k)
            except (Exception, SystemExit) as exc:
                if not live:
                    raise
                self._minimal_stop(f'decision context unavailable: {type(exc).__name__}')
                return
            reason = f'{reason} [floor: {floor_src}]'
            veto = self.action_gate(s, action)
            if veto:
                action, reason = (self.IDLE, f"vetoed: {'; '.join(veto)} (was {action}: {reason})")
        if live and (bad or veto):
            self._minimal_stop(reason)
            return
        want, why_not = self.target_vector(action, s)
        if want is None:
            if live:
                self._minimal_stop(f'no complete target: {why_not}')
                return
            self.journal({'phase': 'NO_VECTOR', 'action': action, 'reason': reason, 'detail': why_not, 'shadow': not live, 'soc': s['soc']})
            self.log(f"[{('LIVE' if live else 'SHADOW')}] {action} :: {reason} :: {why_not}")
            return
        have = self.observed_vector(s, want)
        delta = self.diff_vector(want, have)
        rec = {'phase': 'SHADOW' if not live else 'CONVERGE', 'action': action, 'reason': reason, 'veto': veto, 'soc': s['soc'], 'pack_v': s['pack_v'], 'p1_w': s['p1_w'], 'active_program': self.active_program(s), 'want': want, 'have': have, 'delta': [{'entity': e, 'from': h, 'to': w} for e, h, w in delta], 'ages': s['ages'], 'shadow': not live}
        try:
            fresh = self.observed_now(want)
        except (Exception, SystemExit) as exc:
            if not live:
                raise
            self._minimal_stop(f'diagnostic unavailable: {type(exc).__name__}')
            return
        c = self.classify_tick(want, have, fresh)
        delta_fresh, raced, clean = (c['delta_fresh'], c['raced'], c['clean'])
        rec.update({'fresh': fresh, 'delta_fresh': [{'entity': e, 'from': h, 'to': w} for e, h, w in delta_fresh], 'raced': raced, 'clean': clean})
        bad_reason = self.reason_selfcheck(reason)
        if bad_reason:
            rec['reason_selfcheck'] = bad_reason
            self.log(f'  {bad_reason} :: {reason}')
        if not live:
            self.journal(rec)
            self.persist({'at': s['at'], 'action': action, 'reason': reason, 'would_write': len(delta_fresh), 'clean': clean, 'raced': raced, 'shadow': True})
            tag = 'clean' if clean else 'RACED' if raced and (not delta) else 'DIVERGENT'
            self.log(f'[SHADOW] {action} :: {reason} :: {tag}, would write {len(delta_fresh)} field(s)' + (f' {[e for e, _, _ in delta_fresh]}' if delta_fresh else ''))
            return
        ok, note = self.converge(s, want, fresh, action)
        rec['converged'], rec['converge_note'] = (ok, note)
        rec['phase_observed'] = 'UNVERIFIED_STOP' if not ok else 'CHARGE_ACTIVE' if s['grid_charge_on'] else 'ARMING' if action == CHARGE else 'SAFE_IDLE'
        self.journal(rec)
        self.persist({'at': s['at'], 'action': action, 'reason': reason, 'converged': ok, 'note': note, 'shadow': False,
                      'verified_target': want if ok else None, 'active_program': self.active_program(s)})
        self.log(f'[LIVE] {action} :: {reason} :: {note}')
