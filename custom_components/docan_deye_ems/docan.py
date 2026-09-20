"""One fixed Docan telemetry query over USB/RS485; no command/write interface."""
from __future__ import annotations
import os
import re
import time
from .model import InputError

BATTERY_KEYS = ('battery_soc', 'battery_voltage', 'battery_current', 'battery_power',
                'battery_temperature', 'battery_cell_delta')


def validate_docan(value):
    if not isinstance(value, dict) or set(value)-{'source', 'port', 'address'}:
        raise InputError('invalid_battery_connection')
    if value.get('source') == 'inverter':
        if set(value) != {'source'}:
            raise InputError('invalid_battery_connection')
        return dict(value)
    if value.get('source') != 'docan_usb' or set(value) != {'source', 'port', 'address'}:
        raise InputError('invalid_battery_connection')
    port = value['port']
    if not isinstance(port, str) or not re.fullmatch(r'/dev/(?:serial/by-id/[A-Za-z0-9_.:-]+|ttyUSB\d+|ttyACM\d+)|COM[1-9]\d*', port) or '..' in port:
        raise InputError('invalid_battery_connection')
    if type(value['address']) is not int or not 0 <= value['address'] <= 15:
        raise InputError('invalid_battery_connection')
    return dict(value)


def checksum(body):
    return f'{(-sum(body.encode("ascii"))) & 65535:04X}'


def telemetry_query(address):
    if type(address) is not int or not 0 <= address <= 15:
        raise InputError('invalid_battery_connection')
    body = f'20{address:02X}4642E00201'
    return ('~'+body+checksum(body)+'\r').encode('ascii')


def parse_frame(frame, address, *, controller=False):
    """Accept only the bounded 16-cell VER22/CID1 4A profile, never raw identifiers."""
    try:
        if not 18 <= len(frame) <= 2048 or not frame.startswith(b'~') or not frame.endswith(b'\r'):
            raise ValueError
        text = frame[1:-1].decode('ascii')
        if not re.fullmatch('[0-9A-F]+', text):
            raise ValueError
        body, check = text[:-4], text[-4:]
        if checksum(body) != check or body[:8] != f'22{address:02X}4A00':
            raise ValueError
        length = int(body[8:12], 16)
        size = length & 4095
        if size != len(body[12:]) or size % 2 or length >> 12 != -((size>>8 & 15)+(size>>4 & 15)+(size & 15)) & 15:
            raise ValueError
        data = bytes.fromhex(body[12:])
        if len(data) < 58 or data[5] != 16:
            raise ValueError
        unsigned = lambda offset: int.from_bytes(data[offset:offset+2], 'big')
        soc, voltage = data[2], unsigned(3)/100
        cells = [unsigned(6+2*i)/1000 for i in range(16)]
        pos = 38
        temp_count = data[pos+6]
        after = pos+7+2*temp_count
        if temp_count > 16 or after+13 > len(data):
            raise ValueError
        # Current is the FIRST two bytes after probes; the NEXT field is pack
        # resistance. They must never be confused. Positive on wire = charging.
        current = -int.from_bytes(data[after:after+2], 'big', signed=True)/100
        temperature = unsigned(pos)/10
        if not 0 <= soc <= 100 or not 40 <= voltage <= 65 or abs(current) > 250:
            raise ValueError
        if any(not 1.5 <= v <= 4.5 for v in cells) or not -40 <= temperature <= 100:
            raise ValueError
        result = {'battery_soc': soc, 'battery_voltage': voltage, 'battery_current': current,
                'battery_power': voltage*current, 'battery_temperature': temperature,
                'battery_cell_delta': max(cells)-min(cells)}
        if controller:
            temps = {'environment_temperature': temperature,
                     'mos_temperature': unsigned(pos+4)/10,
                     **{f'probe_{i+1}_temperature': unsigned(pos+7+2*i)/10 for i in range(temp_count)}}
            if temp_count != 4 or any(not -40 <= t <= 120 for t in temps.values()):
                raise ValueError
            result.update(controller_temperatures=temps, cell_voltage_sum=sum(cells),
                          cell_count=16, probe_count=temp_count)
        return result
    except (UnicodeError, ValueError, IndexError, TypeError):
        raise InputError('invalid_battery_frame') from None


def _read_docan(settings, controller=False):
    """Blocking bounded read for HA's executor. Only a fixed telemetry query is sent."""
    import serial
    cfg = validate_docan(settings)
    if cfg['source'] != 'docan_usb':
        raise InputError('invalid_battery_connection')
    try:
        # No serial URLs, scanning, arbitrary messages or live reconfiguration.
        options = {'exclusive': True} if os.name != 'nt' else {}
        with serial.Serial(cfg['port'], baudrate=9600, bytesize=8, parity='N', stopbits=1,
                           timeout=0.1, write_timeout=1, **options) as port:
            port.reset_input_buffer()
            port.write(telemetry_query(cfg['address']))
            deadline, frame = time.monotonic()+2, bytearray()
            while time.monotonic() < deadline and len(frame) <= 2048:
                frame.extend(port.read(1))
                if frame.endswith(b'\r'):
                    return parse_frame(bytes(frame), cfg['address'], controller=controller)
        raise InputError('battery_no_response')
    except (OSError, serial.SerialException):
        raise InputError('battery_connection_failed') from None


def read_docan(settings):
    return _read_docan(settings)


def read_docan_control(settings):
    """Read all six thermal guards; unsupported probe layouts remain unavailable."""
    return _read_docan(settings, controller=True)
