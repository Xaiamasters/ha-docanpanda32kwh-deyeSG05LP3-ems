"""Pure, provider-neutral observations and shadow estimates. No I/O or controls."""
from __future__ import annotations
from datetime import datetime,time,timedelta,timezone
import math
from zoneinfo import ZoneInfo

class InputError(ValueError):
    """A bounded public input error code, not raw upstream data."""

def number(value):
    if isinstance(value,bool) or value is None or value=='':raise InputError('invalid_number')
    try:n=float(value)
    except (TypeError,ValueError):raise InputError('invalid_number') from None
    if not math.isfinite(n):raise InputError('invalid_number')
    return n

def instant(value):
    try:d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError:raise InputError('invalid_timestamp') from None
    if d.tzinfo is None or d.utcoffset() is None:raise InputError('timezone_required')
    return d.astimezone(timezone.utc)

def normalize(value,unit,wanted,sign=1):
    conversions={'W':{'W':1,'kW':1000},'kWh':{'kWh':1,'Wh':0.001},'%':{'%':1},'V':{'V':1},'A':{'A':1},'°C':{'°C':1}}
    if unit not in conversions.get(wanted,{}):raise InputError('invalid_unit')
    n=number(value)*conversions[wanted][unit]*sign
    if wanted=='%' and not 0<=n<=100:raise InputError('invalid_soc')
    if wanted in ('V','kWh') and n<0:raise InputError('invalid_number')
    return n

def day_bounds(day,zone):
    tz=ZoneInfo(zone)
    start=datetime.combine(day,time.min,tzinfo=tz).astimezone(timezone.utc)
    end=datetime.combine(day+timedelta(days=1),time.min,tzinfo=tz).astimezone(timezone.utc)
    return start,end

def normalize_periods(rows,day,zone,*,currency='EUR',unit='EUR/kWh',basis='all_in',tax=0,fee=0,vat=0,export_fee=0):
    """Require a complete exact local delivery day, including 23/25-hour days."""
    begin,end=day_bounds(day,zone)
    if unit not in (currency+'/kWh',currency+'/MWh'):raise InputError('invalid_price_unit')
    scale=0.001 if unit.endswith('/MWh') else 1
    tax,fee,vat,export_fee=map(number,(tax,fee,vat,export_fee))
    if basis not in ('all_in','spot') or min(tax,fee,export_fee)<0 or not 0<=vat<=100:raise InputError('invalid_tariff')
    if basis=='all_in' and any((tax,fee,vat)):raise InputError('all_in_tax_twice')
    result=[]
    for row in rows:
        if not isinstance(row,dict) or not {'start','end','value'}<=row.keys():raise InputError('invalid_price_rows')
        a,b=instant(row['start']),instant(row['end'])
        if b<=begin or a>=end:continue
        if a<begin or b>end or b<=a or (b-a).total_seconds() not in (900,1800,3600):raise InputError('invalid_price_interval')
        raw=number(row['value'])*scale
        buy=(raw+tax+fee)*(1+vat/100) if basis=='spot' else raw
        # Export needs its own explicit price; an import tariff is not compensation.
        sell=number(row['export'])*scale-export_fee if row.get('export') is not None else None
        result.append({'start':a.isoformat(),'end':b.isoformat(),'import':round(buy,8),'export':None if sell is None else round(sell,8)})
    result.sort(key=lambda r:r['start'])
    if not result or instant(result[0]['start'])!=begin or instant(result[-1]['end'])!=end:raise InputError('incomplete_price_day')
    if any(a['end']!=b['start'] for a,b in zip(result,result[1:])):raise InputError('price_gap_or_overlap')
    return result

