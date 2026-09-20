"""Production recovery classification. Clearing a latch remains an explicit action."""
from datetime import datetime, timedelta
CLEAN_SIG = 'converge failed, baseline fully restored and verified'
DIRTY_SIG = 'CONVERGE FAILED AND RESTORE INCOMPLETE'
DIRTY_SIG_V2 = 'CONVERGE FAILED; recovery DIRTY'
UNVERIFIED_SIG = 'STOP RESULT DIRTY: one or more minimal requests unverified'
E_STOP = 'stop'
COOLDOWN_S = 120
DAILY_BUDGET = 4
BACKOFF_S = (0, 300, 900, 1800)

def classify(why):
    if not isinstance(why, str) or not why.strip():
        return 'UNKNOWN'
    if DIRTY_SIG in why or DIRTY_SIG_V2 in why:
        return 'DIRTY'
    if UNVERIFIED_SIG in why:
        return 'UNVERIFIED'
    if CLEAN_SIG in why:
        return 'CLEAN'
    return 'UNKNOWN'

def latch_age_s(latch, now=None):
    now = now or datetime.now()
    try:
        return (now - datetime.fromisoformat(latch['at'])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None

def evaluate(latch, estop_on, reachable, reach_detail, window_kind, at_stake, state, now=None, premise_cleared=False, premise_detail='not probed'):
    now = now or datetime.now()
    if latch is None:
        return (False, 'no latch ; nothing to heal', None)
    kind = classify(latch.get('why'))
    if kind == 'UNVERIFIED' and (not premise_cleared):
        return (False, f'latch premise still stands - the four minimal safety fields are not proven safe: {premise_detail}', None)
    if kind == 'DIRTY':
        return (False, 'latch is DIRTY (restore incomplete) ; plant state is unknown, this is a human decision and always will be', None)
    if kind == 'UNKNOWN':
        return (False, 'latch reason not recognised', f"EMS SELF-HEAL IS INERT: a STOP latch appeared whose reason this cannot classify, so it was left alone. Self-healing is NOT protecting you until someone looks. Reason, verbatim: {latch.get('why')!r}")
    if estop_on:
        return (False, f'{E_STOP} is ON ; the owner has stopped the plant', None)
    age = latch_age_s(latch, now)
    if age is None:
        return (False, 'latch has no usable timestamp', 'EMS SELF-HEAL: a CLEAN latch had no readable timestamp, so the cooldown could not be evaluated and it was left alone.')
    if age < COOLDOWN_S:
        return (False, f'cooldown ; latched {age:.0f}s ago, need {COOLDOWN_S}s', None)
    if not window_kind:
        return (False, 'outside any planned money window ; nothing is being lost', None)
    if not at_stake:
        detail = 'at the export floor ; nothing left to sell' if window_kind == 'export' else 'already at or above the charge ceiling ; nothing left to buy'
        return (False, f'{detail} ; nothing to gain', None)
    heals = int(state.get('heals', 0))
    if heals >= DAILY_BUDGET:
        return (False, f'daily budget spent ({heals}/{DAILY_BUDGET})', f'EMS HAS STOPPED ITSELF {heals} TIMES TODAY and the self-heal budget is spent. It will NOT restart on its own. This is no longer a blip ; the inverter or its link needs looking at.')
    need = BACKOFF_S[min(heals, len(BACKOFF_S) - 1)]
    if need and state.get('last_heal_at'):
        try:
            since = (now - datetime.fromisoformat(state['last_heal_at'])).total_seconds()
        except (TypeError, ValueError):
            since = need
        if since < need:
            return (False, f'backoff ; heal {heals + 1} needs {need:.0f}s since the last, {since:.0f}s so far', None)
    if not reachable:
        return (False, f'inverter not proven reachable: {reach_detail}', None)
    stake = {'export': 'something still to sell', 'charge': 'something still to buy', 'arm': 'the nightly arm still to run'}.get(window_kind, 'something at stake')
    return (True, f'{kind} latch {age:.0f}s old, inverter proven reachable ({reach_detail}), inside the {window_kind} window with {stake}, heal {heals + 1}/{DAILY_BUDGET}' + (f'; premise disproven: {premise_detail}' if kind == 'UNVERIFIED' else ''), None)
ARM_AT_HHMM = (23, 15)
ARM_GUARD_MIN = 30

def arm_guard(now=None):
    now = now or datetime.now()
    arm = now.replace(hour=ARM_AT_HHMM[0], minute=ARM_AT_HHMM[1], second=0, microsecond=0)
    return 'arm' if arm - timedelta(minutes=ARM_GUARD_MIN) <= now < arm else None
