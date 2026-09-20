"""Home Assistant observation adapter for the production-policy shadow engine.

Only a supplied state frame is read. This adapter has no equipment write method,
service call, serial port or network client.
"""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from homeassistant.helpers.storage import Store
from .const import DOMAIN
from .model import InputError,instant,day_bounds
from .engine.planner import Contract
from .engine.runtime import ShadowRuntime
from .engine.storage import MemoryState


class ProductionShadowAdapter:
    def __init__(self,hass,settings):
        self.hass=hass
        self.settings=settings
        self.store=Store(hass,1,DOMAIN+'.'+settings['installation_id']+'.engine',private=True,atomic_writes=True)
        self.state=MemoryState()
        cfg=settings['plan']
        contract=replace(Contract(),cap_kwh=cfg['capacity_kwh'],floor_min_pct=int(cfg['fallback_reserve_soc']),
                         export_power_w=cfg['export_power_w'],rte=cfg['round_trip_efficiency'],degradation=cfg['wear_cost_per_kwh'],
                         export_price_deduction=cfg['export_price_deduction'])
        self.runtime=ShadowRuntime(self.state,contract)
        # Reconfiguration must not apply old pinned plans to a different source/site.
        context={'plan':cfg,'source':settings['bindings'].get('controller_snapshot'),
                 'prices':{k:v for k,v in settings['pricing'].items() if k!='token'},
                 'time_zone':hass.config.time_zone}
        self.context=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()

    async def initialize(self):
        saved=await self.store.async_load()
        if isinstance(saved,dict) and saved.get('context')==self.context:
            state=saved.get('state')
            if not isinstance(state,dict) or not isinstance(state.get('events'),list):
                raise InputError('invalid_saved_engine_state')
            for key in ('history',):
                if key in state and not isinstance(state[key],list):raise InputError('invalid_saved_engine_state')
            for key in ('plans','schedule','state'):
                if key in state and not isinstance(state[key],dict):raise InputError('invalid_saved_engine_state')
            self.state.data=state

    async def save(self):
        await self.store.async_save({'context':self.context,'state':self.state.data})

    async def plan(self,now,state,periods):
        if state is None or state.state in ('unknown','unavailable'):raise InputError('controller_snapshot_unavailable')
        attrs=state.attributes
        try:
            updated=instant(attrs['updated_at'])
            age=(now-updated).total_seconds()
            if not 0<=age<=self.settings['max_age']:raise InputError('controller_snapshot_stale')
            if attrs.get('schema')!=1:raise InputError('invalid_controller_snapshot')
            frame=dict(attrs['snapshot'])
            # Source ages belong to the captured frame; add elapsed transport time.
            frame['ages']={k:v+age if isinstance(v,(int,float)) and not isinstance(v,bool) else v
                           for k,v in frame.get('ages',{}).items()}
            local=now.astimezone(ZoneInfo(self.hass.config.time_zone))
            def curve(day,required):
                start,end=day_bounds(day,self.hass.config.time_zone)
                if (end-start).total_seconds()!=86400:
                    if required:raise InputError('production_policy_requires_96_published_prices')
                    return None
                rows=[r for r in periods if start<=instant(r['start'])<end]
                if len(rows)!=96:
                    if required:raise InputError('production_policy_requires_96_published_prices')
                    return None
                for i,row in enumerate(rows):
                    if instant(row['start'])!=start+timedelta(minutes=15*i) or instant(row['end'])!=start+timedelta(minutes=15*(i+1)):
                        raise InputError('invalid_controller_prices')
                    if row.get('origin','published')!='published':raise InputError('published_prices_required')
                return [r['import'] for r in rows]
            today=curve(local.date(),True);tomorrow=curve(local.date()+timedelta(days=1),False)
            result=await self.hass.async_add_executor_job(self.runtime.run,frame,local,today,tomorrow,
                                                        attrs.get('day_plan'),attrs.get('overnight_draws'))
            await self.save()
            return result
        except InputError:
            raise
        except (ValueError,TypeError,KeyError,AttributeError,IndexError,SystemExit):
            raise InputError('invalid_controller_snapshot') from None
