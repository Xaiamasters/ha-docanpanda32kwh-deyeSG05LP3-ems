"""Assemble complete controller observations from independently timed sources."""
from __future__ import annotations

import asyncio
from datetime import datetime
import math
import time

from .control_device import DeviceError
from .control_store import DeviceLease
from .docan import read_docan_control
from .docan import BATTERY_KEYS
from .engine.runtime import validate_snapshot


class BatteryTruth:
    """Require repeated coherent frames; stale or failed reads reset admission."""
    def __init__(self):
        self.last = None
        self.consecutive = 0

    def reset(self):
        self.last = None
        self.consecutive = 0

    def accept(self, battery, now):
        try:
            soc, volts = battery['battery_soc'], battery['battery_voltage']
            numbers = (soc, volts, battery['cell_voltage_sum'], battery['battery_cell_delta'])
            valid = all(type(x) in (int, float) and math.isfinite(x) for x in numbers)
            valid = (valid and 0 <= soc <= 100 and 40 <= volts <= 60
                     and abs(volts-battery['cell_voltage_sum']) <= 1
                     and battery['battery_cell_delta'] <= .3
                     and battery['cell_count'] == 16 and battery['probe_count'] == 4)
            if self.last:
                elapsed = now-self.last[0]
                # Pack energy cannot jump several percent in a polling interval.
                valid = valid and 0 < elapsed <= 300 and abs(soc-self.last[1]) <= max(2, elapsed/60)
        except (KeyError, TypeError):
            valid = False
        if not valid:
            self.reset()
            return False
        self.last = (now, soc)
        self.consecutive += 1
        return self.consecutive >= 3


class EquipmentObservation:
    """One inverter and one independent battery reader, with no cached fallbacks."""
    def __init__(self, device, battery_config, *, battery_reader=read_docan_control, monotonic=time.monotonic):
        self.device = device
        self.battery_config = battery_config
        self.battery_reader = battery_reader
        self.monotonic = monotonic
        self.truth = BatteryTruth()
        self.measurements = None
        self.source_lock = None

    async def _battery(self):
        lease=None
        task=None
        try:
            async with asyncio.timeout(4):
                if self.source_lock:
                    while lease is None:
                        try:lease=DeviceLease(self.source_lock)
                        except RuntimeError:await asyncio.sleep(.01)
                task=asyncio.create_task(asyncio.to_thread(self.battery_reader,self.battery_config))
                return await asyncio.shield(task)
        finally:
            if task and not task.done():
                # Cancelling a coroutine cannot stop an already running serial
                # read. Keep ownership until that bounded reader has exited.
                def release(done):
                    if lease:lease.close()
                    if not done.cancelled():done.exception()
                task.add_done_callback(release)
            elif lease:lease.close()

    async def read(self, at: datetime, *, owner_stop, pause_charge=False, discharge_now=False):
        if at.tzinfo is None or any(type(x) is not bool for x in (owner_stop, pause_charge, discharge_now)):
            raise DeviceError('invalid_observation_context')
        battery_begin = self.monotonic()
        self.measurements = None
        try:
            battery = await self._battery()
            inverter = await self.device.observe()
            finished = self.monotonic()
            truth_ready = self.truth.accept(battery, finished)
            fields = inverter['fields']
            programs = {i: {k: fields[f'program_{i}_{k}'] for k in ('time','charging','soc','power','voltage')}
                        for i in range(1, 7)}
            starts = [programs[i]['time'] for i in range(1, 7)]
            if len(set(starts)) != 6 or starts != sorted(starts):
                raise DeviceError('invalid_program_order')
            mask = inverter['tou_today_mask']
            enabled_today = bool(mask & 1 and mask & (2 << at.weekday()))
            frame = {'soc': battery['battery_soc'], 'pack_v': battery['battery_voltage'],
                     'p1_w': inverter['p1_w'], 'grid_charge_on': fields['grid_charge']=='on',
                     'solar_sell_on': fields['solar_sell']=='on', 'grid_charge_a': fields['grid_charge_a'],
                     'export_w': fields['export_w'], 'grid_export_w': fields['grid_export_w'],
                     'tou': fields['tou'], 'work_mode': fields['work_mode'],
                     'energy_pattern': fields['energy_pattern'], 'alarm': inverter['alarm'],
                     'batt_alarm': inverter['batt_alarm'], 'programs': programs,
                     'temps': battery['controller_temperatures'], 'docan_fresh': finished-battery_begin <= 300,
                     'truth_ready': truth_ready and inverter['voltage_mode'] and enabled_today,
                     'ages': {'docan': finished-battery_begin, 'deye': inverter['age_s'], 'p1': inverter['age_s']},
                     'stop': owner_stop, 'pause_charge': pause_charge, 'discharge_now': discharge_now}
            result = validate_snapshot(frame)
            result.update(at=at.isoformat(), battery_power=battery['battery_power'],
                          voltage_mode=inverter['voltage_mode'], tou_enabled_today=enabled_today)
            self.measurements = {**inverter['measurements'],**{k:battery[k] for k in BATTERY_KEYS}}
            return result
        except BaseException:
            self.truth.reset()
            raise
