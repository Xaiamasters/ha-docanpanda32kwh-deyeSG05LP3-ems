"""Production overnight draw admission and reserve calculation."""
from datetime import datetime, timedelta
from .planner import percentile, nightly_reserve
SLOPE_EXPORT = 12.0
EVENING_SCAN = (17, 23)

def _at(pts, when):
    best = None
    for t, v in pts:
        if t <= when:
            best = v
        else:
            break
    return best

def _transitions(pts, to_state: str):
    out = []
    prev = None
    for t, s in pts:
        if s in ('unavailable', 'unknown', 'none', 'None'):
            continue
        if prev is not None and prev != to_state and (s == to_state):
            out.append(t)
        prev = s
    return out

def export_close(soc, day, slope: float=0.0):
    slope = slope or SLOPE_EXPORT
    t0 = datetime.combine(day, datetime.min.time()) + timedelta(hours=EVENING_SCAN[0])
    t1 = datetime.combine(day, datetime.min.time()) + timedelta(hours=EVENING_SCAN[1])
    pts = [(t, v) for t, v in soc if t0 <= t <= t1]
    for a, b in reversed(list(zip(pts, pts[1:]))):
        dt_h = (b[0] - a[0]).total_seconds() / 3600.0
        if dt_h <= 0 or dt_h > 1.0:
            continue
        if (a[1] - b[1]) / dt_h >= slope:
            return (b[0], f'slope end {b[0]:%H:%M}')
    return (t0 + timedelta(hours=4), f'{EVENING_SCAN[0] + 4}:00 (no discharge seen)')
ADMIT_HOURS_LO, ADMIT_HOURS_HI = (4.0, 30.0)
ADMIT_MIN_SAMPLES = 8
ADMIT_MIN_SAMPLES_PER_H = 1.0
ADMIT_CONSISTENCY_PTS = 0.55
WINSOR_PCT = 95.0
FLOOR_MAX_MOVE_PTS = 3.0

def classify_night(n, slope_pts_h):
    try:
        hours = float(n.get('hours') or 0.0)
        draw = float(n['draw_trough'])
        close = float(n['soc_close'])
        low = float(n['soc_low'])
        opened = float(n['soc_open'])
        samples = int(n.get('samples') or 0)
    except (KeyError, TypeError, ValueError) as exc:
        return f'unreadable record ({exc})'
    if not ADMIT_HOURS_LO <= hours <= ADMIT_HOURS_HI:
        return f'window {hours:.1f} h outside [{ADMIT_HOURS_LO:.0f}, {ADMIT_HOURS_HI:.0f}] ; window detection failed; this is not one night'
    if samples < ADMIT_MIN_SAMPLES or samples / hours < ADMIT_MIN_SAMPLES_PER_H:
        return f'{samples} samples across {hours:.1f} h ; a trough you did not observe is not a trough'
    if not all((0.0 <= v <= 100.0 for v in (close, low, opened))):
        return f'SoC outside [0, 100] (close {close:.0f}, low {low:.0f}, open {opened:.0f}) ; sensor fault'
    if low > close or low > opened:
        return f'the trough {low:.0f} is not the minimum (close {close:.0f}, open {opened:.0f}) ; a reading, not a discharge'
    if abs(close - low - draw) > ADMIT_CONSISTENCY_PTS:
        return f'draw {draw:.1f} disagrees with close-low {close - low:.1f} ; the record contradicts itself'
    if draw / hours > slope_pts_h:
        return f'mean {draw / hours:.2f} pts/h exceeds the {slope_pts_h:.1f} pts/h export slope ; the house cannot draw at export rate for a whole night'
    return None

def winsorise(xs, pct=WINSOR_PCT):
    xs = [float(x) for x in xs]
    if len(xs) < 3:
        return xs
    cap = percentile(sorted(xs), pct)
    return [min(x, cap) for x in xs]

def cap_move(res, prev_pct, limit=FLOOR_MAX_MOVE_PTS):
    if prev_pct is None:
        return (res, None)
    raw = int(res['pct'])
    capped = int(max(prev_pct - limit, min(prev_pct + limit, raw)))
    if capped == raw:
        return (res, None)
    out = dict(res)
    out['pct'] = capped
    out['binding'] = f"{res['binding']} ; MOVE CAPPED {raw} -> {capped} (previous {prev_pct}, limit +/-{limit:.0f}/night)"
    return (out, {'raw_pct': raw, 'previous_pct': prev_pct, 'capped_pct': capped})
