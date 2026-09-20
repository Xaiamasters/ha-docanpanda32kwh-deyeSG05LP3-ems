"""Dated daily plans, conditional ceilings and reserve admission."""
from __future__ import annotations
from dataclasses import replace
from datetime import date, datetime, timedelta
import copy
import math

from . import arming, programs, reserve
from .context import dated_contract
from .planner import CHARGE, EXPORT, Contract, plan_day, nightly_reserve, percentile
from .repin import reserve_verdict, without_pinnable_floor


def build_day(prices, soc, at: datetime, target: date, draws, base=None):
    """Return the production plan and proposed firmware block, without applying it."""
    expected = len(prices) if hasattr(prices,'contract') else 96
    if len(prices) != expected or any(isinstance(p, bool) or not math.isfinite(p) for p in prices):
        raise ValueError('production_policy_requires_96_published_prices')
    if not math.isfinite(soc) or not 0 <= soc <= 100:
        raise ValueError('invalid_soc')
    if target not in (at.date(), at.date()+timedelta(days=1)):
        raise ValueError('invalid_plan_day')
    contract, source = dated_contract(draws, target.isoformat(), base)
    if hasattr(prices,'contract'):contract=prices.contract(contract)
    stats = (draws or {}).get('reserve', {})
    try:
        p50, p90 = float(stats['p50']), float(stats['p90'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('overnight_draw_history_required') from None
    if not all(math.isfinite(x) for x in (p50,p90)):
        raise ValueError('invalid_overnight_draws')
    tomorrow = target != at.date()
    opening, _, basis = arming.forecast_morning_soc(soc,p50,p90) if tomorrow else (soc,soc,'current battery state of charge')
    arming.opening_guard(opening,soc,p50,p90,tomorrow)
    # An installed profile may lower the maximum below the original 90/95 policy.
    contract=replace(contract,chg_soc_target=min(contract.chg_soc_target,contract.maximum_soc))
    now_slot=(min(len(prices),prices.slot_at(at)+1) if hasattr(prices,'slot_at') else (at.hour*60+at.minute+14)//15) if contract.household_profile and not tomorrow else 0
    plan = plan_day(prices,opening,contract,now_slot=now_slot)
    if contract.maximum_soc < arming.CEILING_TRIAL:
        ceiling,reason=contract.chg_soc_target,'Configured maximum limits the charge target.'
    else:
        trial = plan_day(prices,opening,replace(contract,chg_soc_target=arming.CEILING_TRIAL),now_slot=now_slot)
        ceiling, reason = arming.choose_ceiling(prices,opening,contract,plan,trial)
        if ceiling == arming.CEILING_TRIAL:
            plan,contract = trial,replace(contract,chg_soc_target=ceiling)
    start,end,window_reason = arming.stretch_to_armed_window(plan,prices,contract)
    programs.validate(plan,contract,charge_only=True)
    desired = programs.plan_to_programs(plan,contract,charge_only=True,prices=prices)
    payload = {'date':target.isoformat(),'for_date':target.isoformat(),'pinned_at':at.isoformat(),
               'soc0':opening,'soc_at_plan':opening,'soc_basis':basis,'ceiling_pct':ceiling,
               'ceiling_reason':reason,'reserve_source':source,
               'charge_window_hhmm':[programs.slot_to_hhmm(x,prices) if x is not None else None for x in (start,end)],
               'armed_window_reason':window_reason,
               'periods':[{'i':p.index,'start':programs.slot_to_hhmm(p.start_slot,prices)[:5],'end':programs.slot_to_hhmm(p.end_slot,prices)[:5],'action':p.action,'soc_target':p.soc_target} for p in plan.periods],
               'export_clusters':[[p.start_slot,p.end_slot,int(max(plan.floors.get(p.end_slot,contract.reserve_floor_pct),contract.never_empty_pct))] for p in plan.periods if p.action==EXPORT],
               'charge_window':arming.charge_window(plan),
               'reserve':copy.deepcopy(stats) if (draws or {}).get('for_date')==target.isoformat() else {},
               'warnings':list(plan.warnings), 'programs':desired}
    arming.schema_v2_extend(payload,prices,contract)
    if hasattr(prices,'starts'):
        payload.update(slot_count=len(prices),interval_starts=[x.isoformat() for x in prices.starts],
                       interval_end=prices.end.isoformat(),transition_day=prices.transition)
    return payload


def measure_draws(history, at: datetime, target: date, previous=None, days=12):
    """Apply the captured slope/trough measurement to explicit local history.

    Each row has local `at`, `soc`, `solar_sell_on` and `grid_charge_on`.
    Callers must retain unavailability gaps and use the installation timezone.
    """
    end=at.replace(tzinfo=None)
    start=end-timedelta(days=days)
    soc=[]; exp=[]; chg=[]
    for row in history:
        stamp=datetime.fromisoformat(row['at'])
        if stamp.tzinfo is not None and at.tzinfo is not None:
            stamp=stamp.astimezone(at.tzinfo)
        stamp=stamp.replace(tzinfo=None)
        if not start<=stamp<=end:continue
        value=row.get('soc')
        if isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value) and 0<=value<=100:soc.append((stamp,value))
        for key,output in (('solar_sell_on',exp),('grid_charge_on',chg)):
            value=row.get(key)
            output.append((stamp,'on' if value is True else 'off' if value is False else 'unavailable'))
    soc.sort();exp.sort();chg.sort()
    if not soc:raise ValueError('no_soc_history')
    closes=reserve._transitions(exp,'off'); charge_ons=reserve._transitions(chg,'on')
    nights=[];day=(soc[0][0]+timedelta(days=1)).date()
    while day<end.date():
        close,close_source=reserve.export_close(soc,day)
        evening=datetime.combine(day,datetime.min.time())+timedelta(hours=17)
        switches=[t for t in closes if evening<=t<evening+timedelta(hours=7)]
        delta=round((close-max(switches)).total_seconds()/60) if switches else None
        morning=datetime.combine(day+timedelta(days=1),datetime.min.time())
        following=[t for t in charge_ons if close<t<morning+timedelta(hours=14)]
        opening,opening_source=(min(following),'charge on') if following else (min(morning+timedelta(hours=11),end),'11:00 (no charge)')
        initial=reserve._at(soc,close)
        window=[(t,v) for t,v in soc if close<=t<=opening]
        if initial is not None and window and opening>close:
            low_at,low=min(window,key=lambda tv:(tv[1],tv[0]));last=window[-1][1]
            nights.append({'night':str(day),'close':close.strftime('%H:%M'),'close_src':close_source,
                           'open':opening.strftime('%d %H:%M'),'open_src':opening_source,
                           'hours':round((opening-close).total_seconds()/3600,1),
                           'soc_close':initial,'soc_low':low,'low_at':low_at.strftime('%H:%M'),
                           'soc_open':last,'draw_trough':round(initial-low,1),'draw_endpoint':round(initial-last,1),
                           'samples':len(window),'switch_delta_min':delta,
                           'clock_transition':bool(at.tzinfo is not None and
                               close.replace(tzinfo=at.tzinfo).utcoffset()!=opening.replace(tzinfo=at.tzinfo).utcoffset())})
        day+=timedelta(days=1)
    for row in nights:
        # The captured estimator uses wall-clock slopes and durations. Exclude
        # transition nights instead of treating a repeated/skipped hour as data.
        row['refused_reason']='clock_transition_night' if row['clock_transition'] else reserve.classify_night(row,reserve.SLOPE_EXPORT)
        row['admitted']=row['refused_reason'] is None
    admitted=[r for r in nights if r['admitted']]; refused=[r for r in nights if not r['admitted']]
    draws=[r['draw_trough'] for r in admitted]
    if not draws:raise ValueError('no_measurable_nights')
    kept=reserve.winsorise(draws)
    result,note=reserve.cap_move(nightly_reserve(kept),previous)
    heavy=percentile(sorted(draws),95) if len(draws)>=3 else max(draws)
    return {'measured_at':at.isoformat(),'for_date':target.isoformat(),'nights':nights,'draws':draws,'reserve':result,
            'admission':{'admitted_nights':len(admitted),'refused':[{'night':r['night'],'reason':r['refused_reason']} for r in refused],
                         'winsor_pct':reserve.WINSOR_PCT,'winsorised_from':[d for d,k in zip(draws,kept) if d!=k],
                         'p95_draw':round(heavy,1),'previous_pct':previous,'move_cap_pts':reserve.FLOOR_MAX_MOVE_PTS,
                         'move_capped':note,'newest_night_is_heavy':bool(draws and draws[-1]>=heavy)}}


def repin_draws(draws):
    verdict,reason=reserve_verdict(draws)
    if verdict=='REFUSE_DAY':raise ValueError('overnight_measurement_not_admitted')
    return (without_pinnable_floor(draws) if verdict=='REFUSE_NUMBER' else draws),verdict,reason
