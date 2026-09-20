"""Controller execution and independent local watchdog loops.

Watchdogs do not depend on prices or the planner and run in another process
against the same durable store. A whole-host power failure is outside this model.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
import time
from pathlib import Path

from .control_device import FIELDS, STOP_VALUES, LabAuthority, DeviceError, WriteDenied
from .control_store import DeviceLease
from .engine.controller import Controller
from .engine.planner import Contract
from .engine.runtime import validate_pin, finite
from .engine.safety import charge_stop, export_stop, DeadmanLimits


def identity(connection):
    return hashlib.sha256(json.dumps(connection,sort_keys=True).encode()).hexdigest()


def battery_identity(connection):
    # Serial aliases and different BMS addresses still share the same wire.
    return identity({'port':str(Path(connection['port']).resolve())}) if connection.get('port') else identity(connection)


class StoredAuthority(LabAuthority):
    """Recheck another process's durable STOP immediately before transmission."""
    def __init__(self, connection, store):
        super().__init__(connection)
        self.store = store

    def check(self, cfg, field, value, generation, stop_only=False):
        super().check(cfg, field, value, generation, stop_only)
        if not stop_only and (self.store.latch is not None or not self.store.get('active',False)
                              or self.store.get('owner_stop',False)):
            self.revoke()
            raise WriteDenied('durable_authority_revoked')


class ThreadTransport:
    """Bridge the captured synchronous policy to the asynchronous device adapter."""
    def __init__(self, session, snapshot, generation):
        self.session = session
        self.frame = snapshot
        self.generation = generation

    def snapshot(self):return self.frame

    def _await(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine,self.session.loop)
        try:return future.result(timeout=self.session.device.timeout*4+2)
        except BaseException:
            future.cancel()
            raise

    def read(self, field):return self._await(self.session.device.read(field))

    def write(self, field, value):
        stop_only = self.session.store.latch is not None or not self.session.authority.enabled
        if stop_only:
            self.session.authority.revoke()
        return self._await(self.session.device.write(field,value,authority=self.session.authority,
                           generation=self.generation,stop_only=stop_only))

    def sleep(self, seconds):time.sleep(seconds)


async def minimal_stop(device, authority, store, reason, actor):
    """Four bounded reductions, all attempted; no rollback to prior activity."""
    authority.revoke()
    durable = True
    try:store.set_latch({'why':reason, 'at':datetime.now(timezone.utc).isoformat()})
    except Exception:durable = False
    outcomes = []
    for field, value in STOP_VALUES.items():
        row = {'field':field,'value':value,'verified':False}
        try:store.event(actor, {'phase':'STOP_PREPARED','field':field,'value':value})
        except Exception:durable = False
        try:
            await device.write(field,value,authority=authority,generation=authority.generation,stop_only=True)
            row['verified'] = (await device.read(field)) == value
        except Exception as exc:
            row['error'] = type(exc).__name__
        outcomes.append(row)
    result = {'phase':'STOP_RESULT','reason':reason,'fields':outcomes,
              'minimal_readback_verified':all(x['verified'] for x in outcomes),
              'physical_completion_proven':False, 'durable_stop_recorded':durable}
    try:store.event(actor,result)
    except Exception:result['durable_stop_recorded']=False
    return result


