"""Real socket tests for telemetry-only transports and failure boundaries."""
import asyncio
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.docan_deye_ems.equipment import (
    read_equipment, validate_connection, crc16, _read_request, decode_registers, BLOCKS)
from custom_components.docan_deye_ems.model import InputError
from equipment_peer import EquipmentPeer


class Equipment(unittest.IsolatedAsyncioTestCase):
    async def probe(self, protocol, fault=None, registers=None):
        peer = await EquipmentPeer(protocol).start()
        peer.fault = fault
        if registers:
            peer.registers.update(registers)
        try:
            value = await read_equipment({'transport': protocol, 'host': '127.0.0.1', 'port': peer.port, 'unit': 1, 'serial': 123456789})
            self.assertEqual(peer.requests, [(3, *b) for b in BLOCKS])
            return value
        finally:
            await peer.close()

    async def test_three_wire_protocols_create_measurements_from_zero_sensors(self):
        for protocol in ('modbus_tcp', 'modbus_rtu_tcp', 'solarman_v5'):
            with self.subTest(protocol=protocol):
                value = await self.probe(protocol)
                self.assertEqual(value['battery_soc'], 57)
                self.assertEqual(value['battery_voltage'], 52.1)
                self.assertEqual(value['solar_power'], 800)
                self.assertEqual(value['load_today'], 8.4)

    async def test_signed_charging_export_and_real_zero(self):
        value = await self.probe('modbus_tcp', registers={590: 65536-7509, 591: 65536-13620, 625: 65536-135, 672: 0, 673: 0})
        self.assertEqual(value['battery_power'], -7509)
        self.assertEqual(value['battery_current'], -136.2)
        self.assertEqual(value['grid_power'], -135)
        self.assertEqual(value['solar_power'], 0)

    async def test_corrupt_or_wrong_response_fails_closed(self):
        for protocol, fault in (('modbus_tcp', 'transaction'), ('solarman_v5', 'serial'), ('solarman_v5', 'crc'), ('modbus_rtu_tcp', 'crc'), ('modbus_tcp', 'truncate'), ('modbus_tcp', 'exception')):
            with self.subTest(protocol=protocol, fault=fault), self.assertRaises(InputError):
                await self.probe(protocol, fault)

    async def test_device_class_and_battery_sanity_checked(self):
        for registers in ({0: 3}, {588: 65535}, {587: 0}):
            with self.subTest(registers=registers), self.assertRaises(InputError):
                await self.probe('modbus_tcp', registers=registers)

    async def test_timeout_is_bounded_and_can_recover(self):
        with self.assertRaisesRegex(InputError, 'cannot_connect_equipment'):
            await self.probe('modbus_tcp', 'timeout')
        self.assertEqual((await self.probe('modbus_tcp'))['battery_soc'], 57)

    async def test_solarman_documented_double_crc(self):
        self.assertEqual((await self.probe('solarman_v5', 'double_crc'))['battery_soc'], 57)

    def test_only_fixed_telemetry_reads_can_be_constructed(self):
        for block in ((100, 1), (587, 100), (0, 20)):
            with self.assertRaises(InputError):
                _read_request(1, block)
        self.assertEqual(crc16(bytes.fromhex('01030000000a')).hex(), 'c5cd')

    def test_endpoint_shape_and_no_broadcast(self):
        base = {'transport': 'modbus_tcp', 'host': '127.0.0.1', 'port': 502, 'unit': 1}
        for change in ({'host': 'https://example.org'}, {'host': '8.8.8.8'}, {'host': '0.0.0.0'}, {'unit': 0}, {'unit': 1.5}, {'function': 6}, {'transport': 'solarman_v5'}):
            with self.subTest(change=change), self.assertRaises(InputError):
                validate_connection({**base, **change})


if __name__ == '__main__':
    unittest.main()
