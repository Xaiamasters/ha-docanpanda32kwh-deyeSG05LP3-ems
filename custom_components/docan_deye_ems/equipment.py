"""Bounded Deye LV three-phase telemetry reader. Only Modbus function 03 exists.

No generic transaction interface, register editor, write function or service.
Protocol/register references and limitations: EQUIPMENT_CONNECTION.md.
"""
from __future__ import annotations

import asyncio
import ipaddress
import secrets
import struct

from .model import InputError

TRANSPORTS = ('solarman_v5', 'modbus_tcp', 'modbus_rtu_tcp')
# Fixed telemetry reads: device class, battery, grid, inverter, load, MPPTs, totals.
BLOCKS = ((0, 1), (587, 5), (625, 1), (636, 1), (653, 1), (672, 2),
          (520, 2), (526, 1), (529, 1))
DIRECT_KEYS = ('battery_soc', 'battery_power', 'battery_voltage', 'battery_current',
               'inverter_power', 'load_power', 'grid_power', 'solar_power',
               'solar_today', 'load_today', 'import_today', 'export_today')


def validate_connection(value):
    """Accept only a user-entered local unicast IP and explicit wire protocol."""
    try:
        if not isinstance(value, dict) or set(value) - {'transport', 'host', 'port', 'unit', 'serial'}:
            raise ValueError
        d = dict(value)
        ip = ipaddress.ip_address(d['host'])
        if not ip.is_private or ip.is_multicast or ip.is_unspecified or ip.is_link_local or ip.is_reserved:
            raise ValueError
        if d['transport'] not in TRANSPORTS:
            raise ValueError
        for key, high in (('port', 65535), ('unit', 247)):
            if type(d[key]) is not int or not 1 <= d[key] <= high:
                raise ValueError
        if d['transport'] == 'solarman_v5':
            if type(d.get('serial')) is not int or not 1 <= d['serial'] <= 0xFFFFFFFF:
                raise ValueError
        else:
            d.pop('serial', None)
        d['host'] = str(ip)
        return d
    except (KeyError, ValueError, TypeError):
        raise InputError('invalid_connection') from None


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return struct.pack('<H', crc)


def _read_request(unit, block):
    if block not in BLOCKS or type(unit) is not int or not 1 <= unit <= 247:
        raise InputError('unsupported_read')
    return struct.pack('>BBHH', unit, 3, *block)


def _registers(body, unit, count):
    if len(body) == 3 and body[:2] == bytes((unit, 0x83)):
        raise InputError('equipment_rejected_read')
    if len(body) != 3 + 2 * count or body[:3] != bytes((unit, 3, count * 2)):
        raise InputError('invalid_equipment_response')
    return struct.unpack('>' + 'H' * count, body[3:])


def _rtu_body(frame, unit, count):
    expected = 5 if len(frame) >= 2 and frame[1] == 0x83 else 5 + 2 * count
    # Some Deye loggers append a second CRC (two zeros) to an already valid RTU.
    if len(frame) == expected + 2 and frame[-2:] == b'\0\0':
        frame = frame[:-2]
    if len(frame) != expected or crc16(frame[:-2]) != frame[-2:]:
        raise InputError('invalid_equipment_response')
    return _registers(frame[:-2], unit, count)


async def _exchange(reader, writer, d, block, sequence):
    raw = _read_request(d['unit'], block)
    count = block[1]
    if d['transport'] == 'modbus_tcp':
        writer.write(struct.pack('>HHH', sequence, 0, len(raw)) + raw)
        await writer.drain()
        head = await reader.readexactly(7)
        tid, protocol, size, unit = struct.unpack('>HHHB', head)
        if tid != sequence or protocol != 0 or unit != d['unit'] or not 3 <= size <= 253:
            raise InputError('invalid_equipment_response')
        return _registers(bytes((unit,)) + await reader.readexactly(size - 1), unit, count)
    rtu = raw + crc16(raw)
    if d['transport'] == 'modbus_rtu_tcp':
        writer.write(rtu)
        await writer.drain()
        head = await reader.readexactly(3)
        if head[0] != d['unit'] or head[1] not in (3, 0x83):
            raise InputError('invalid_equipment_response')
        if head[1] == 3 and head[2] != count * 2:
            raise InputError('invalid_equipment_response')
        return _rtu_body(head + await reader.readexactly(2 if head[1] == 0x83 else head[2] + 2), d['unit'], count)
    payload = b'\x02' + bytes(14) + rtu
    header = struct.pack('<BHHHI', 0xA5, len(payload), 0x4510, sequence, d['serial'])
    packet = header + payload
    writer.write(packet + bytes((sum(packet[1:]) & 255, 0x15)))
    await writer.drain()
    head = await reader.readexactly(11)
    start, size, control, reply_sequence, serial = struct.unpack('<BHHHI', head)
    if (start != 0xA5 or not 19 <= size <= 271 or control != 0x1510
            or reply_sequence & 255 != sequence & 255 or serial != d['serial']):
        raise InputError('invalid_equipment_response')
    tail = await reader.readexactly(size + 2)
    if tail[-1] != 0x15 or (sum(head[1:]) + sum(tail[:-2])) & 255 != tail[-2] or tail[:2] != b'\x02\x01':
        raise InputError('invalid_equipment_response')
    return _rtu_body(tail[14:-2], d['unit'], count)


def decode_registers(registers):
    """The conservative, published 16-bit LV profile; reject other device classes."""
    r = registers
    if r[0] not in (5, 0x0500):
        raise InputError('unsupported_equipment')
    signed = lambda value: value - 65536 if value & 0x8000 else value
    if not 0 <= r[588] <= 100 or not 4000 <= r[587] <= 6500:
        raise InputError('invalid_equipment_readings')
    values = {'battery_soc': r[588], 'battery_voltage': r[587] / 100,
              'battery_power': signed(r[590]), 'battery_current': signed(r[591]) / 100,
              'grid_power': signed(r[625]), 'inverter_power': signed(r[636]),
              'load_power': r[653], 'solar_power': r[672] + r[673]}
    if r[653] == 65535 or r[672] == 65535 or r[673] == 65535:
        raise InputError('invalid_equipment_readings')
    for key, reg in (('import_today', 520), ('export_today', 521), ('load_today', 526), ('solar_today', 529)):
        values[key] = None if r[reg] == 65535 else r[reg] / 10
    return values


async def read_equipment(connection):
    """One bounded transaction session, closed even on timeout or cancellation."""
    d = validate_connection(connection)
    writer = None
    try:
        async with asyncio.timeout(15):
            reader, writer = await asyncio.open_connection(d['host'], d['port'])
            registers = {}
            sequence = secrets.randbelow(200) + 1
            for index, block in enumerate(BLOCKS):
                async with asyncio.timeout(3):
                    result = await _exchange(reader, writer, d, block, sequence + index)
                registers.update({block[0] + i: value for i, value in enumerate(result)})
                if index == 0 and registers[0] not in (5, 0x0500):
                    raise InputError('unsupported_equipment')
                await asyncio.sleep(0.05)
            return decode_registers(registers)
    except (OSError, TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        # Never expose host, serial, packet contents or connection exception text.
        raise InputError('cannot_connect_equipment') from None
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass


def direct_keys(settings):
    if settings.get('connection') != 'direct_deye':
        return ()
    return tuple(k for k in DIRECT_KEYS if not (k.startswith('solar_') and settings['solar']['connection'] != 'deye_dc'))
