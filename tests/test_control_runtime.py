"""Local lifecycle, durable STOP and process-failure tests with real wire traffic."""
import asyncio
import copy
from datetime import datetime, timezone
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from control_peer import ControlPeer
from control_process_peer import supervisor_worker,stalled_controller
from test_control_device import battery_frame
from custom_components.docan_deye_ems.docan import parse_frame
from custom_components.docan_deye_ems.control_device import DeyeDevice,WriteDenied,DeviceError,STOP_VALUES
from custom_components.docan_deye_ems.control_observation import EquipmentObservation
from custom_components.docan_deye_ems.control_runtime import ControlSession,Watchdog,identity,ThreadTransport
from custom_components.docan_deye_ems.control_store import ControlStore,DeviceLease
from custom_components.docan_deye_ems.engine.simulation import example_snapshot

AT=datetime(2026,9,20,12,20,tzinfo=timezone.utc)
PIN={'for_date':'2026-09-20','ceiling_pct':95,'export_clusters':[[76,80,64]],'reserve':{'pct':64}}


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.peer=await ControlPeer('modbus_tcp').start()
        self.device=DeyeDevice(self.peer.connection(),timeout=.2)
        self.battery=parse_frame(battery_frame(),0,controller=True)
        self.observation=EquipmentObservation(self.device,{},battery_reader=lambda _:copy.deepcopy(self.battery))
        self.store=ControlStore(Path(self.tmp.name)/'controller.sqlite')
        self.session=ControlSession(self.device,self.observation,self.store)
        self.patch_sleep=patch.object(ThreadTransport,'sleep',lambda *_:None)
        self.patch_sleep.start()
        await self.session.start()

    async def asyncTearDown(self):
        await self.session.close()
        if self.session.worker:await asyncio.gather(self.session.worker,return_exceptions=True)
        self.store.close()
        await self.peer.close()
        self.assertEqual(self.peer.errors,[])
        self.patch_sleep.stop()
        self.tmp.cleanup()

    async def arm(self):
        await self.session.observe(AT);await self.session.observe(AT)
        await self.session.enable_lab(AT)

    def guard(self,kind):
        device=DeyeDevice(self.peer.connection(),timeout=.2)
        observation=EquipmentObservation(device,{},battery_reader=lambda _:copy.deepcopy(self.battery))
        return Watchdog(kind,device,observation,self.store)

    async def test_cold_start_cannot_write_or_arm_before_truth_warmup(self):
        self.assertEqual(self.peer.writes,[])
        with self.assertRaises(WriteDenied):await self.session.enable_lab(AT)
        with self.assertRaises(WriteDenied):await self.session.tick(AT,[.3]*96,PIN)
        self.assertEqual(self.peer.writes,[])
        await self.arm()
        self.assertTrue(self.store.get('active'))

    async def test_real_controller_charges_and_auditor_accepts_only_verified_writes(self):
        await self.arm()
        await self.session.tick(AT,example_snapshot(AT)['prices'],PIN)
        self.assertEqual(self.store.state['action'],'charge')
        self.assertTrue(self.store.state['converged'])
        self.assertEqual(self.peer.writes,[(128,160),(130,1)])
        self.assertIsNone(await self.guard('auditor').once(AT))
        # An intent or a shadow proposal does not explain an actual equipment change.
        self.store.event('controller',{'phase':'WRITE_PREPARED','entity':'export_w','value':6000})
        self.store.event('controller',{'phase':'SHADOW','entity':'export_w','value':6000})
        self.peer.registers[143]=6000
        await self.guard('auditor').once(AT)
        self.assertEqual(self.store.latch['why'],'unattributed_control_change')
        self.assertEqual(self.peer.registers[130],0)

    async def test_uncertain_command_latches_and_communications_recovery_does_not_rearm(self):
        await self.arm();self.peer.fault='lost_ack'
        await self.session.tick(AT,example_snapshot(AT)['prices'],PIN)
        self.assertIsNotNone(self.store.latch)
        self.assertEqual(self.peer.writes.count((128,160)),1)
        self.assertNotIn((130,1),self.peer.writes)
        self.peer.fault=None
        with self.assertRaises(WriteDenied):await self.session.enable_lab(AT)
        with self.assertRaises(WriteDenied):await self.session.tick(AT,[.3]*96,PIN)

    async def test_stop_attempts_all_four_fields_despite_one_failed_transport(self):
        await self.arm()
        self.peer.registers.update({130:1,145:1,142:0,141:0})
        original=self.device.write
        async def faulty(field,*args,**kwargs):
            if field=='solar_sell':raise DeviceError('simulated_failure')
            return await original(field,*args,**kwargs)
        with patch.object(self.device,'write',side_effect=faulty):result=await self.session.stop('test_stop')
        self.assertFalse(result['minimal_readback_verified'])
        self.assertEqual(len(result['fields']),4)
        self.assertEqual(self.peer.writes,[(130,0),(142,2),(141,1)])
        self.assertFalse(result['physical_completion_proven'])

    async def test_stop_persists_restart_and_acknowledgement_does_not_rearm(self):
        await self.arm();await self.session.stop('operator_review')
        latch=self.store.latch
        self.session.lease.close();self.session.lease=None
        self.store.close()
        self.store=ControlStore(Path(self.tmp.name)/'controller.sqlite')
        self.session=ControlSession(self.device,self.observation,self.store)
        await self.session.start()
        self.assertEqual(self.store.latch['id'],latch['id'])
        with self.assertRaises(WriteDenied):await self.session.acknowledge_stop(latch['id'],AT)
        self.battery['battery_power']=0;self.peer.registers[625]=100
        with self.assertRaises(ValueError):await self.session.acknowledge_stop('stale-id',AT)
        await self.session.acknowledge_stop(latch['id'],AT)
        self.assertIsNone(self.store.latch);self.assertFalse(self.store.get('active'))
        with self.assertRaises(WriteDenied):await self.session.tick(AT,[.3]*96,PIN)
        await self.session.enable_lab(AT)
        self.assertTrue(self.store.get('active'))

    async def test_unclean_restart_reduces_controls_and_latches(self):
        await self.arm();self.peer.registers[130]=1
        self.session.lease.close();self.session.lease=None
        self.session=ControlSession(self.device,self.observation,self.store)
        await self.session.start()
        self.assertEqual(self.store.latch['why'],'unclean_controller_restart')
        self.assertEqual(self.peer.registers[130],0)
        self.assertFalse(self.store.get('active'))

    async def test_independent_charge_guard_stops_on_temperature_without_prices(self):
        await self.arm();self.peer.registers[130]=1
        self.battery['controller_temperatures']['probe_4_temperature']=46
        result=await self.guard('charge').once(AT)
        self.assertEqual(result['reason'],'probe_temperature_bound')
        self.assertEqual(self.peer.registers[130],0)

    async def test_independent_export_guard_stops_at_floor_and_hard_end(self):
        for soc,at,reason in ((42,AT.replace(hour=19),'export_soc_floor'),(70,AT.replace(hour=23),'export_hard_end')):
            self.store.save('latch',None);self.store.save('active',True)
            self.peer.registers[145]=1;self.peer.registers[625]=65536-4000
            self.battery['battery_soc']=soc
            result=await self.guard('export').once(at)
            self.assertEqual(result['reason'],reason)
            self.assertEqual(self.peer.registers[145],0)

    async def test_missing_battery_and_supervisor_lost_heartbeat_stop_independently(self):
        await self.arm();self.peer.registers[130]=1
        guard=self.guard('charge')
        with patch.object(guard.observation,'battery_reader',side_effect=TimeoutError):
            result=await guard.once(AT)
        self.assertEqual(result['reason'],'watchdog_observation_unavailable')
        self.store.save('latch',None);self.store.save('active',True);self.store.save('heartbeat',0)
        self.peer.registers[130]=1
        self.assertEqual((await self.guard('supervisor').once(AT))['reason'],'controller_heartbeat_lost')
        self.assertEqual(self.peer.registers[130],0)

    async def test_supervisor_refuses_missing_independent_watchdog(self):
        await self.arm();self.peer.registers[130]=1
        self.store.save('heartbeat',time.time())
        result=await self.guard('supervisor').once(AT)
        self.assertEqual(result['reason'],'independent_watchdog_heartbeat_lost')
        self.assertEqual(self.peer.registers[130],0)

    async def test_watchdogs_share_battery_read_ownership(self):
        first=self.guard('charge').observation;second=self.guard('export').observation
        counters={'active':0,'peak':0}
        def read(_):
            counters['active']+=1;counters['peak']=max(counters['peak'],counters['active'])
            time.sleep(.025)
            counters['active']-=1
            return copy.deepcopy(self.battery)
        first.battery_reader=second.battery_reader=read
        await asyncio.gather(first.read(AT,owner_stop=False),second.read(AT,owner_stop=False))
        self.assertEqual(counters['peak'],1)

    async def test_queued_command_is_revoked_by_another_store_before_wire_send(self):
        await self.arm()
        lease=DeviceLease(self.device.wire_lock)
        pending=asyncio.create_task(self.device.write('grid_charge','on',authority=self.session.authority,
                                                      generation=self.session.authority.generation))
        await asyncio.sleep(.04)
        other=ControlStore(self.store.path);other.set_latch({'why':'independent_guard'});other.close()
        lease.close()
        with self.assertRaises(WriteDenied):await pending
        self.assertEqual(self.peer.writes,[])

    async def test_invalid_price_day_pin_and_dst_cannot_energize(self):
        cases=[(AT,[.3]*95,PIN),(AT,[float('nan')]*96,PIN),(AT,[.3]*96,{**PIN,'for_date':'2026-09-19'}),
               (datetime(2026,10,25,12,tzinfo=ZoneInfo('Europe/Amsterdam')),[.3]*96,{**PIN,'for_date':'2026-10-25'})]
        for at,prices,pin in cases:
            self.store.save('latch',None);await self.arm()
            with self.assertRaises(ValueError):await self.session.tick(at,prices,pin)
            self.assertIsNotNone(self.store.latch)
            self.assertNotIn((130,1),self.peer.writes)

    async def test_storage_failure_never_allows_first_energizing_command(self):
        await self.arm()
        with patch.object(self.store,'journal',side_effect=OSError('disk_full')):
            with self.assertRaises(OSError):await self.session.tick(AT,example_snapshot(AT)['prices'],PIN)
        self.assertEqual(self.peer.writes,[])
        self.assertIsNotNone(self.store.latch)

    async def test_failed_stop_storage_does_not_suppress_emergency_reductions(self):
        await self.arm();self.peer.registers.update({130:1,145:1,142:0,141:0})
        with patch.object(self.store,'set_latch',side_effect=OSError('disk_full')),\
             patch.object(self.store,'event',side_effect=OSError('disk_full')):
            result=await self.session.stop('storage_fault')
        self.assertFalse(result['durable_stop_recorded'])
        self.assertTrue(result['minimal_readback_verified'])
        self.assertFalse(self.session.authority.enabled)
        self.assertEqual(self.peer.writes,[(145,0),(130,0),(142,2),(141,1)])

    async def test_cancelled_thread_cannot_send_late_energizing_command(self):
        await self.arm()
        started=threading.Event();release=threading.Event()
        def suspended_policy(policy,live):
            started.set();release.wait(3)
            try:policy.transport.write('grid_charge','on')
            except WriteDenied:return
            raise AssertionError('Late command escaped revocation')
        with patch('custom_components.docan_deye_ems.control_runtime.Controller.tick',suspended_policy):
            pending=asyncio.create_task(self.session.tick(AT,example_snapshot(AT)['prices'],PIN))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,2))
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):await pending
                with self.assertRaises(WriteDenied):await self.session.acknowledge_stop(self.store.latch['id'],AT)
            finally:release.set()
            await self.session.worker
        self.assertNotIn((130,1),self.peer.writes)

    async def test_six_program_transaction_reads_back_and_preserves_weekdays(self):
        await self.arm();self.battery['battery_power']=0;self.peer.registers[625]=100
        desired={i:{'time':t,'power':8000,'soc':95 if i==2 else 25,'voltage':55.2 if i==2 else 49,
                    'charging':'Grid' if i==2 else 'Disabled'}
                 for i,t in enumerate(('01:00:00','12:15:00','18:15:00','23:15:00','23:30:00','23:45:00'),1)}
        await self.session.apply_programs(AT,desired,AT.date().isoformat())
        self.assertEqual(self.peer.writes[0],(146,254));self.assertEqual(self.peer.writes[-1],(146,255))
        self.assertEqual(self.peer.registers[149],1215)
        self.assertEqual(self.store.get('programmed_day'),'2026-09-20')
        self.assertNotIn((130,1),self.peer.writes)
        self.assertIsNone(await self.guard('auditor').once(AT))

    async def test_failed_program_transaction_does_not_restore_tou_or_activity(self):
        await self.arm();self.battery['battery_power']=0;self.peer.registers[625]=100
        desired={i:{'time':f'{i+1:02d}:00','power':8000,'soc':25,'voltage':49,'charging':'Disabled'} for i in range(1,7)}
        self.peer.fault='lost_ack'
        with self.assertRaises(DeviceError):await self.session.apply_programs(AT,desired,AT.date().isoformat())
        self.assertEqual(self.peer.registers[146],254)
        self.assertIsNotNone(self.store.latch)
        self.assertNotIn((146,255),self.peer.writes)

    async def test_single_controller_lease_and_wrong_endpoint_storage(self):
        duplicate=ControlSession(self.device,self.observation,self.store)
        with self.assertRaises(RuntimeError):await duplicate.start()
        self.session.lease.close();self.session.lease=None
        self.store.save('equipment_identity','another-device')
        with self.assertRaises(WriteDenied):await duplicate.start()
        self.assertIsNone(duplicate.lease)
        self.assertEqual(self.peer.writes,[])

    async def test_separate_process_guard_survives_planner_process_death(self):
        # This process hosts the inverter. Two spawned processes have separate
        # interpreters: a stalled planner and a watchdog with no planner inputs.
        self.session.lease.close();self.session.lease=None
        ctx=multiprocessing.get_context('spawn')
        ready=ctx.Event();guard_ready=ctx.Event();finish=ctx.Event();result=ctx.Queue()
        lease_path=self.store.path.parent/(identity(self.device.connection)+'.controller.lock')
        planner=ctx.Process(target=stalled_controller,args=(str(self.store.path),str(lease_path),ready))
        guard=ctx.Process(target=supervisor_worker,args=(self.peer.connection(),str(self.store.path),guard_ready,finish,result))
        self.peer.registers[130]=1
        try:
            planner.start();self.assertTrue(await asyncio.to_thread(ready.wait,10))
            with self.assertRaises(RuntimeError):DeviceLease(lease_path)
            guard.start();self.assertTrue(await asyncio.to_thread(guard_ready.wait,10))
            # Terminate only the child process created in this test.
            planner.terminate();await asyncio.to_thread(planner.join,5)
            outcome=await asyncio.to_thread(result.get,True,8)
            self.assertEqual(outcome['reason'],'controller_heartbeat_lost')
            self.assertTrue(outcome['minimal_readback_verified'])
            self.assertEqual(self.peer.registers[130],0)
            released=DeviceLease(lease_path);released.close()
            self.assertIsNotNone(self.store.latch)
        finally:
            finish.set()
            for process in (planner,guard):
                if process.pid:
                    await asyncio.to_thread(process.join,2)
                    if process.is_alive():process.terminate();await asyncio.to_thread(process.join,2)
                    process.close()
            result.close();result.join_thread()


if __name__=='__main__':unittest.main()