def observed_plan(state,attrs,now,zone,max_age=180):
    """Validate a controller's declared day, timestamp and bounded structured windows."""
    if state in ('unknown','unavailable',None):raise InputError('plan_unavailable')
    at=instant(attrs.get('updated_at'));age=(now.astimezone(timezone.utc)-at).total_seconds()
    if age<0 or age>max_age:raise InputError('stale_plan')
    if attrs.get('date')!=now.astimezone(ZoneInfo(zone)).date().isoformat():raise InputError('wrong_plan_day')
    rows=attrs.get('windows')
    if not isinstance(rows,list) or len(rows)>200:raise InputError('invalid_plan_windows')
    begin,end=day_bounds(now.astimezone(ZoneInfo(zone)).date(),zone)
    result=[]
    for row in rows:
        if not isinstance(row,dict):raise InputError('invalid_plan_windows')
        a,b=instant(row.get('start')),instant(row.get('end'));action=row.get('action')
        if not begin<=a<b<=end or action not in ('charge','export','idle'):raise InputError('invalid_plan_windows')
        result.append({'start':a.isoformat(),'end':b.isoformat(),'action':action,'source':'observed'})
    result.sort(key=lambda x:x['start'])
    if any(instant(a['end'])>instant(b['start']) for a,b in zip(result,result[1:])):raise InputError('overlapping_plan_windows')
    return {'status':str(state)[:80],'mode':'observed','windows':result,'updated_at':at.isoformat(),'physical_authority':False,'date':attrs['date']}

def shadow_plan(periods,soc,settings,now,zone):
    """Price-ranked charge estimate, bounded by household capacity/power/deadline.

    This is not a controller command or a promise of future SoC. Solar and future
    household demand are not forecast in this beta; the assumptions are surfaced.
    Export is deliberately not inferred from an import price or an unknown reserve.
    """
    capacity=number(settings['capacity_kwh']);target=number(settings['target_soc']);floor=number(settings['reserve_soc'])
    power=number(settings['charge_power_kw']);eff=number(settings['charge_efficiency'])/100
    soc=number(soc)
    if not 0<=floor<target<=100 or not 0<=soc<=100 or capacity<=0 or power<=0 or not 0<eff<=1:raise InputError('invalid_planning_limits')
    try:deadline_time=time.fromisoformat(settings['charge_deadline'])
    except (ValueError,TypeError):raise InputError('invalid_deadline') from None
    if deadline_time.tzinfo:raise InputError('invalid_deadline')
    local=now.astimezone(ZoneInfo(zone));deadline=datetime.combine(local.date(),deadline_time,tzinfo=local.tzinfo).astimezone(timezone.utc)
    now=now.astimezone(timezone.utc)
    need=max(0,(target-soc)/100*capacity);remaining=need;chosen=[]
    eligible=[]
    for p in periods:
        a=max(instant(p['start']),now);b=min(instant(p['end']),deadline)
        if a<b:eligible.append((number(p['import']),a,b))
    for price,a,b in sorted(eligible,key=lambda x:(x[0],x[1])):
        if remaining<=1e-8:break
        hours=min((b-a).total_seconds()/3600,remaining/(power*eff));energy=hours*power
        finish=a+timedelta(hours=hours)
        chosen.append({'start':a.isoformat(),'end':finish.isoformat(),'action':'charge','import_price':price,'grid_kwh':round(energy,5),'source':'shadow_estimate'})
        remaining-=energy*eff
    chosen.sort(key=lambda x:x['start'])
    reached=remaining<=1e-6
    return {'status':'target_already_met' if need==0 else 'estimate_ready' if reached else 'target_not_reachable',
            'mode':'shadow_estimate','date':local.date().isoformat(),'updated_at':now.isoformat(),'windows':chosen,
            'physical_authority':False,'target_soc':target,'reserve_soc':floor,'target_reachable':reached,
            'projected_soc':round(min(100,soc+(need-max(0,remaining))/capacity*100),2),
            'estimated_import_kwh':round(sum(r['grid_kwh'] for r in chosen),3),
            'estimated_import_cost':round(sum(r['grid_kwh']*r['import_price'] for r in chosen),3),
            'assumptions':['Entered constant charge power and efficiency; no household-load or solar forecast.',
                           'Proposed charging only. No schedule is armed and no export is inferred.']}
