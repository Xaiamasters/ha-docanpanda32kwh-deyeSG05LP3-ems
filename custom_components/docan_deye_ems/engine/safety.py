"""Independent charge and export deadman decisions from the production policy."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DeadmanLimits:
    charge_soc: float = 96.0
    charge_voltage: float = 55.3
    mos_temperature: float = 80.0
    probe_temperature: float = 45.0
    export_soc: float = 42.3
    export_voltage: float = 49.0
    export_end: str = '22:30'


def numeric(value):
    return value if isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value) else None


def charge_stop(snapshot,charge_power,limits=DeadmanLimits()):
    power=numeric(charge_power)
    charging=snapshot.get('grid_charge_on') is not False or power is None or power>300
    if not charging and snapshot.get('stop') is False:return None
    if snapshot.get('stop') is not False:return 'owner_stop_or_unknown'
    soc=numeric(snapshot.get('soc'));voltage=numeric(snapshot.get('pack_v'))
    temps=snapshot.get('temps',{})
    mos=numeric(temps.get('mos_temperature'))
    probes=[numeric(temps.get(f'probe_{i}_temperature')) for i in range(1,5)]
    if soc is None or voltage is None or mos is None or any(v is None for v in probes):return 'safety_telemetry_unreadable'
    if soc>=limits.charge_soc:return 'charge_soc_bound'
    if voltage>=limits.charge_voltage:return 'charge_voltage_bound'
    if snapshot.get('alarm')!='OK':return 'inverter_alarm'
    if mos>=limits.mos_temperature:return 'mos_temperature_bound'
    if any(v>=limits.probe_temperature for v in probes):return 'probe_temperature_bound'
    return None


def export_stop(snapshot,at,limits=DeadmanLimits()):
    p1=numeric(snapshot.get('p1_w'))
    if not (snapshot.get('solar_sell_on') is True and p1 is not None and p1 < -200):return None
    soc=numeric(snapshot.get('soc'));voltage=numeric(snapshot.get('pack_v'))
    if soc is not None and soc<=limits.export_soc:return 'export_soc_floor'
    if voltage is not None and voltage<=limits.export_voltage:return 'export_voltage_floor'
    if at.strftime('%H:%M')>=limits.export_end:return 'export_hard_end'
    if snapshot.get('alarm') not in ('OK',None):return 'inverter_alarm'
    return None
