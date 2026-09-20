"""Production-policy shadow lifecycle with explicit observations and saved state.

The runtime has no equipment writer. The controller's execution path is exercised
through SimulatedPlant in tests; observations here can never enable that path.
"""
from __future__ import annotations
import copy
from datetime import datetime, timedelta
import math
import threading

from .controller import Controller
from .daily import build_day, measure_draws, repin_draws
from .planner import Contract
from .simulation import SimulatedPlant


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def validate_snapshot(snapshot):
    if not isinstance(snapshot,dict):raise ValueError('invalid_controller_snapshot')
    numeric={'soc','pack_v','p1_w','grid_charge_a','export_w','grid_export_w'}
    flags={'grid_charge_on','solar_sell_on','stop','discharge_now','pause_charge','docan_fresh','truth_ready'}
    text={'tou','work_mode','energy_pattern','alarm','batt_alarm'}
    row={}
    for key in numeric:
        value=snapshot.get(key)
        if value is not None and not finite(value):raise ValueError('invalid_controller_snapshot')
        row[key]=value
    for key in flags:
        value=snapshot.get(key)
        if value is not None and type(value) is not bool:raise ValueError('invalid_controller_snapshot')
        row[key]=value
    for key in text:
        value=snapshot.get(key)
        if value is not None and (not isinstance(value,str) or len(value)>80):raise ValueError('invalid_controller_snapshot')
        row[key]=value
    row['temps']={}
    for key,_,_ in Controller.THERMAL:
        value=(snapshot.get('temps') or {}).get(key)
        if value is not None and not finite(value):raise ValueError('invalid_controller_snapshot')
        row['temps'][key]=value
    row['ages']={}
    for key in Controller.MAX_AGE_S:
        value=(snapshot.get('ages') or {}).get(key)
        if value is not None and not finite(value):raise ValueError('invalid_controller_snapshot')
        row['ages'][key]=value
    row['programs']={}
    for index in range(1,7):
        source=(snapshot.get('programs') or {}).get(str(index),(snapshot.get('programs') or {}).get(index,{}))
        item={}
        for key in ('time','charging'):
            value=source.get(key)
            if value is not None and (not isinstance(value,str) or len(value)>20):raise ValueError('invalid_controller_snapshot')
            item[key]=value
        for key in ('soc','power','voltage'):
            value=source.get(key)
            if value is not None and not finite(value):raise ValueError('invalid_controller_snapshot')
            item[key]=value
        row['programs'][index]=item
    return row


def validate_pin(plan):
    if plan is None:return None
    if not isinstance(plan,dict):raise ValueError('invalid_controller_plan')
    day=plan.get('for_date')
    datetime.strptime(day,'%Y-%m-%d')
    result={'for_date':day,'date':day}
    if 'ceiling_pct' in plan:
        value=plan['ceiling_pct']
        if not finite(value) or not 0<=value<=95:raise ValueError('invalid_controller_plan')
        result['ceiling_pct']=value
    result['export_clusters']=[]
    for group in plan.get('export_clusters',[]):
        if not isinstance(group,(list,tuple)) or len(group)!=3 or any(type(x) is not int for x in group):raise ValueError('invalid_controller_plan')
        start,end,floor=group
        if not 0<=start<end<=96 or not 25<=floor<=100:raise ValueError('invalid_controller_plan')
        if result['export_clusters'] and start<result['export_clusters'][-1][1]:raise ValueError('invalid_controller_plan')
        result['export_clusters'].append(list(group))
    source=plan.get('reserve') or {}
    result['reserve']={k:source[k] for k in ('pct','n','p50','p90') if k in source and finite(source[k])}
    if 'pct' in result['reserve'] and not 25<=result['reserve']['pct']<=100:raise ValueError('invalid_controller_plan')
    return result


