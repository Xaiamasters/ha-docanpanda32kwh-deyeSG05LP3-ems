"""Reserve policy and its real native-control path, using owned loopback devices."""
import copy
from datetime import datetime, timedelta, timezone
import unittest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_control_runtime as lifecycle
import test_control_host as hosting
import test_control_device as devices
import test_engine_adapter as adapters
from custom_components.docan_deye_ems.engine.arming import forecast_morning_soc, opening_guard
from custom_components.docan_deye_ems.engine.controller import Controller, household_reserve_requested
from custom_components.docan_deye_ems.engine.simulation import SimulatedPlant, example_snapshot
from custom_components.docan_deye_ems.engine.storage import MemoryState
from custom_components.docan_deye_ems.control_device import DeviceError, WriteDenied, UncertainWrite
from custom_components.docan_deye_ems.control_host import reported_plan
from custom_components.docan_deye_ems.docan import parse_frame

AT=datetime(2026,9,20,9,0,tzinfo=timezone.utc)
PIN={'for_date':str(AT.date()),'ceiling_pct':95,'export_clusters':[], 'reserve':{'pct':64}}


class ReservePolicyTests(unittest.TestCase):
    def test_opening_forecast_uses_physical_range_and_rejects_unreachable_override(self):
        f50,f90,_=forecast_morning_soc(51,50,54)
        self.assertEqual((f50,f90),(1,0))
        self.assertIn('PASS',opening_guard(f50,51,50,54,True))
        with self.assertRaises(SystemExit):opening_guard(15,51,50,54,True)
        self.assertEqual(forecast_morning_soc(5,20,30)[:2],(0,0))
        for values in ((True,0,0),(float('nan'),0,0),(50,float('inf'),60),(50,70,60),(101,0,0),(50,-1,5)):
            with self.subTest(values=values),self.assertRaises(ValueError):forecast_morning_soc(*values)
        for value in (float('nan'),float('inf'),True,-1,101):
            with self.subTest(value=value),self.assertRaises(ValueError):opening_guard(value,51,50,54,True)

    def controller(self,soc,at=AT,previous=None,**changes):
        frame=example_snapshot(at,soc=soc);frame.update(changes)
        plant=SimulatedPlant(frame);state=MemoryState({'state':previous or {}})
        policy=Controller(at,plant,state,PIN,allow_writes=True)
        return policy,plant,state

    def test_watch_hold_release_and_verified_hysteresis(self):
        policy,plant,state=self.controller(14)
        policy.tick(True)
        self.assertEqual(state.state['action'],'IDLE')
        self.assertTrue(state.state['reserve_watch'])
        self.assertEqual(plant.writes,[])
        plant.data['soc']=10;policy.tick(True)
        self.assertTrue(state.state['reserve_hold'])
        self.assertEqual(plant.data['programs'][1]['voltage'],55.2)
        self.assertFalse(plant.data['grid_charge_on'])
        plant.data['soc']=11;policy.tick(True)
        self.assertEqual(state.state['action'],'HOLD')
        plant.data['soc']=12;policy.tick(True)
        self.assertEqual(state.state['action'],'IDLE')
        self.assertEqual(plant.data['programs'][1]['voltage'],49)

    def test_legacy_shadow_future_and_malformed_history_do_not_hold_above_ten(self):
        valid={'at':AT.isoformat(),'action':'HOLD','converged':True,'shadow':False,
               'reserve_hold':True,'reserve_floor_pct':10}
        self.assertTrue(household_reserve_requested(11,valid,AT))
        for changes in ({'reserve_floor_pct':25},{'reserve_floor_pct':True},{'converged':False},
                        {'shadow':True},{'at':'bad'},{'at':(AT+timedelta(seconds=1)).isoformat()},
                        {'at':AT.replace(tzinfo=None).isoformat()},{'reserve_hold':False}):
            with self.subTest(changes=changes):
                self.assertFalse(household_reserve_requested(11,{**valid,**changes},AT))
                self.assertTrue(household_reserve_requested(10,{**valid,**changes},AT))

    def test_charge_wins_and_planner_failure_only_protects_low_reserve(self):
        policy,plant,state=self.controller(9,AT.replace(hour=13))
        policy.tick(True)
        self.assertEqual(state.state['action'],'charge')
        policy,plant,state=self.controller(9)
        with patch.object(policy,'contract_for_today',side_effect=ValueError):policy.tick(True)
        self.assertTrue(state.state['reserve_hold'])
        self.assertFalse(plant.data['grid_charge_on'])
        self.assertFalse(plant.data['solar_sell_on'])

    def test_stop_stale_and_hot_equipment_never_uses_reserve_override(self):
        for changes in ({'stop':True},{'truth_ready':False},{'ages':{'docan':301,'deye':0,'p1':0}},
                        {'temps':{**example_snapshot(AT)['temps'],'probe_1_temperature':46}}):
            policy,plant,state=self.controller(9,**changes)
            policy.tick(True)
            self.assertFalse(state.state.get('reserve_hold',False))
            self.assertNotIn(('program_1_voltage',55.2),plant.writes)


class ReserveNativeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=lifecycle.LifecycleTests.asyncSetUp
    asyncTearDown=lifecycle.LifecycleTests.asyncTearDown
    arm=lifecycle.LifecycleTests.arm

    async def low_reserve(self):
        self.battery['battery_soc']=10;self.peer.registers[588]=10
        self.battery['battery_power']=50;self.peer.registers[625]=1200
        await self.arm()

    def desired(self):
        return {i:{'time':t,'power':8000,'soc':95 if i==2 else 25,
                   'voltage':55.2 if i==2 else 49,'charging':'Grid' if i==2 else 'Disabled'}
                for i,t in enumerate(('01:00','12:15','18:15','23:15','23:30','23:45'),1)}

    async def test_missing_prices_and_pin_protect_native_equipment(self):
        await self.low_reserve()
        await self.session.tick(AT,None,None)
        self.assertTrue(self.store.state['reserve_hold'])
        self.assertEqual(self.peer.registers[160],5520)
        self.assertEqual(self.peer.registers[130],0)
        self.assertIsNone(self.store.latch)

    async def test_invalid_tomorrow_does_not_cancel_current_charge(self):
        await self.arm()
        for tomorrow in ([float('nan')]*96,[.2]*95,None):
            await self.session.tick(lifecycle.AT,example_snapshot(lifecycle.AT)['prices'],lifecycle.PIN,tomorrow)
            self.assertEqual(self.store.state['action'],'charge')
            self.assertIsNone(self.store.latch)

    async def test_native_controller_waits_for_bounded_voltage_readback(self):
        await self.low_reserve()
        original=self.device._request;sent=False;lag=2
        async def delayed(function,address,values,*args,**kwargs):
            nonlocal sent,lag
            result=await original(function,address,values,*args,**kwargs)
            if function==16 and address==160:sent=True
            if function==3 and address==160 and sent and lag:
                lag-=1;return [4900]
            return result
        with patch.object(self.device,'_request',side_effect=delayed):
            await self.session.tick(AT,None,None)
        self.assertTrue(self.store.state['reserve_hold'])
        self.assertIsNone(self.store.latch)
        self.assertEqual(self.peer.writes.count((160,5520)),1)

    async def test_household_import_programs_without_disabling_tou_or_exposing_reserve(self):
        await self.low_reserve()
        desired=self.desired()
        original=self.device.write;intermediate=[]
        async def observe_write(field,*args,**kwargs):
            result=await original(field,*args,**kwargs)
            values=[self.peer.registers[148+i] for i in range(6)]
            self.assertEqual(values,sorted(set(values)))
            self.assertEqual(self.peer.registers[146],255)
            if field.endswith('_time'):
                self.assertTrue(all(self.peer.registers[160+i]==5520 for i in range(6)))
            intermediate.append(field)
            return result
        with patch.object(self.device,'write',side_effect=observe_write):
            await self.session.apply_programs(AT,desired,str(AT.date()))
        self.assertEqual(self.session._proof_pause.await_count,2)
        self.assertEqual([self.peer.registers[160+i] for i in range(6)],[5520,5520,4900,4900,4900,4900])
        self.assertNotIn('tou',intermediate)
        self.assertEqual(self.peer.registers[130],0)
        self.assertEqual(self.store.get('programmed_day'),str(AT.date()))
        before=len(self.peer.writes)
        await self.session.apply_programs(AT,desired,str(AT.date()))
        self.assertEqual(len(self.peer.writes),before)

    async def test_actual_grid_charging_export_and_late_unsafe_proof_refuse(self):
        await self.low_reserve()
        for grid,battery in ((1200,-500),(-500,500)):
            self.peer.registers[625]=grid%65536;self.battery['battery_power']=battery
            with self.assertRaises(WriteDenied):await self.session.apply_programs(AT,self.desired(),str(AT.date()))
            self.assertEqual(self.peer.writes,[])
        self.peer.registers[625]=1200;self.battery['battery_power']=50
        async def becomes_unsafe():self.peer.registers[130]=1
        self.session._proof_pause=AsyncMock(side_effect=becomes_unsafe)
        with self.assertRaises(WriteDenied):await self.session.apply_programs(AT,self.desired(),str(AT.date()))
        self.assertEqual(self.peer.writes,[])

    async def test_stop_during_reserve_cleanup_never_permits_a_lowering(self):
        await self.low_reserve()
        original=self.device.write;stopped=False
        async def stop_before_cleanup(field,value,**kwargs):
            nonlocal stopped
            if field.endswith('_voltage') and value==49:
                stopped=True;self.store.set_latch({'why':'owner_requested_stop','at':AT.isoformat()})
            return await original(field,value,**kwargs)
        with patch.object(self.device,'write',side_effect=stop_before_cleanup):
            with self.assertRaises(WriteDenied):await self.session.apply_programs(AT,self.desired(),str(AT.date()))
        self.assertTrue(stopped)
        self.assertFalse(any(160<=r<=165 and v==4900 for r,v in self.peer.writes))
        self.assertIsNotNone(self.store.latch)

    async def test_unchanged_timetable_residual_hold_is_not_lowered_near_boundary(self):
        await self.low_reserve()
        desired=self.desired()
        for i,row in desired.items():
            self.peer.registers[147+i]=int(row['time'].replace(':',''))
            self.peer.registers[159+i]=5520
        at=AT.replace(hour=12,minute=14,second=59)
        clock=SimpleNamespace(monotonic=lambda:0.0,time=time.time,sleep=time.sleep)
        with patch('custom_components.docan_deye_ems.control_runtime.time',clock),\
             self.assertRaisesRegex(WriteDenied,'program_boundary_too_close'):
            await self.session.apply_programs(at,desired,str(at.date()))
        self.assertFalse(any(160<=r<=165 and v==4900 for r,v in self.peer.writes))
        self.assertIsNone(self.store.get('programmed_day'))

    async def test_cleanup_budgets_later_full_observations_before_unlock(self):
        await self.low_reserve()
        desired=self.desired()
        for i,row in desired.items():
            self.peer.registers[147+i]=int(row['time'].replace(':',''))
            self.peer.registers[159+i]=5520
        # Fifty seconds exceeds the old single-write margin (7.2 s here),
        # but not the remaining bounded full observations and cleanup writes.
        at=AT.replace(hour=18,minute=14,second=10)
        elapsed=[0.0];original=self.device.write
        async def cross_boundary(field,value,**kwargs):
            result=await original(field,value,**kwargs)
            if field=='program_3_voltage' and value==49:elapsed[0]+=60
            return result
        clock=SimpleNamespace(monotonic=lambda:elapsed[0],time=time.time,sleep=time.sleep)
        with patch('custom_components.docan_deye_ems.control_runtime.time',clock),\
             patch.object(self.device,'write',side_effect=cross_boundary),\
             self.assertRaisesRegex(WriteDenied,'program_boundary_too_close'):
            await self.session.apply_programs(at,desired,str(at.date()))
        self.assertFalse(any(160<=r<=165 and v==4900 for r,v in self.peer.writes))
        self.assertIsNone(self.store.get('programmed_day'))

    async def test_unchanged_schedule_crossing_a_boundary_protects_new_active_point(self):
        await self.low_reserve()
        desired=self.desired()
        for i,row in desired.items():
            self.peer.registers[147+i]=int(row['time'].replace(':',''))
            self.peer.registers[159+i]=int(row['voltage']*100)
        at=AT.replace(hour=18,minute=14,second=10)
        elapsed=[0.0];proofs=[0];original=self.session._programming_proof
        async def crossing(stamp):
            result=await original(stamp);proofs[0]+=1
            if proofs[0]==4:elapsed[0]=60
            return result
        clock=SimpleNamespace(monotonic=lambda:elapsed[0],time=time.time,sleep=time.sleep)
        with patch('custom_components.docan_deye_ems.control_runtime.time',clock),\
             patch.object(self.session,'_programming_proof',side_effect=crossing):
            await self.session.apply_programs(at,desired,str(at.date()))
        self.assertEqual(self.peer.registers[162],5520)
        self.assertEqual(self.peer.writes,[(162,5520)])
        self.assertEqual(self.store.get('programmed_day'),str(at.date()))


class ReserveHostTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=hosting.HostTests.asyncSetUp
    asyncTearDown=hosting.HostTests.asyncTearDown
    commission=hosting.HostTests.commission

    async def test_activation_uses_current_time_after_programming(self):
        await self.commission()
        original=self.host.apply_plan
        later=hosting.AT.replace(hour=13,minute=5)
        async def apply_and_advance(at,plan):
            await original(at,plan)
            self.host.now=lambda:later
        with patch.object(self.host,'apply_plan',side_effect=apply_and_advance):
            await self.host.command('enable_live',{})
        self.assertEqual(self.host.store.state['at'],later.isoformat())
        self.assertEqual(self.host.store.state['action'],'charge')
        self.assertEqual(self.peer.registers[130],1)

    async def test_price_failure_reaches_native_reserve_protection_only_after_activation(self):
        self.battery['battery_soc']=10;self.peer.registers[588]=10
        await self.host.tick(inputs_ready=False)
        self.assertEqual(self.peer.writes,[])
        await self.commission();await self.host.session.enable(hosting.AT)
        self.host.now=lambda:AT
        self.engine.today=None
        await self.host.tick(inputs_ready=False)
        self.assertTrue(self.host.store.state['reserve_hold'])
        self.assertTrue(self.host.store.get('active'))
        row=reported_plan({'windows':[{'action':'charge'}]},self.host.status())
        self.assertEqual(row['status'],'reserve_hold')
        self.assertEqual(row['windows'],[{'action':'charge'}])


class VoltageVerificationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=devices.DeviceTests.asyncSetUp
    asyncTearDown=devices.DeviceTests.asyncTearDown
    device=devices.DeviceTests.device

    async def test_delayed_voltage_readback_retries_reads_only(self):
        peer,device,authority,generation=await self.device()
        original=device._request;after_write=False;lag=2
        async def delayed(function,address,values,*args,**kwargs):
            nonlocal after_write,lag
            result=await original(function,address,values,*args,**kwargs)
            if function==16:after_write=True
            if function==3 and address==160 and after_write and lag:
                lag-=1;return [4900]
            return result
        with patch.object(device,'_request',side_effect=delayed),patch('custom_components.docan_deye_ems.control_device.asyncio.sleep',new=AsyncMock()):
            result=await device.write('program_1_voltage',55.2,authority=authority,generation=generation)
        self.assertEqual(result,55.2)
        self.assertEqual(peer.writes,[(160,5520)])

    async def test_unacknowledged_voltage_write_is_never_retried(self):
        peer,device,authority,generation=await self.device();peer.fault='lost_ack'
        with self.assertRaises(DeviceError):await device.write('program_1_voltage',55.2,authority=authority,generation=generation)
        self.assertEqual(peer.writes,[(160,5520)])


class ReserveCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=adapters.AdapterTests.asyncSetUp
    coordinator=adapters.AdapterTests.coordinator

    async def asyncTearDown(self):
        await adapters.AdapterTests.asyncTearDown(self)
        if hasattr(self,'peer'):await self.peer.close()

    async def test_missing_prices_coordinator_calls_native_reserve_then_owner_stop_wins(self):
        self.peer=await lifecycle.ControlPeer('modbus_tcp').start()
        self.peer.registers.update({588:10,625:100})
        self.settings.update(connection='direct_deye',equipment=self.peer.connection(),
                             battery={'source':'docan_usb','port':'/dev/ttyUSB0','address':0})
        self.settings['bindings']={'price_curve':self.settings['bindings']['price_curve']}
        coordinator=await self.coordinator()
        battery=parse_frame(devices.battery_frame(),0,controller=True)
        battery.update(battery_soc=10,battery_power=50)
        coordinator.control.observation.battery_reader=lambda _:copy.deepcopy(battery)
        coordinator.control.guards_factory=hosting.ReadyGuards
        preview=await coordinator.control.command('preview',{})
        await coordinator.control.command('confirm',{'nonce':preview['nonce'],'checks':dict.fromkeys(hosting.CHECKS,True)})
        await coordinator.control.session.enable(coordinator.control.now())
        self.hass.states.async_set('sensor.test_price_curve','unavailable')
        await coordinator.async_refresh()
        self.assertFalse(coordinator.data['ready'])
        self.assertIn('prices',coordinator.data['errors'])
        self.assertEqual(coordinator.data['plan']['status'],'reserve_hold')
        self.assertTrue(coordinator.control.store.state['reserve_hold'])
        self.assertEqual(self.peer.registers[130],0)
        coordinator.control.store.save('owner_stop',True)
        await coordinator.async_refresh()
        self.assertFalse(coordinator.control.store.state.get('reserve_hold',False))
        self.assertEqual(self.peer.registers[130],0)


if __name__=='__main__':unittest.main()
