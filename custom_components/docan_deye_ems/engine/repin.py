"""Production morning reserve admission policy."""
MIN_ADMITTED_NIGHTS = 5
FLOOR_LO, FLOOR_HI = (40.0, 70.0)

def reserve_verdict(artefact, floor_lo=FLOOR_LO, floor_hi=FLOOR_HI, min_nights=MIN_ADMITTED_NIGHTS):
    if not (artefact.get('nights') or []):
        return ('REFUSE_DAY', 'artefact has no nightly draws')
    adm = artefact.get('admission') or {}
    if 'admitted_nights' not in adm:
        return ('REFUSE_DAY', 'artefact predates the admission block - the measure and the guard are out of step')
    n_ok = int(adm.get('admitted_nights') or 0)
    if n_ok < min_nights:
        return ('REFUSE_DAY', f'only {n_ok} admitted night(s) < {min_nights} - too few to size a floor from')
    r = artefact.get('reserve') or {}
    try:
        pct = float(r['pct'])
    except (KeyError, TypeError, ValueError):
        return ('REFUSE_NUMBER', 'artefact carries no usable reserve.pct')
    if not floor_lo <= pct <= floor_hi:
        return ('REFUSE_NUMBER', f'computed floor {pct:.0f}% outside [{floor_lo:.0f},{floor_hi:.0f}]')
    return ('USE', f'floor {pct:.0f}% inside [{floor_lo:.0f},{floor_hi:.0f}]')

def without_pinnable_floor(artefact):
    out = dict(artefact)
    r = dict(out.get('reserve') or {})
    if 'pct' in r:
        r['pct_refused_out_of_band'] = r.pop('pct')
    out['reserve'] = r
    return out
