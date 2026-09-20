"""In-memory plant adapter. It has no network or hardware access."""
from __future__ import annotations
import copy

FIELDS = {'grid_charge': 'grid_charge_on', 'grid_charge_a': 'grid_charge_a',
          'work_mode': 'work_mode', 'solar_sell': 'solar_sell_on',
          'energy_pattern': 'energy_pattern', 'export_w': 'export_w',
          'grid_export_w': 'grid_export_w', 'tou_enable': 'tou'}
SWITCHES = {'grid_charge', 'solar_sell'}


class SimulatedPlant:
    def __init__(self, snapshot, *, fail_after_effect=None, stale_reads=None):
        self.data = copy.deepcopy(snapshot)
        self.writes = []
        self.reads = []
        self.fail_after_effect = fail_after_effect
        self.stale_reads = copy.deepcopy(stale_reads or {})

    def snapshot(self):
        return copy.deepcopy(self.data)

    def read(self, field):
        self.reads.append(field)
        stale = self.stale_reads.get(field)
        if stale:
            return stale.pop(0)
        if field.startswith('program_'):
            _, number, item = field.split('_', 2)
            return self.data['programs'][int(number)][item]
        value = self.data[FIELDS[field]]
        return ('on' if value else 'off') if field in SWITCHES else value

    def write(self, field, value):
        self.writes.append((field, value))
        if field.startswith('program_'):
            _, number, item = field.split('_', 2)
            self.data['programs'][int(number)][item] = value
        else:
            self.data[FIELDS[field]] = value == 'on' if field in SWITCHES else value
        if self.fail_after_effect == len(self.writes):
            raise TimeoutError('simulated_transport_lost_after_effect')

    def sleep(self, seconds):
        pass


def example_snapshot(at, soc=35.0, prices=None):
    """Synthetic commissioning fixture, never a substitute for missing telemetry."""
    starts = ['00:00:00', '12:00:00', '18:30:00', '23:30:00', '23:40:00', '23:50:00']
    return {'at': at.isoformat(), 'soc': soc, 'pack_v': 53.0, 'p1_w': 0.0,
            'grid_charge_on': False, 'grid_charge_a': 40.0, 'tou': 'Enabled',
            'work_mode': 'Zero Export To CT', 'solar_sell_on': False,
            'energy_pattern': 'Load First', 'export_w': 7900.0, 'grid_export_w': 7900.0,
            'alarm': 'OK', 'batt_alarm': 'off', 'stop': False, 'discharge_now': False,
            'pause_charge': False, 'docan_fresh': True, 'truth_ready': True,
            'ages': {'docan': 0.0, 'deye': 0.0, 'p1': 0.0},
            'temps': {'mos_temperature': 25.0, 'environment_temperature': 25.0,
                      **{f'probe_{i}_temperature': 25.0 for i in range(1, 5)}},
            'prices': prices or [.3]*48 + [.1]*24 + [.6]*24,
            'prices_tomorrow': None,
            'programs': {i: {'time': start, 'charging': 'Grid' if i == 2 else 'Disabled',
                             'voltage': 55.2 if i == 2 else 49.0, 'power': 8000.0,
                             'soc': 95.0 if i == 2 else 25.0}
                         for i, start in enumerate(starts, 1)}}
