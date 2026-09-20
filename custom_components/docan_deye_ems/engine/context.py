"""Explicit clock, plan, transport and persistence for the controller policy."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math

from .planner import Contract, SLOTS_PER_DAY


def pinned_exports(plan: dict | None, day: str):
    if not isinstance(plan, dict):
        return None, 'no readable day plan'
    if str(plan.get('for_date')) != day:
        return None, f"day plan is for {plan.get('for_date')}, not {day}"
    rows = []
    for cluster in plan.get('export_clusters') or []:
        try:
            start, end, floor = int(cluster[0]), int(cluster[1]), int(cluster[2])
        except (TypeError, ValueError, IndexError):
            return None, 'day plan has a malformed export cluster'
        if not 0 <= start < end <= SLOTS_PER_DAY:
            return None, 'day plan cluster is out of range'
        rows.append((start, end, floor))
    if any(a[1] > b[0] for a, b in zip(rows, rows[1:])):
        return None, 'day plan clusters overlap'
    return rows, f"pinned {len(rows)} export cluster(s) from {plan.get('pinned_at', '?')}"


def dated_contract(plan: dict | None, day: str, base: Contract | None = None):
    contract = base or Contract()
    if not isinstance(plan, dict) or str(plan.get('for_date')) != day:
        return contract, f'pinned {contract.floor_min_pct}% reserve fallback'
    try:
        floor = int(plan['reserve']['pct'])
    except (KeyError, TypeError, ValueError):
        return contract, f'pinned {contract.floor_min_pct}%: no reserve.pct'
    return replace(contract, nightly_reserve_pct=floor), f'measured overnight reserve {floor}%'


class PolicyContext:
    """Transport methods run on one worker under the runtime's exclusive lock.

    The transport provides snapshot(), read(field), write(field, value) and
    sleep(seconds). Storage must durably record intent before write() is called.
    No source filename, endpoint, entity ID or credential is known to this module.
    """

    def __init__(self, at: datetime, transport, storage, day_plan=None,
                 contract: Contract | None = None, limits=None, *, allow_writes=False):
        self.at = at
        self.transport = transport
        self.storage = storage
        self.DAY_PLAN = day_plan
        self.base_contract = contract or Contract()
        self.EXPORT_W = self.base_contract.export_power_w
        self.allow_writes = allow_writes
        for key, value in (limits or {}).items():
            if key not in {'CHARGE_A', 'IDLE_A', 'EXPORT_W', 'CHARGE_V', 'IDLE_V',
                           'HARD_V', 'PACK_LOW_V', 'NEVER_EMPTY', 'SOC_MAX'}:
                raise ValueError('unsupported_controller_limit')
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError('invalid_controller_limit')
            setattr(self, key, float(value))

    def now(self):
        return self.at

    def snapshot(self):
        return self.transport.snapshot()

    def plan_ceiling(self):
        plan = self.DAY_PLAN
        if not isinstance(plan, dict) or str(plan.get('for_date')) != self.at.date().isoformat():
            return 90.0, 'ceiling 90%: no current day plan'
        raw = plan.get('ceiling_pct')
        if raw is None:
            return 90.0, 'ceiling 90%: plan pins no ceiling'
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError('INVALID_CEILING_NUMBER') from None
        if isinstance(raw, bool) or not math.isfinite(value):
            raise ValueError('INVALID_CEILING_FINITE')
        if not 0 <= value <= 95:
            raise ValueError('INVALID_CEILING_RANGE')
        return value, f"ceiling {value:.0f}% (pinned {plan.get('pinned_at', '?')})"

    def pinned_export(self, plan):
        return pinned_exports(plan, self.at.date().isoformat())

    def contract_for_today(self, plan):
        return dated_contract(plan, self.at.date().isoformat(), self.base_contract)

    def set_entity(self, field, value):
        if not self.allow_writes:
            raise PermissionError('equipment_writes_not_authorized')
        self.transport.write(field, value)

    def read_one(self, field):
        return self.transport.read(field)

    def journal(self, row):
        self.storage.journal({**row, 'ts': self.at.isoformat()})

    def persist(self, row):
        self.storage.persist(row)

    def latch_stop(self, reason):
        try:
            self.storage.set_latch({'at': self.at.isoformat(), 'why': reason})
        except OSError:
            return False
        return True

    def log(self, message):
        # Structured events carry the evidence; duplicate free-text logs are omitted.
        pass
