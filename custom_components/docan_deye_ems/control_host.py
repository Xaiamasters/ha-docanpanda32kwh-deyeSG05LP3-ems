"""Commissioning, operating modes and owned independent guard processes.

Creating this host cannot send an equipment command. Only an authenticated
owner confirmation binds a session to a freshly observed device identity.
"""
from __future__ import annotations

import asyncio
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .control_device import CommissionedAuthority,DeyeDevice,WriteDenied,STOP_VALUES
from .control_observation import EquipmentObservation
from .control_profile import context_digest,contract,policy_limits,MODELS
from .control_runtime import ControlSession,identity,battery_identity
from .control_store import ControlStore
from .engine.controller import Controller

CHECKS=('equipment_readings_match','bms_link_checked','sole_controller',
        'limits_checked','grid_export_permission','host_failure_understood')


class GuardProcesses:
    def __init__(self,store,settings,context,zone):
        self.store=store;self.settings=settings;self.context=context;self.zone=zone
        self.children=[]

    async def start(self):
        if self.children:raise WriteDenied('watchdogs_already_started')
        run_id=uuid4().hex
        self.store.save('watchdog_run_id',run_id)
        try:
            for role in ('charge','export','auditor','supervisor'):
                self.store.save('watchdog_'+role,0)
                payload={'role':role,'store':str(self.store.path),'lock_dir':str(self.store.lock_dir),'connection':self.settings['equipment'],
                         'battery':self.settings['battery'],'context':self.context,'run_id':run_id,
                         'zone':self.zone,'limits':self.settings['control']}
                child=await asyncio.create_subprocess_exec(sys.executable,'-B',str(Path(__file__).with_name('control_worker.py')),
                    stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                self.children.append(child)
                child.stdin.write(json.dumps(payload).encode()+b'\n')
                await child.stdin.drain();child.stdin.close()
            async with asyncio.timeout(15):
                while True:
                    if any(p.returncode is not None for p in self.children):raise WriteDenied('watchdog_start_failed')
                    if all(self.store.get('watchdog_'+r,0)>0 for r in ('charge','export','auditor','supervisor')):break
                    await asyncio.sleep(.1)
        except BaseException:
            await self.close();raise

    async def close(self):
        self.store.save('watchdog_run_id',None)
        for child in self.children:
            if child.returncode is None:child.terminate()
        for child in self.children:
            try:await asyncio.wait_for(child.wait(),8)
            except TimeoutError:
                child.kill();await child.wait()
        self.children=[]


class ControlHost:
    def __init__(self,settings,zone,path,engine,*,device=None,observation=None,guards_factory=GuardProcesses,lock_dir=None):
        self.settings=settings;self.zone=zone;self.engine=engine
        self.context=context_digest(settings,zone)
        self.store=ControlStore(path,lock_dir)
        self.device=device or DeyeDevice(settings['equipment'])
        self.observation=observation or EquipmentObservation(self.device,settings['battery'])
        self.device.wire_lock=self.store.lock_dir/(identity(self.device.connection)+'.wire.lock')
        self.observation.source_lock=self.store.lock_dir/(battery_identity(self.observation.battery_config)+'.battery.lock')
        self.session=None;self.guards=None;self.guards_factory=guards_factory
        self.lock=asyncio.Lock();self.preview_record=None
        self.mode='shadow';self.last_error=None
        if self.store.get('active',False):
            self.store.set_latch({'why':'unclean_host_restart','at':datetime.now(timezone.utc).isoformat()})
        # A restore or restart never reuses physical command authority. The owner
        # must verify the connected device again, even if copied storage agrees.
        self.store.save('active',False)

    def now(self):return datetime.now(ZoneInfo(self.zone))

    def status(self):
        return {'available':True,'mode':self.mode,'active':self.store.get('active',False),'commissioned':self.session is not None,
                'stop':self.store.latch,'controller':self.store.state,'last_error':self.last_error,
                'programmed_day':self.store.get('programmed_day'),
                'watchdogs':{r:0<=time.time()-self.store.get('watchdog_'+r,0)<=75
                             for r in ('charge','export','auditor','supervisor')}}

    async def preview(self):
        if self.store.get('active',False):raise WriteDenied('stop_before_commissioning')
        at=self.now()
        observed=await self.device.identify()
        if observed['rated_power_w']!=MODELS[self.settings['model']]*1000:
            raise WriteDenied('configured_model_does_not_match_device_rating')
        for _ in range(3):frame=await self.observation.read(at,owner_stop=False)
        policy=Controller(at,None,self.store,contract=contract(self.settings),limits=policy_limits(self.settings['control']))
        errors=policy.validity(frame)+policy.action_gate(frame,'charge')
        if errors:raise WriteDenied('commissioning_observations_not_ready')
        fields=(await self.device.observe())['fields']
        if any(fields[k]!=v for k,v in STOP_VALUES.items()) or abs(frame['p1_w'])>500 or abs(frame['battery_power'])>300:
            raise WriteDenied('commissioning_requires_idle_equipment')
        nonce=secrets.token_urlsafe(24)
        self.preview_record={'nonce':nonce,'expires':time.monotonic()+120,'device':observed}
        return {'nonce':nonce,'expires_in':120,'model':self.settings['model'],
                'rated_power_w':observed['rated_power_w'],'firmware_words':observed['firmware_words'],
                'soc':frame['soc'],'pack_v':frame['pack_v'],'grid_w':frame['p1_w'],
                'battery_w':frame['battery_power'],'limits':self.settings['control'],'checks':list(CHECKS)}

    async def confirm(self,payload):
        record=self.preview_record
        self.preview_record=None
        if (record is None or set(payload)!={'nonce','checks'} or payload['nonce']!=record['nonce']
            or time.monotonic()>record['expires'] or not isinstance(payload['checks'],dict)
            or set(payload['checks'])!=set(CHECKS) or any(v is not True for v in payload['checks'].values())):
            raise WriteDenied('fresh_commissioning_confirmation_required')
        observed=await self.device.identify()
        if observed!=record['device']:raise WriteDenied('device_changed_during_commissioning')
        await self._close_session()
        self.store.save('commissioning',{'id':uuid4().hex,'context':self.context,'connection':self.device.connection,
                         'checks_confirmed':True,'device':observed,'limits':self.settings['control'],
                         'export_power_w':self.settings['plan']['export_power_w'],'at':self.now().isoformat()})
        authority=CommissionedAuthority(self.device.connection,self.store,self.context)
        self.session=ControlSession(self.device,self.observation,self.store,contract(self.settings),
                                    authority=authority,limits=policy_limits(self.settings['control']))
        try:
            await self.session.start()
            self.guards=self.guards_factory(self.store,self.settings,self.context,self.zone)
            await self.guards.start()
        except BaseException:
            await self._close_session();raise
        self.mode='shadow'

    def _current_plan(self,at):
        prices=self.engine.today
        if prices is None or prices.day!=at.date():raise WriteDenied('published_current_prices_required')
        plan=self.engine.state.data.get('plans',{}).get(at.date().isoformat())
        if not plan:raise WriteDenied('current_plan_required')
        return prices,plan

    async def apply_plan(self,at,plan):
        programs={int(i):row for i,row in plan['programs'].items()}
        await self.session.apply_programs(at,programs,plan['for_date'])
        self.store.save('programmed_pin',plan['pinned_at'])
        plan.update(program_status='readback_verified',programmed_at=at.isoformat())
        self.engine.state.save_plan(plan)
        for job in self.engine.state.data.get('schedule',{}).values():
            if job.get('for_date')==plan['for_date'] and job.get('status')=='planned':
                job['status']='readback_verified'
        await self.engine.save()

    async def enable(self):
        if not self.session:raise WriteDenied('commissioning_required')
        self.session.authority.verify_identity(await self.device.identify())
        at=self.now()
        prices,plan=self._current_plan(at)
        # Build a new remaining-day plan at activation instead of applying an old
        # preview with a no-longer-reachable opening state.
        from .engine.schedule import reserve_inputs
        from .engine.daily import build_day
        frame=await self.session.observe(at)
        draws=reserve_inputs(self.engine.state.data,at,at.date(),contract(self.settings))
        plan=build_day(prices,frame['soc'],at,at.date(),draws,contract(self.settings))
        plan['reserve_basis']=draws.get('basis','measured_overnight_history')
        self.engine.state.save_plan(plan)
        await self.engine.save()
        await self.session.enable(at)
        try:
            await self.apply_plan(at,plan)
            self.mode='live'
            await self.session.tick(at,prices,plan,self.engine.tomorrow)
        except BaseException:
            await self.session.stop('activation_incomplete');self.mode='stopped';raise

    async def tick(self,*,inputs_ready):
        if not self.session or not self.store.get('active',False):
            if self.store.latch:self.mode='stopped'
            return
        async with self.lock:
            try:
                if not inputs_ready:raise WriteDenied('live_inputs_unavailable')
                at=self.now();prices,plan=self._current_plan(at)
                # Only arm tomorrow in the scheduled evening window. At all other
                # times apply the current pin, then run the captured control policy.
                upcoming=self.engine.state.data.get('plans',{}).get((at.date()+timedelta(days=1)).isoformat())
                apply=upcoming if at.hour==23 and at.minute>=15 and upcoming else plan
                if self.store.get('programmed_pin')!=apply['pinned_at']:
                    await self.apply_plan(at,apply)
                await self.session.tick(at,prices,plan,self.engine.tomorrow)
                if self.store.latch:self.mode='stopped'
            except Exception as exc:
                self.last_error=str(exc) if isinstance(exc,WriteDenied) else 'controller_execution_failed'
                await self.session.stop(self.last_error);self.mode='stopped'

    async def command(self,name,payload):
        async with self.lock:
            if name=='preview' and not payload:return await self.preview()
            if name=='confirm':await self.confirm(payload)
            elif name=='enable_live' and not payload:await self.enable()
            elif name in ('stop','shadow','read_only') and not payload:
                if self.session and (self.store.get('active',False) or self.store.latch):await self.session.stop('owner_requested_stop')
                self.mode='stopped' if self.store.latch else name
            elif name=='acknowledge_stop' and set(payload)=={'id'}:
                if not self.session:raise WriteDenied('commissioning_required')
                await self.session.acknowledge_stop(payload['id'],self.now());self.mode='shadow'
            else:raise WriteDenied('unsupported_control_action')
            return self.status()

    async def _close_session(self):
        if self.session and (self.store.get('active',False) or self.store.latch):
            result=await self.session.stop('controller_shutdown')
            if not result['minimal_readback_verified']:
                self.last_error='shutdown_stop_not_verified'
                raise WriteDenied(self.last_error)
        if self.session:await self.session.close()
        if self.guards:await self.guards.close()
        self.guards=None;self.session=None

    async def close(self):
        async with self.lock:
            await self._close_session()
            self.store.close()