class ShadowRuntime:
    """One serialized policy run; the same immutable frame drives every decision."""
    def __init__(self,storage,contract=None):
        self.storage=storage
        self.contract=contract or Contract()
        self.lock=threading.Lock()

    def run(self,snapshot,at,prices,tomorrow=None,observed_plan=None,draws=None):
        if not self.lock.acquire(blocking=False):raise ValueError('controller_busy')
        try:
            return self._run(snapshot,at,prices,tomorrow,observed_plan,draws)
        finally:self.lock.release()

    def _run(self,snapshot,at,prices,tomorrow,observed_plan,draws):
        state=validate_snapshot(snapshot)
        if len(prices)!=96 or not all(finite(x) for x in prices):raise ValueError('production_policy_requires_96_published_prices')
        if tomorrow is not None and (len(tomorrow)!=96 or not all(finite(x) for x in tomorrow)):raise ValueError('invalid_tomorrow_prices')
        state.update(at=at.isoformat(),prices=list(prices),prices_tomorrow=tomorrow)
        saved=self.storage.data
        history=saved.setdefault('history',[])
        row={k:state[k] for k in ('at','soc','grid_charge_on','solar_sell_on')}
        # Keep at most one actual observation per minute. A missing frame adds no energy.
        minute=at.isoformat()[:16]
        if not history or history[-1]['at'][:16]!=minute:history.append(row)
        cutoff=(at-timedelta(days=14)).isoformat()
        saved['history']=[r for r in history if r['at']>=cutoff][-20160:]
        observed=validate_pin(observed_plan)
        if observed is not None and observed['for_date']!=at.date().isoformat():raise ValueError('wrong_controller_plan_day')
        schedules=saved.setdefault('schedule',{})
        schedule_error=None
        # Preserve the production times. A missed job is visible; it is not silently
        # moved into a different time window on startup.
        job='arm' if at.hour==23 and at.minute==15 else 'repin' if at.hour==10 and at.minute==30 else None
        job_id=at.date().isoformat()+':'+str(job)
        if job and job_id not in schedules:
            target=at.date()+timedelta(days=1) if job=='arm' else at.date()
            curve=tomorrow if job=='arm' else prices
            try:
                if curve is None:raise ValueError('published_tomorrow_prices_required')
                if job=='repin':
                    measured=measure_draws(saved['history'],at,target,(saved.get('draws') or {}).get('reserve',{}).get('pct'))
                    measured,verdict,detail=repin_draws(measured)
                else:
                    # The nightly arm consumes the latest measured series. It does
                    # not relabel yesterday's reserve as tomorrow's measurement.
                    measured=saved.get('draws') or draws
                plan=build_day(curve,state['soc'],at,target,measured,self.contract)
                plan['physical_authority']=False
                self.storage.save_plan(plan)
                if job=='repin':saved['draws']=measured
                schedules[job_id]={'status':'simulated','at':at.isoformat()}
            except (ValueError,TypeError,KeyError,SystemExit):
                schedule_error='scheduled_plan_not_ready'
                schedules[job_id]={'status':schedule_error,'at':at.isoformat()}
            saved['schedule']=dict(sorted(schedules.items())[-30:])
        pin=observed or saved.get('plans',{}).get(at.date().isoformat())
        # A supplied measured-draw artifact can bootstrap an offline comparison. It
        # never arms equipment, and is not interpreted as commissioning permission.
        if pin is None and draws is not None:
            pin=build_day(prices,state['soc'],at,at.date(),draws,self.contract)
            self.storage.save_plan(pin)
        transport=SimulatedPlant(state)
        policy=Controller(at,transport,self.storage,pin,self.contract,allow_writes=False)
        input_errors=policy.validity(state)
        policy.tick(False)
        assert not transport.writes
        outcome=dict(self.storage.state)
        events=self.storage.data['events']
        event=next((r for r in reversed(events) if r.get('phase') in ('SHADOW','NO_VECTOR')), {})
        if event.get('phase')=='NO_VECTOR':outcome={'action':'no_complete_target','reason':event.get('reason')}
        windows=[]
        if pin:
            midnight=at.replace(hour=0,minute=0,second=0,microsecond=0)
            for start,end,floor in pin.get('export_clusters',[]):
                windows.append({'start':(midnight+timedelta(minutes=start*15)).isoformat(),
                                'end':(midnight+timedelta(minutes=end*15)).isoformat(),'action':'export','reserve_soc':floor})
            # The charge window displayed is the observed firmware window, not a
            # claim that this read-only integration programmed it.
            programs=state['programs']
            for i in range(1,7):
                if programs[i]['charging']!='Grid':continue
                try:
                    start=datetime.strptime(programs[i]['time'][:5],'%H:%M')
                    end=datetime.strptime(programs[i%6+1]['time'][:5],'%H:%M')
                except (TypeError,ValueError):continue
                a=midnight+timedelta(hours=start.hour,minutes=start.minute)
                b=midnight+timedelta(hours=end.hour,minutes=end.minute)
                if b<=a:b+=timedelta(days=1)
                windows.append({'start':a.isoformat(),'end':b.isoformat(),'action':'charge'})
        ceiling,_=policy.plan_ceiling()
        contract,_=policy.contract_for_today(pin)
        self.storage.flush()
        return {'mode':'production_shadow','status':outcome.get('action','no_complete_target'),
                'date':at.date().isoformat(),'updated_at':at.isoformat(),'physical_authority':False,
                'windows':sorted(windows,key=lambda w:w['start']),'ceiling_pct':ceiling,
                'reserve_soc':contract.reserve_floor_pct,'reason':outcome.get('reason',event.get('reason')),
                'would_write':outcome.get('would_write'),'decision':event,'scheduled_jobs':saved['schedule'],
                'inputs_valid':not input_errors and event.get('phase')=='SHADOW' and not event.get('veto'),
                'input_errors':input_errors,
                'schedule_error':schedule_error,'pinned_plan_present':pin is not None,
                'assumptions':['Production policy replay. Equipment commands are disabled.',
                               'A displayed charge window comes from observed firmware settings.',
                               'Solar forecasts and anticipated prices do not change this policy.']}
