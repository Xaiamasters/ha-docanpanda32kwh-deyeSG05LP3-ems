"""Public commissioning and activation against owned loopback devices only."""
import asyncio
import copy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from control_peer import ControlPeer
from test_control_device import battery_frame
from custom_components.docan_deye_ems.control_host import ControlHost,CHECKS,GuardProcesses
from custom_components.docan_deye_ems.control_profile import defaults,contract
from custom_components.docan_deye_ems.control_device import DeyeDevice,WriteDenied,CommissionedAuthority
from custom_components.docan_deye_ems.control_observation import EquipmentObservation
from custom_components.docan_deye_ems.control_runtime import ThreadTransport
from custom_components.docan_deye_ems.docan import parse_frame
from custom_components.docan_deye_ems.engine.market import DeliveryPrices
from custom_components.docan_deye_ems.engine.storage import MemoryState
from custom_components.docan_deye_ems.engine.schedule import update_schedule

AT=datetime(2026,9,20,12,1,tzinfo=timezone.utc)


class ReadyGuards:
    def __init__(self,store,*args):self.store=store;self.closed=False
    async def start(self):
        for role in ('charge','export','auditor','supervisor'):self.store.save('watchdog_'+role,time.time())
    async def close(self):self.closed=True


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.peer=await ControlPeer('modbus_tcp').start()
        self.peer.registers[625]=100
        self.battery=parse_frame(battery_frame(),0,controller=True);self.battery['battery_power']=0
        self.device=DeyeDevice(self.peer.connection(),timeout=.4)
        self.observation=EquipmentObservation(self.device,{},battery_reader=lambda _:copy.deepcopy(self.battery))
        model='SUN-10K-SG05LP3-EU-SM2'
        self.settings={'installation_id':'a'*32,'model':model,'connection':'direct_deye','equipment':self.peer.connection(),
            'battery':{'source':'docan_usb','port':'/dev/ttyUSB0','address':0},'control':defaults(model),'bindings':{},
            'signs':{'battery_power':1,'battery_current':1,'grid_power':1},'pricing':{'provider':'sensor'},
            'plan':{'capacity_kwh':32,'fallback_reserve_soc':64,'export_power_w':7900,'round_trip_efficiency':.87,'wear_cost_per_kwh':.04}}
        midnight=AT.replace(hour=0,minute=0)
        rows=[{'start':(midnight+timedelta(minutes=15*i)).isoformat(),'end':(midnight+timedelta(minutes=15*(i+1))).isoformat(),
               'import':.1 if 52<=i<64 else .4,'export':.05} for i in range(96)]
        prices=DeliveryPrices(rows,AT.date(),'UTC',allow_export=False)
        state=MemoryState();update_schedule(state,AT,prices,None,55,contract(self.settings))
        self.engine=SimpleNamespace(today=prices,tomorrow=None,state=state,save=AsyncMock())
        self.host=ControlHost(self.settings,'UTC',Path(self.tmp.name)/'control.sqlite',self.engine,
                              device=self.device,observation=self.observation,guards_factory=ReadyGuards)
        self.host.now=lambda:AT
        self.sleep=patch.object(ThreadTransport,'sleep',lambda *_:None);self.sleep.start()

    async def asyncTearDown(self):
        self.peer.fault=None
        await self.host.close();await self.peer.close();self.tmp.cleanup();self.sleep.stop()
        self.assertEqual(self.peer.errors,[])

    async def commission(self):
        preview=await self.host.command('preview',{})
        result=await self.host.command('confirm',{'nonce':preview['nonce'],'checks':dict.fromkeys(CHECKS,True)})
        return result

    async def test_preview_and_confirmation_do_not_activate_or_write(self):
        with self.assertRaisesRegex(WriteDenied,'commissioning_required'):await self.host.command('enable_live',{})
        result=await self.commission()
        self.assertTrue(result['commissioned']);self.assertFalse(result['active'])
        self.assertEqual(self.peer.writes,[])

    async def test_activation_programs_device_and_stop_does_not_rearm(self):
        await self.commission();result=await self.host.command('enable_live',{})
        self.assertTrue(result['active']);self.assertEqual(result['mode'],'live')
        self.assertEqual(result['programmed_day'],str(AT.date()))
        self.host.now=lambda:AT.replace(hour=13,minute=5)
        await self.host.tick(inputs_ready=True)
        self.assertEqual(self.host.store.state['action'],'charge')
        self.assertEqual(self.peer.registers[130],1)
        from custom_components.docan_deye_ems.analytics import command_observation
        diagnostic=command_observation(self.host.status(),self.host.now())
        self.assertEqual(diagnostic['charge_current_a'],160)
        self.assertEqual(diagnostic['setpoint_w'],-8000)
        stopped=await self.host.command('stop',{})
        self.assertFalse(stopped['active']);self.assertIsNotNone(stopped['stop'])
        ack=await self.host.command('acknowledge_stop',{'id':stopped['stop']['id']})
        self.assertFalse(ack['active']);self.assertIsNone(ack['stop'])
        self.assertEqual(self.peer.registers[130],0)

    async def test_expired_nonce_missing_check_and_device_change_refuse(self):
        p=await self.host.command('preview',{})
        self.host.preview_record['expires']=0
        with self.assertRaises(WriteDenied):await self.host.command('confirm',{'nonce':p['nonce'],'checks':dict.fromkeys(CHECKS,True)})
        p=await self.host.command('preview',{});self.peer.registers[3]+=1
        with self.assertRaisesRegex(WriteDenied,'device_changed'):await self.host.command('confirm',{'nonce':p['nonce'],'checks':dict.fromkeys(CHECKS,True)})
        self.assertEqual(self.peer.writes,[])

    async def test_firmware_change_before_command_sends_no_write(self):
        await self.commission()
        await self.host.session.enable(AT)
        authority=self.host.session.authority
        self.peer.registers[14]+=1
        with self.assertRaisesRegex(WriteDenied,'device_changed'):
            await self.device.write('grid_charge','on',authority=authority,generation=authority.generation)
        self.assertEqual(self.peer.writes,[])
        self.peer.registers[14]-=1

    async def test_lost_guard_or_changed_limits_revoke_authority(self):
        await self.commission();await self.host.session.enable(AT)
        authority=self.host.session.authority
        self.host.store.save('watchdog_supervisor',0)
        with self.assertRaisesRegex(WriteDenied,'watchdogs_not_ready'):
            await self.device.write('grid_charge','on',authority=authority,generation=authority.generation)
        with self.assertRaisesRegex(WriteDenied,'commissioning_required'):
            CommissionedAuthority(self.peer.connection(),self.host.store,'changed-context')
        self.assertEqual(self.peer.writes,[])

    async def test_telemetry_or_prices_failure_latches_live_operation(self):
        await self.commission();await self.host.command('enable_live',{})
        await self.host.tick(inputs_ready=False)
        self.assertFalse(self.host.store.get('active'))
        self.assertEqual(self.host.store.latch['why'],'live_inputs_unavailable')

    async def test_restore_requires_new_commissioning_and_cannot_rearm(self):
        await self.commission();await self.host.close()
        self.host=ControlHost(self.settings,'UTC',Path(self.tmp.name)/'control.sqlite',self.engine,
                              device=self.device,observation=self.observation,guards_factory=ReadyGuards)
        self.assertFalse(self.host.status()['commissioned'])
        with self.assertRaises(WriteDenied):await self.host.command('enable_live',{})
        self.assertEqual(self.peer.writes,[])

    async def test_real_process_guards_start_and_are_reaped(self):
        await self.commission();await self.host.guards.close()
        self.host.guards=GuardProcesses(self.host.store,self.settings,self.host.context,'UTC')
        await self.host.guards.start()
        children=list(self.host.guards.children)
        self.assertEqual(len(children),4)
        self.assertTrue(all(p.returncode is None for p in children))
        self.assertTrue(all(self.host.status()['watchdogs'].values()))
        await self.host.guards.close()
        self.assertTrue(all(p.returncode is not None for p in children))

    async def test_same_device_identity_cannot_be_owned_through_a_second_endpoint(self):
        await self.commission()
        peer=await ControlPeer('modbus_tcp').start();peer.registers[625]=100
        settings=copy.deepcopy(self.settings);settings['equipment']=peer.connection()
        settings['installation_id']='b'*32
        device=DeyeDevice(peer.connection(),timeout=.4)
        observation=EquipmentObservation(device,{},battery_reader=lambda _:copy.deepcopy(self.battery))
        other=ControlHost(settings,'UTC',Path(self.tmp.name)/'other'/'control.sqlite',self.engine,
                          device=device,observation=observation,guards_factory=ReadyGuards,lock_dir=self.host.store.lock_dir)
        other.now=lambda:AT
        try:
            preview=await other.command('preview',{})
            with self.assertRaisesRegex(RuntimeError,'equipment_role_already_owned'):
                await other.command('confirm',{'nonce':preview['nonce'],'checks':dict.fromkeys(CHECKS,True)})
            self.assertEqual(peer.writes,[])
            self.assertFalse(other.status()['active'])
        finally:await other.close();await peer.close()

    async def test_failed_shutdown_keeps_guards_for_recovery(self):
        await self.commission();await self.host.command('enable_live',{})
        self.peer.registers[130]=1;self.peer.fault='disconnect'
        with self.assertRaisesRegex(WriteDenied,'shutdown_stop_not_verified'):await self.host.close()
        self.assertIsNotNone(self.host.session)
        self.assertFalse(self.host.guards.closed)
        self.peer.fault=None


if __name__=='__main__':unittest.main()
