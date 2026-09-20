"""Local dated planning jobs and an explicitly labelled first-install estimate."""
from datetime import timedelta
import math

from .daily import build_day, measure_draws, repin_draws


def reserve_inputs(saved, at, target, contract):
    """Use admitted observations, otherwise a declared household-load assumption."""
    try:
        measured=measure_draws(saved.get('history',[]),at,target,
                               (saved.get('draws') or {}).get('reserve',{}).get('pct'))
        measured,_,_=repin_draws(measured)
        saved['draws']=measured
        return measured
    except ValueError:
        points=contract.baseline_load_kw*contract.overnight_reference_hours/contract.cap_kwh*100
        return {'for_date':target.isoformat(),'reserve':{
                    'pct':max(contract.floor_min_pct,min(70,math.ceil(contract.never_empty_pct+points*1.5))),
                    'p50':points,'p90':points*1.5,'n':0},
                'basis':'configured_household_load_assumption','measured':False}


def update_schedule(storage, at, prices, tomorrow, soc, contract):
    """Produce current and scheduled plans; hardware application is a separate step.

    Jobs have five-minute delivery windows. A late startup builds today's plan
    from the remaining intervals and records missed scheduled work explicitly.
    """
    saved=storage.data
    schedules=saved.setdefault('schedule',{})
    key=at.date().isoformat()
    day_plans=saved.setdefault('plans',{})
    if key not in day_plans:
        data=reserve_inputs(saved,at,at.date(),contract)
        plan=build_day(prices,soc,at,at.date(),data,contract)
        plan['reserve_basis']=data.get('basis','measured_overnight_history')
        storage.save_plan(plan)
        schedules[key+':initial']={'status':'planned','at':at.isoformat()}
    for job,hour,minute in (('repin',10,30),('arm',23,15)):
        job_key=key+':'+job
        elapsed=(at-at.replace(hour=hour,minute=minute,second=0,microsecond=0)).total_seconds()
        if elapsed<0 or job_key in schedules:continue
        if elapsed>=300:
            schedules[job_key]={'status':'missed_window','at':at.isoformat()}
            continue
        target=at.date()+timedelta(days=1) if job=='arm' else at.date()
        curve=tomorrow if job=='arm' else prices
        if curve is None:
            # Retry in this delivery window; never fabricate tomorrow's tariff.
            saved['schedule_pending']={'job':job,'reason':'published_tomorrow_prices_required'}
            continue
        data=reserve_inputs(saved,at,target,contract)
        plan=build_day(curve,soc,at,target,data,contract)
        plan['reserve_basis']=data.get('basis','measured_overnight_history')
        storage.save_plan(plan)
        schedules[job_key]={'status':'planned','at':at.isoformat(),'for_date':target.isoformat()}
        saved.pop('schedule_pending',None)
    saved['schedule']=dict(sorted(schedules.items())[-60:])
    return saved['plans'][key]