class ControlSession:
    """Explicit session activation, policy ticks and manual STOP review."""
    def __init__(self, device, observation, store, contract=None, *, authority=None, limits=None):
        self.device = device
        self.observation = observation
        self.store = store
        self.contract = contract or Contract()
        self.authority = authority or StoredAuthority(device.connection, store)
        self.limits=limits
        device.wire_lock = store.lock_dir/(identity(device.connection)+'.wire.lock')
        observation.source_lock = store.lock_dir/(battery_identity(observation.battery_config)+'.battery.lock')
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        self.lease = None
        self.last_frame = None
        self.worker = None

    async def start(self):
        if self.lease is not None:raise WriteDenied('controller_already_started')
        physical=getattr(self.authority,'record',{}).get('device',{}).get('digest')
        self.lease = DeviceLease(self.store.lock_dir/((physical or identity(self.device.connection))+'.controller.lock'))
        try:
            recorded = self.store.get('equipment_identity')
            if recorded and recorded != identity(self.device.connection):
                raise WriteDenied('saved_state_belongs_to_other_equipment')
            self.store.save('equipment_identity',identity(self.device.connection))
            was_active = self.store.get('active',False)
            self.store.save('active',False)
            if was_active:
                self.store.set_latch({'why':'unclean_controller_restart','at':datetime.now(timezone.utc).isoformat()})
            if self.store.latch:
                await minimal_stop(self.device,self.authority,self.store,'startup_with_stop_latch','startup')
            self.store.save('heartbeat',time.time())
        except BaseException:
            self.lease.close();self.lease=None
            raise

    async def observe(self, at):
        frame = await self.observation.read(at,owner_stop=self.store.get('owner_stop',False))
        self.last_frame = frame
        self.store.save('heartbeat',time.time())
        return frame

    async def enable(self, at):
        if self.lease is None or self.store.latch is not None or (self.worker and not self.worker.done()):
            raise WriteDenied('startup_or_stop_review_required')
        frame = await self.observe(at)
        controller = Controller(at,None,self.store,contract=self.contract,limits=self.limits)
        if controller.validity(frame) or controller.action_gate(frame,'charge'):
            raise WriteDenied('commissioning_observations_not_ready')
        fields = (await self.device.observe())['fields']
        if any(fields[k] != v for k,v in STOP_VALUES.items()):
            raise WriteDenied('equipment_must_be_idle_before_arming')
        self.store.save('audit_baseline',fields)
        self.store.save('audit_event_cursor',max((r['id'] for r in self.store.events()),default=0))
        self.authority.arm()
        try:self.store.arm()
        except BaseException:
            self.authority.revoke()
            raise

    async def tick(self, at, prices, plan, tomorrow=None):
        async with self.lock:
            if self.store.latch or not self.store.get('active',False) or (self.worker and not self.worker.done()):
                self.authority.revoke()
                raise WriteDenied('controller_not_armed')
            try:
                count=len(prices) if hasattr(prices,'contract') else 96
                future_count=len(tomorrow) if hasattr(tomorrow,'contract') else 96
                if (at.tzinfo is None or len(prices)!=count or not all(finite(x) for x in prices)
                    or (tomorrow is not None and (len(tomorrow)!=future_count or not all(finite(x) for x in tomorrow)))):
                    raise ValueError('invalid_price_day')
                midnight=at.replace(hour=0,minute=0,second=0,microsecond=0)
                if not hasattr(prices,'contract') and (midnight+timedelta(days=1)).timestamp()-midnight.timestamp()!=86400:
                    raise ValueError('unsupported_clock_change_day')
                pin=validate_pin(plan)
                if pin is None or pin['for_date']!=at.date().isoformat() or pin['slot_count']!=len(prices):
                    raise ValueError('current_pinned_plan_required')
                frame=await self.observe(at)
                frame.update(prices=prices,prices_tomorrow=tomorrow)
                self.store.save('controller_busy_until',time.time()+90)
                transport=ThreadTransport(self,frame,self.authority.generation)
                policy=Controller(at,transport,self.store,pin,self.contract,limits=self.limits,allow_writes=True)
                self.worker=asyncio.create_task(asyncio.to_thread(policy.tick,True))
                await asyncio.shield(self.worker)
                if self.store.latch:
                    self.authority.revoke()
                self.store.save('heartbeat',time.time())
            except BaseException:
                await self.stop('controller_tick_interrupted')
                raise
            finally:self.store.save('controller_busy_until',0)

    async def stop(self, reason):
        return await minimal_stop(self.device,self.authority,self.store,reason,'controller')

    async def apply_programs(self, at, desired, plan_day):
        """Stage six programs while idle. Never restore activity after a failure."""
        async with self.lock:
            if self.store.latch or not self.store.get('active',False):
                raise WriteDenied('controller_not_armed')
            # Normalize the pure planner's HH:MM:SS output before any writes.
            if at.tzinfo is None or plan_day not in (at.date().isoformat(),(at.date()+timedelta(days=1)).isoformat()):
                raise ValueError('invalid_program_day')
            if set(desired)!=set(range(1,7)):
                raise ValueError('six_programs_required')
            target={}
            for i in range(1,7):
                if set(desired[i])!={'time','charging','soc','power','voltage'}:
                    raise ValueError('incomplete_program')
                for key,value in desired[i].items():
                    if key=='time':
                        if not isinstance(value,str) or (len(value)!=5 and (len(value)!=8 or value[5:]!=':00')):
                            raise ValueError('invalid_program_time')
                        value=value[:5]
                    field=f'program_{i}_{key}'
                    FIELDS[field].encode(value,0)
                    if key=='charging' and value not in ('Disabled','Grid'):
                        raise ValueError('generator_program_unsupported')
                    target[field]=value
            starts=[target[f'program_{i}_time'] for i in range(1,7)]
            if starts!=sorted(set(starts)) or starts[0]=='00:00':
                raise ValueError('invalid_program_boundaries')
            frame=await self.observe(at)
            if (not frame['truth_ready'] or abs(frame['p1_w'])>500 or abs(frame['battery_power'])>300
                or frame['alarm']!='OK' or frame['batt_alarm']!='off'):
                raise WriteDenied('programming_requires_fresh_idle_equipment')
            before=(await self.device.observe())['fields']
            if any(before[k]!=v for k,v in STOP_VALUES.items()):
                raise WriteDenied('programming_requires_inactive_controls')
            generation=self.authority.generation
            async def put(field,value):
                old=await self.device.read(field)
                self.store.event('programmer',{'phase':'WRITE_PREPARED','entity':field,'value':value})
                await self.device.write(field,value,authority=self.authority,generation=generation,expected=old)
                self.store.event('programmer',{'phase':'WRITE_READBACK_VERIFIED','entity':field,'value':value})
            self.store.save('controller_busy_until',time.time()+90)
            try:
                await put('tou','Disabled')
                for i in range(1,7):await put(f'program_{i}_charging','Disabled')
                for field,value in target.items():
                    if not field.endswith('_charging'):await put(field,value)
                for i in range(1,7):await put(f'program_{i}_charging',target[f'program_{i}_charging'])
                after=(await self.device.observe())['fields']
                if any(after[k]!=v for k,v in target.items()):raise DeviceError('program_readback_mismatch')
                await put('tou',before['tou'])
                self.store.save('programmed_day',plan_day)
                self.store.save('heartbeat',time.time())
            except BaseException:
                await self.stop('program_transaction_incomplete')
                raise
            finally:self.store.save('controller_busy_until',0)

    async def acknowledge_stop(self, expected_id, at):
        """Clear a reviewed latch only after fresh idle proof; never re-arm here."""
        if self.worker and not self.worker.done():raise WriteDenied('controller_worker_still_running')
        if self.store.get('owner_stop',False):raise WriteDenied('owner_stop_still_set')
        for _ in range(3):
            frame=await self.observe(at)
            fields=(await self.device.observe())['fields']
            if (not frame['truth_ready'] or frame['alarm']!='OK' or frame['batt_alarm']!='off'
                or any(fields[k]!=v for k,v in STOP_VALUES.items())
                or frame['battery_power'] < -300 or frame['p1_w'] < -200):
                raise WriteDenied('safe_idle_not_verified')
        self.authority.revoke()
        self.store.acknowledge(expected_id)
        self.store.event('owner',{'phase':'STOP_ACKNOWLEDGED','id':expected_id,'rearmed':False})

    async def close(self):
        try:
            if self.store.get('active',False):await self.stop('controller_shutdown')
        finally:
            self.authority.revoke()
            # A cancelled coroutine does not terminate its policy thread. Keep
            # ownership and the store open until that bounded worker has exited.
            try:
                if self.worker and not self.worker.done():
                    await asyncio.shield(self.worker)
            finally:
                # Release after a failed worker has exited, but retain ownership
                # if shutdown itself was cancelled while the thread still runs.
                if (not self.worker or self.worker.done()) and self.lease:
                    self.lease.close();self.lease=None


