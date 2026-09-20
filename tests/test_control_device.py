"""Actual wire requests against loopback devices, including uncertain writes."""
import asyncio
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from control_peer import ControlPeer
from custom_components.docan_deye_ems.control_device import DeyeDevice,LabAuthority,DeviceError,WriteDenied,UncertainWrite
from custom_components.docan_deye_ems.control_observation import EquipmentObservation
from custom_components.docan_deye_ems.docan import parse_frame,checksum
from custom_components.docan_deye_ems.model import InputError


def battery_frame(probes=4):
    data=bytearray(72);data[2]=55;data[3:5]=(5280).to_bytes(2,'big');data[5]=16
    for i in range(16):data[6+2*i:8+2*i]=(3300).to_bytes(2,'big')
    data[38:44]=b'\x00\xfa\x00\xfa\x01\x2c';data[44]=probes
    for i in range(probes):data[45+2*i:47+2*i]=(250+i).to_bytes(2,'big')
    offset=45+2*probes;data[offset:offset+2]=(10000).to_bytes(2,'big',signed=True)
    raw=data.hex().upper();n=len(raw);length=((-sum((n>>s)&15 for s in (8,4,0)))&15)<<12|n
    body=f'22004A00{length:04X}'+raw
    return ('~'+body+checksum(body)+'\r').encode()


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.peers=[]

    async def asyncTearDown(self):
        for peer in self.peers:
            await peer.close()
            self.assertEqual(peer.errors,[])

    async def device(self,protocol='modbus_tcp'):
        peer=await ControlPeer(protocol).start();self.peers.append(peer)
        device=DeyeDevice(peer.connection(),timeout=.15)
        authority=LabAuthority(peer.connection());generation=authority.arm()
        return peer,device,authority,generation

    async def test_full_reads_and_verified_writes_on_all_three_protocols(self):
        for protocol in ('modbus_tcp','modbus_rtu_tcp','solarman_v5'):
            peer,device,authority,generation=await self.device(protocol)
            observation=await device.observe()
            self.assertEqual(observation['fields']['program_2_time'],'12:00')
            self.assertEqual(observation['fields']['grid_export_w'],7900)
            self.assertEqual(observation['fields']['program_2_voltage'],55.2)
            self.assertTrue(observation['voltage_mode'])
            await device.write('grid_charge_a',160,authority=authority,generation=generation,expected=40)
            await device.write('grid_export_w',7000,authority=authority,generation=generation)
            await device.write('program_2_voltage',55.1,authority=authority,generation=generation)
            self.assertEqual(peer.writes,[(128,160),(231,700),(161,5510)])

    async def test_control_destination_cannot_be_a_physical_host(self):
        cfg={'transport':'modbus_tcp','host':'192.0.2.1','port':502,'unit':1}
        with patch('asyncio.open_connection',side_effect=AssertionError('No network')):
            with self.assertRaises(WriteDenied):LabAuthority(cfg)
            device=DeyeDevice(cfg)
            with self.assertRaises(WriteDenied):await device._request(16,130,(1,),lambda:None)

    async def test_field_bounds_precision_and_unknown_fields_send_no_writes(self):
        peer,d,a,g=await self.device()
        for key,value in (('grid_charge_a',161),('grid_charge_a',True),('export_w',10001),
                          ('grid_export_w',7055),('program_1_voltage',56),('program_1_time','24:00'),
                          ('program_1_soc',96),('program_1_charging','arbitrary'),('register_80',1)):
            with self.assertRaises(DeviceError):await d.write(key,value,authority=a,generation=g)
        self.assertEqual(peer.writes,[])

    async def test_revocation_and_endpoint_binding_are_checked(self):
        peer,d,a,g=await self.device();a.revoke()
        with self.assertRaises(WriteDenied):await d.write('grid_charge','on',authority=a,generation=g)
        peer2,d2,a2,g2=await self.device()
        with self.assertRaises(WriteDenied):await d2.write('grid_charge','on',authority=a,generation=g)
        with self.assertRaises(WriteDenied):await d.write('grid_charge','on',authority=a,generation=g,stop_only=True)
        peer.registers[130]=1
        await d.write('grid_charge','off',authority=a,generation=g,stop_only=True)
        self.assertEqual(peer.writes,[(130,0)])

    async def test_compare_before_write_and_weekday_mask_preservation(self):
        peer,d,a,g=await self.device()
        with self.assertRaisesRegex(DeviceError,'setting_changed_before_write'):
            await d.write('grid_charge_a',160,authority=a,generation=g,expected=50)
        await d.write('tou','Disabled',authority=a,generation=g)
        self.assertEqual(peer.registers[146],254)
        await d.write('tou','Enabled',authority=a,generation=g)
        self.assertEqual(peer.registers[146],255)

    async def test_no_repeat_after_effect_but_missing_or_wrong_ack(self):
        for protocol in ('modbus_tcp','modbus_rtu_tcp','solarman_v5'):
            for fault in ('lost_ack','wrong_echo','ignored_write'):
                peer,d,a,g=await self.device(protocol);peer.fault=fault
                with self.assertRaises(UncertainWrite):await d.write('grid_charge_a',160,authority=a,generation=g)
                self.assertEqual(peer.writes,[(128,160)])

    async def test_framing_and_connection_faults_discard_entire_observation(self):
        for protocol in ('modbus_tcp','modbus_rtu_tcp','solarman_v5'):
            faults=['disconnect','timeout','truncate','exception','wrong_unit']
            if protocol!='modbus_tcp':faults.append('bad_crc')
            if protocol!='modbus_rtu_tcp':faults.append('wrong_sequence')
            if protocol=='solarman_v5':faults.append('wrong_serial')
            for fault in faults:
                peer,d,a,g=await self.device(protocol);peer.fault=fault
                with self.assertRaises(DeviceError):await d.observe()
                self.assertEqual(peer.writes,[])
                peer.fault=None
                self.assertEqual((await d.observe())['alarm'],'OK')

    async def test_inverter_alarm_and_bms_alarm_are_not_defaulted_healthy(self):
        peer,d,a,g=await self.device();peer.registers[555]=1;peer.registers[220]=1
        result=await d.observe()
        self.assertEqual(result['alarm'],'FAULT');self.assertEqual(result['batt_alarm'],'on')

    async def test_complete_battery_temperatures_and_independent_truth_warmup(self):
        peer,d,a,g=await self.device()
        battery=parse_frame(battery_frame(),0,controller=True)
        self.assertEqual(battery['controller_temperatures']['mos_temperature'],30)
        self.assertEqual(battery['controller_temperatures']['probe_4_temperature'],25.3)
        obs=EquipmentObservation(d,{},battery_reader=lambda _:battery)
        at=datetime(2026,9,20,12,20,tzinfo=timezone.utc)
        self.assertFalse((await obs.read(at,owner_stop=False))['truth_ready'])
        self.assertFalse((await obs.read(at,owner_stop=False))['truth_ready'])
        self.assertTrue((await obs.read(at,owner_stop=False))['truth_ready'])
        with patch.object(obs,'battery_reader',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):await obs.read(at,owner_stop=False)
        self.assertFalse((await obs.read(at,owner_stop=False))['truth_ready'])
        peer.registers[111]=1
        self.assertFalse((await obs.read(at,owner_stop=False))['truth_ready'])

    async def test_missing_probe_and_invalid_programs_are_refused(self):
        with self.assertRaises(InputError):parse_frame(battery_frame(probes=2),0,controller=True)
        peer,d,a,g=await self.device();peer.registers[149]=2500
        with self.assertRaises(DeviceError):await d.observe()


if __name__=='__main__':unittest.main()
