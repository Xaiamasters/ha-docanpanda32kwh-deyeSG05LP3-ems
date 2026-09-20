"""Bounded Deye control transport with device-bound installation authority.

Register facts and validation status are recorded in CONTROL_ADAPTER.md. No
caller can supply a register address to write. Physical writes require a current
commissioning record, armed session, healthy guards and matching device identity.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import math
import hashlib
import secrets
import struct
import time

from .equipment import crc16, validate_connection, BLOCKS, decode_registers
from .control_store import DeviceLease


class DeviceError(RuntimeError):
    """A redacted transport or profile error."""


class UncertainWrite(DeviceError):
    """A request may have taken effect; do not retry the command."""


class WriteDenied(DeviceError):
    """The caller lacks current command authority."""


@dataclass(frozen=True)
class Field:
    register: int
    scale: float = 1
    low: float = 0
    high: float = 1
    choices: tuple = ()
    clock: bool = False
    mask: int | None = None

    def decode(self, raw):
        if type(raw) is not int or not 0 <= raw <= 65535:
            raise DeviceError('invalid_register_word')
        value = raw if self.mask is None else raw & self.mask
        if self.clock:
            if value // 100 > 23 or value % 100 > 59:
                raise DeviceError('invalid_program_time')
            return f'{value // 100:02d}:{value % 100:02d}'
        if self.choices:
            if value >= len(self.choices):
                raise DeviceError('unsupported_setting_value')
            return self.choices[value]
        return value * self.scale

    def encode(self, value, before):
        if self.clock:
            if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
                raise DeviceError('invalid_program_time')
            try:
                hour, minute = map(int, value.split(':'))
            except ValueError:
                raise DeviceError('invalid_program_time') from None
            if not 0 <= hour < 24 or not 0 <= minute < 60:
                raise DeviceError('invalid_program_time')
            raw = hour * 100 + minute
        elif self.choices:
            if value not in self.choices:
                raise DeviceError('unsupported_setting_value')
            raw = self.choices.index(value)
        else:
            if type(value) not in (int, float) or not math.isfinite(value) or not self.low <= value <= self.high:
                raise DeviceError('setting_out_of_bounds')
            scaled = value / self.scale
            raw = round(scaled)
            if not math.isclose(scaled, raw, abs_tol=1e-7, rel_tol=0):
                raise DeviceError('setting_resolution')
        return raw if self.mask is None else (before & ~self.mask) | raw


FIELDS = {
    'grid_charge': Field(130, choices=('off', 'on')),
    'grid_charge_a': Field(128, high=160),
    'energy_pattern': Field(141, choices=('Battery First', 'Load First')),
    'work_mode': Field(142, choices=('Export First', 'Zero Export To Load', 'Zero Export To CT')),
    'export_w': Field(143, high=10000),
    'solar_sell': Field(145, choices=('off', 'on')),
    'grid_export_w': Field(231, scale=10, high=10000),
    'tou': Field(146, choices=('Disabled', 'Enabled'), mask=1),
}
for _n in range(1, 7):
    FIELDS[f'program_{_n}_time'] = Field(147 + _n, clock=True)
    FIELDS[f'program_{_n}_power'] = Field(153 + _n, high=10000)
    FIELDS[f'program_{_n}_voltage'] = Field(159 + _n, scale=.01, low=48, high=55.2)
    FIELDS[f'program_{_n}_soc'] = Field(165 + _n, high=95)
    FIELDS[f'program_{_n}_charging'] = Field(171 + _n, choices=('Disabled', 'Grid', 'Generator', 'Both'))

READ_BLOCKS = ((0, 1), (111, 1), (128, 1), (130, 1), (141, 6), (148, 30),
               (220, 1), (231, 1), (553, 6), (587, 5), (625, 1))
READ_BLOCKS += tuple(block for block in BLOCKS if block not in READ_BLOCKS)
READABLE = frozenset(r for first, count in READ_BLOCKS for r in range(first, first + count)) | frozenset(range(23))
STOP_VALUES = {'solar_sell': 'off', 'grid_charge': 'off',
               'work_mode': 'Zero Export To CT', 'energy_pattern': 'Load First'}


class LabAuthority:
    """Revocable per-session authority; never constructible for a physical host.

    Revocation is checked again immediately before bytes are sent, after waiting
    for the wire lock. Stop commands remain possible after revocation.
    """
    def __init__(self, connection):
        cfg = validate_connection(connection)
        if not ipaddress.ip_address(cfg['host']).is_loopback:
            raise WriteDenied('physical_commissioning_not_available')
        self.identity = tuple(sorted(cfg.items()))
        self.enabled = False
        self.generation = 0

    def arm(self):
        self.generation += 1
        self.enabled = True
        return self.generation

    def revoke(self):
        self.enabled = False
        self.generation += 1

    def check(self, cfg, field, value, generation, stop_only=False):
        if tuple(sorted(cfg.items())) != self.identity:
            raise WriteDenied('authority_endpoint_mismatch')
        if stop_only:
            if field not in STOP_VALUES or STOP_VALUES[field] != value:
                raise WriteDenied('stop_writer_cannot_activate')
        elif not self.enabled or generation != self.generation:
            raise WriteDenied('command_authority_revoked')


def device_identity(words):
    """Hash stable identity registers; never expose the inverter serial."""
    if len(words)!=23 or words[0] not in (5,1280):raise DeviceError('unsupported_inverter_profile')
    rating=((words[20]<<16)|words[21])/10
    if not 1000<=rating<=30000 or not any(words[3:8]):raise DeviceError('device_identity_unavailable')
    stable=tuple(words[i] for i in (0,2,3,4,5,6,7,10,11,13,14,15,16,17,20,21))
    return {'digest':hashlib.sha256(struct.pack('>'+'H'*len(stable),*stable)).hexdigest(),
            'rated_power_w':rating,'firmware_words':[words[i] for i in (10,11,13,14,15,16,17)]}


class CommissionedAuthority(LabAuthority):
    """A device-bound approval created by the authenticated commissioning flow.

    The record contains no reusable network credentials. The device identity is
    read again on the same connection before each command, including STOP.
    """
    def __init__(self, connection, store, context):
        cfg=validate_connection(connection)
        record=store.get('commissioning')
        if (not isinstance(record,dict) or record.get('context')!=context
            or record.get('connection')!=cfg or not record.get('id')
            or not record.get('checks_confirmed') or not isinstance(record.get('device'),dict)):
            raise WriteDenied('commissioning_required')
        self.identity=tuple(sorted(cfg.items()))
        self.enabled=False;self.generation=0
        self.store=store;self.record=record;self.context=context

    def verify_identity(self, observed):
        if observed!=self.record['device']:
            self.revoke()
            raise WriteDenied('commissioned_device_changed')

    def check(self, cfg, field, value, generation, stop_only=False):
        super().check(cfg,field,value,generation,stop_only)
        if stop_only:return
        current=self.store.get('commissioning')
        if (not current or current.get('id')!=self.record['id'] or current.get('context')!=self.context
            or self.store.latch is not None or not self.store.get('active',False) or self.store.get('owner_stop',False)):
            self.revoke();raise WriteDenied('commissioned_authority_revoked')
        if any(not 0<=time.time()-self.store.get('watchdog_'+role,0)<=75
               for role in ('charge','export','auditor','supervisor')):
            self.revoke();raise WriteDenied('watchdogs_not_ready')
        limit=self.record['limits']
        if field=='grid_charge_a' and value>limit['charge_current_a']:raise WriteDenied('installation_current_limit')
        if field in ('export_w','grid_export_w') and value>self.record['export_power_w']:raise WriteDenied('installation_export_limit')
        if field=='solar_sell' and value=='on' and not limit['allow_export']:raise WriteDenied('export_not_enabled')
        if field.startswith('program_'):
            suffix=field.rsplit('_',1)[1]
            if suffix=='power' and value>limit['program_power_w']:raise WriteDenied('installation_power_limit')
            if suffix=='voltage' and not limit['idle_voltage']<=value<=limit['charge_voltage']:raise WriteDenied('installation_voltage_limit')
            if suffix=='soc' and not limit['minimum_soc']<=value<=limit['max_soc']:raise WriteDenied('installation_soc_limit')
            if suffix=='charging' and value not in ('Grid','Disabled'):raise WriteDenied('unsupported_charge_source')


class DeyeDevice:
    """Read the complete inverter frame and write only named, bounded fields."""
    def __init__(self, connection, *, timeout=2.0):
        self.connection = validate_connection(connection)
        if not .02 <= timeout <= 5:
            raise ValueError('invalid_timeout')
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._sequence = secrets.randbelow(200) + 1
        self.wire_lock = None

    async def _take_wire(self):
        if self.wire_lock is None:
            return None
        while True:
            try:
                return DeviceLease(self.wire_lock)
            except RuntimeError:
                await asyncio.sleep(.01)

    def _next_sequence(self):
        self._sequence = self._sequence % 250 + 1
        return self._sequence

    async def _exchange(self, reader, writer, cfg, function, address, values, guard, attempt):
        seq = self._next_sequence()
        if function == 3:
            request = struct.pack('>BBHH', cfg['unit'], 3, address, values)
        else:
            request = struct.pack('>BBHHBH', cfg['unit'], 16, address, 1, 2, values[0])
            guard()
        if cfg['transport'] == 'modbus_tcp':
            packet = struct.pack('>HHH', seq, 0, len(request)) + request
        else:
            rtu = request + crc16(request)
            if cfg['transport'] == 'modbus_rtu_tcp':
                packet = rtu
            else:
                payload = b'\x02' + bytes(14) + rtu
                packet = struct.pack('<BHHHI', 165, len(payload), 0x4510, seq, cfg['serial']) + payload
                packet += bytes((sum(packet[1:]) & 255, 21))
        if function == 16:
            guard()
        if function == 16:attempt['sent']=True
        writer.write(packet)
        await writer.drain()
        if cfg['transport'] == 'modbus_tcp':
            header = await reader.readexactly(7)
            tid, protocol, length, unit = struct.unpack('>HHHB', header)
            if tid != seq or protocol != 0 or unit != cfg['unit'] or not 3 <= length <= 253:
                raise DeviceError('invalid_response_identity')
            body = bytes((unit,)) + await reader.readexactly(length - 1)
        elif cfg['transport'] == 'solarman_v5':
            header = await reader.readexactly(11)
            start, length, code, reply_seq, serial = struct.unpack('<BHHHI', header)
            if start != 165 or not 19 <= length <= 271 or code != 0x1510 or reply_seq & 255 != seq or serial != cfg['serial']:
                raise DeviceError('invalid_response_identity')
            tail = await reader.readexactly(length + 2)
            if tail[-1] != 21 or (sum(header[1:]) + sum(tail[:-2])) & 255 != tail[-2] or tail[:2] != b'\x02\x01':
                raise DeviceError('invalid_gateway_frame')
            body = self._rtu(tail[14:-2], function, values)
        else:
            head = await reader.readexactly(3)
            if head[1] == function | 128:
                rest = 2
            elif function == 3 and head[1] == 3 and head[2] == values * 2:
                rest = head[2] + 2
            elif function == 16 and head[1] == 16:
                rest = 5
            else:
                raise DeviceError('invalid_response_function')
            body = self._rtu(head + await reader.readexactly(rest), function, values)
        if body[:2] == bytes((cfg['unit'], function | 128)) and len(body) == 3:
            raise DeviceError('device_rejected_request')
        if function == 3:
            if len(body) != 3 + values * 2 or body[:3] != bytes((cfg['unit'], 3, values * 2)):
                raise DeviceError('invalid_read_response')
            return struct.unpack('>' + 'H' * values, body[3:])
        if body != struct.pack('>BBHH', cfg['unit'], 16, address, 1):
            raise DeviceError('invalid_write_echo')
        return None

    async def _request(self, function, address, values, guard=None, authority=None):
        if function == 3:
            if type(values) is not int or not 1 <= values <= 32 or any(r not in READABLE for r in range(address,address+values)):
                raise DeviceError('unsupported_read')
        elif (function != 16 or guard is None or len(values) != 1
              or address not in {f.register for f in FIELDS.values()}):
            raise WriteDenied('unsupported_write')
        if function == 16 and not ipaddress.ip_address(self.connection['host']).is_loopback and not isinstance(authority,CommissionedAuthority):
            raise WriteDenied('commissioning_required')
        writer = lease = None
        attempt={'sent':False}
        try:
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    lease=await self._take_wire()
                    cfg=self.connection
                    reader,writer=await asyncio.open_connection(cfg['host'],cfg['port'])
                    if function == 16 and isinstance(authority,CommissionedAuthority):
                        words=await self._exchange(reader,writer,cfg,3,0,23,None,attempt)
                        authority.verify_identity(device_identity(words))
                    return await self._exchange(reader,writer,cfg,function,address,values,guard,attempt)
        except asyncio.CancelledError:
            raise
        except (OSError,TimeoutError,asyncio.IncompleteReadError,DeviceError) as exc:
            if function == 16 and attempt['sent']:raise UncertainWrite('write_outcome_unknown') from None
            if isinstance(exc,WriteDenied):raise
            raise DeviceError('device_request_failed') from None
        finally:
            try:
                if writer is not None:
                    writer.close()
                    try:
                        async with asyncio.timeout(.2):await writer.wait_closed()
                    except (OSError,TimeoutError):pass
            finally:
                if lease is not None:lease.close()

    @staticmethod
    def _rtu(raw, function, values):
        expected = 5 if len(raw) > 1 and raw[1] == function | 128 else 5 + values * 2 if function == 3 else 8
        if len(raw) == expected + 2 and raw[-2:] == b'\x00\x00':
            raw = raw[:-2]
        if len(raw) != expected or crc16(raw[:-2]) != raw[-2:]:
            raise DeviceError('invalid_crc')
        return raw[:-2]

    async def read(self, field):
        spec = FIELDS[field]
        raw = (await self._request(3, spec.register, 1))[0]
        return spec.decode(raw)

    async def identify(self):
        return device_identity(await self._request(3,0,23))

    async def write(self, field, value, *, authority, generation, expected=None, stop_only=False):
        if field not in FIELDS:
            raise WriteDenied('unknown_control_field')
        authority.check(self.connection, field, value, generation, stop_only)
        spec = FIELDS[field]
        raw = (await self._request(3, spec.register, 1))[0]
        observed = spec.decode(raw)
        if expected is not None and observed != expected:
            raise DeviceError('setting_changed_before_write')
        encoded = spec.encode(value, raw)
        if encoded == raw:
            return observed
        guard = lambda: authority.check(self.connection, field, value, generation, stop_only)
        await self._request(16, spec.register, (encoded,), guard, authority)
        # A write is never retried, including a successful echo with failed readback.
        try:
            after = (await self._request(3, spec.register, 1))[0]
            if after != encoded:
                raise DeviceError('write_readback_mismatch')
            return spec.decode(after)
        except DeviceError:
            raise UncertainWrite('write_readback_unverified') from None

    async def observe(self):
        registers = {}
        begin = time.monotonic()
        async with asyncio.timeout(self.timeout * len(READ_BLOCKS)):
            for block in READ_BLOCKS:
                words = await self._request(3, *block)
                registers.update({block[0] + i: raw for i, raw in enumerate(words)})
                if block[0] == 0 and registers[0] not in (5, 1280):
                    raise DeviceError('unsupported_inverter_profile')
                if self.wire_lock is not None:
                    # Hand ownership to queued workers between telemetry blocks;
                    # a long observation must not starve a STOP or another guard.
                    await asyncio.sleep(.01)
        fields = {key: spec.decode(registers[spec.register]) for key, spec in FIELDS.items()}
        signed = lambda n: n - 65536 if n & 32768 else n
        return {'fields': fields, 'battery_soc': registers[588], 'battery_voltage': registers[587] / 100,
                'battery_power': signed(registers[590]), 'p1_w': signed(registers[625]),
                'alarm': 'OK' if not any(registers[r] for r in range(553, 559)) else 'FAULT',
                'batt_alarm': 'off' if registers[220] == 0 else 'on',
                'voltage_mode': registers[111] == 0,
                'tou_today_mask': registers[146], 'age_s': time.monotonic() - begin,
                'observed_monotonic': begin, 'measurements':decode_registers(registers)}
