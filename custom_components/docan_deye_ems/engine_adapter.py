"""Home Assistant observation adapter for the production-policy shadow engine.

Accept a supplied state frame or directly read the Deye and independent BMS.
This adapter never creates command authority or starts a controller session.
"""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from homeassistant.helpers.storage import Store
from .const import DOMAIN
from .model import InputError,instant,day_bounds
from .engine.market import DeliveryPrices
from .control_profile import contract,policy_limits,context_digest,validate_control
from .engine.runtime import ShadowRuntime
from .engine.storage import MemoryState
from .control_device import DeyeDevice
from .control_observation import EquipmentObservation


class ProductionShadowAdapter:
    def __init__(self,hass,settings):
        settings={**settings,'control':validate_control(settings.get('control'),settings['model'])}
        self.hass=hass
        self.settings=settings
        self.store=Store(hass,1,DOMAIN+'.'+settings['installation_id']+'.engine',private=True,atomic_writes=True)
        self.state=MemoryState()
        self.observation=(EquipmentObservation(DeyeDevice(settings['equipment']),settings['battery'])
                          if settings.get('connection')=='direct_deye' and settings['battery']['source']=='docan_usb'
                          and 'controller_snapshot' not in settings['bindings'] else None)
        self.runtime=ShadowRuntime(self.state,contract(settings),policy_limits(settings['control']))
        self.context=context_digest(settings,hass.config.time_zone)
        self.today=None;self.tomorrow=None;self.latest_frame=None

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

    async def plan(self,now,state,periods,frame=None):
        if frame is not None:
            attrs={'schema':1,'updated_at':frame['at'],'snapshot':frame}
        else:
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
                try:return DeliveryPrices(periods,day,self.hass.config.time_zone,allow_export=self.settings['control']['allow_export'])
                except ValueError:
                    if required:raise InputError('incomplete_published_price_day') from None
                    return None
            today=curve(local.date(),True);tomorrow=curve(local.date()+timedelta(days=1),False)
            self.today=today;self.tomorrow=tomorrow;self.latest_frame=frame
            result=await self.hass.async_add_executor_job(self.runtime.run,frame,local,today,tomorrow,
                                                        attrs.get('day_plan'),attrs.get('overnight_draws'))
            await self.save()
            return result
        except InputError:
            raise
        except (ValueError,TypeError,KeyError,AttributeError,IndexError,SystemExit):
            raise InputError('invalid_controller_snapshot') from None