class Watchdog:
    """A separate worker with fresh observations and durable stop-only authority."""
    def __init__(self, kind, device, observation, store, *, limits=None, max_heartbeat_age=75, require_watchdogs=True, authority=None):
        if kind not in ('charge','export','supervisor','auditor'):raise ValueError('invalid_watchdog_role')
        self.kind=kind;self.device=device;self.observation=observation;self.store=store
        self.authority=authority or LabAuthority(device.connection)
        device.wire_lock=store.lock_dir/(identity(device.connection)+'.wire.lock')
        if observation:observation.source_lock=store.lock_dir/(battery_identity(observation.battery_config)+'.battery.lock')
        self.limits=limits or DeadmanLimits()
        self.max_heartbeat_age=max_heartbeat_age
        self.require_watchdogs=require_watchdogs

    async def once(self, at):
        reason = None
        try:
            self.store.save('watchdog_'+self.kind,time.time())
            if not self.store.get('active',False) and not self.store.latch:return None
            if self.store.get('equipment_identity')!=identity(self.device.connection):
                raise WriteDenied('watchdog_equipment_mismatch')
            if self.kind=='supervisor':
                heartbeat=self.store.get('heartbeat',0)
                if not 0<=time.time()-heartbeat<=self.max_heartbeat_age:reason='controller_heartbeat_lost'
                elif self.require_watchdogs and any(not 0<=time.time()-self.store.get('watchdog_'+role,0)<=self.max_heartbeat_age
                                                    for role in ('charge','export','auditor')):
                    reason='independent_watchdog_heartbeat_lost'
                if self.store.latch:reason='durable_stop_latch_present'
            elif self.kind=='auditor':
                if self.store.get('controller_busy_until',0)>time.time():return None
                observed=(await self.device.observe())['fields']
                baseline=self.store.get('audit_baseline')
                cursor=self.store.get('audit_event_cursor',0)
                records=self.store.events(cursor)
                claims={r.get('entity'):r.get('value') for r in records if r.get('phase')=='WRITE_READBACK_VERIFIED'}
                for r in records:
                    if r.get('phase')=='STOP_RESULT':
                        for f in r.get('fields',[]):
                            if f.get('verified') or (f.get('transport_returned') and f.get('readback_matches')):
                                claims[f.get('field',f.get('entity'))]=f['value']
                if baseline:
                    unexpected=[k for k in observed if observed[k]!=baseline.get(k) and claims.get(k)!=observed[k]]
                    if unexpected:reason='unattributed_control_change'
                if reason is None:
                    self.store.save('audit_baseline',observed)
                    self.store.save('audit_event_cursor',max((r['id'] for r in records),default=cursor))
            else:
                frame=await self.observation.read(at,owner_stop=self.store.get('owner_stop',False))
                reason=(charge_stop(frame,-frame['battery_power'],self.limits) if self.kind=='charge'
                        else export_stop(frame,at,self.limits))
                # Unlike price availability, telemetry/STOP uncertainty cannot
                # exempt an active installation from its independent guard.
                if frame['stop'] or frame['batt_alarm']!='off':reason='owner_stop_or_battery_alarm'
        except WriteDenied:
            raise
        except Exception:
            reason='watchdog_observation_unavailable'
        if reason:
            return await minimal_stop(self.device,self.authority,self.store,reason,self.kind)
        return None

    async def run(self, stop_event, *, clock, interval=1):
        """The host must supply its installation-local, timezone-aware clock."""
        lease=DeviceLease(self.store.lock_dir/(identity(self.device.connection)+'.'+self.kind+'.lock'))
        try:
            while not stop_event.is_set():
                await self.once(clock())
                try:await asyncio.wait_for(stop_event.wait(),interval)
                except TimeoutError:pass
        finally:lease.close()
