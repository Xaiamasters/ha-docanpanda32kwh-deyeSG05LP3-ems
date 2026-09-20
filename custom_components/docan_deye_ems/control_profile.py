"""Validate installation-owned limits and build the controller's contract."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math

from .engine.planner import Contract
from .engine.safety import DeadmanLimits

MODELS = {f'SUN-{n}K-SG05LP3-EU-SM2':n for n in (6,8,10,12)}


def defaults(model):
    rating=MODELS[model]*1000
    return {'charge_current_a':min(160,int(rating/55.2)), 'program_power_w':min(8000,rating),
            'charge_voltage':55.2,'idle_voltage':49.0,'hard_voltage':55.3,
            'max_soc':95,'minimum_soc':25,'export_floor_soc':45,
            'mos_stop_c':70,'probe_stop_c':45,'environment_stop_c':50,
            'allow_export':False,'export_end':'22:30','baseline_load_kw':.5,
            'meter_factor':.95}


def validate_control(value, model):
    cfg=defaults(model)
    if value is not None:
        if not isinstance(value,dict) or set(value)-set(cfg):raise ValueError('invalid_control_profile')
        cfg.update(value)
    rating=MODELS[model]*1000
    bounds={'charge_current_a':(1,min(160,int(rating/55.2))), 'program_power_w':(100,min(10000,rating)),
            'charge_voltage':(50,55.2),'idle_voltage':(48,52),'hard_voltage':(50.1,55.3),
            'max_soc':(50,95),'minimum_soc':(25,70),'export_floor_soc':(25,90),
            'mos_stop_c':(35,80),'probe_stop_c':(30,45),'environment_stop_c':(30,50),
            'baseline_load_kw':(.05,10),'meter_factor':(.5,1)}
    for name,(low,high) in bounds.items():
        v=cfg[name]
        if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high:raise ValueError('invalid_control_profile')
    for key in ('charge_current_a','program_power_w','max_soc','minimum_soc','export_floor_soc'):
        if cfg[key]!=int(cfg[key]):raise ValueError('whole_number_required')
        cfg[key]=int(cfg[key])
    for key in ('charge_voltage','idle_voltage','hard_voltage'):
        if not math.isclose(cfg[key]*100,round(cfg[key]*100),rel_tol=0,abs_tol=1e-7):raise ValueError('invalid_voltage_precision')
    if not cfg['idle_voltage']<cfg['charge_voltage']<cfg['hard_voltage'] or not cfg['minimum_soc']<=cfg['export_floor_soc']<cfg['max_soc']:
        raise ValueError('inconsistent_control_limits')
    for key in ('allow_export',):
        if type(cfg[key]) is not bool:raise ValueError('invalid_control_profile')
    from datetime import time
    try:cutoff=time.fromisoformat(cfg['export_end'])
    except (ValueError,TypeError):raise ValueError('invalid_export_cutoff') from None
    if cutoff.tzinfo is not None or cutoff.second or not 18<=cutoff.hour<=23:raise ValueError('invalid_export_cutoff')
    cfg['export_end']=cutoff.strftime('%H:%M')
    return cfg


def contract(settings):
    cfg=validate_control(settings.get('control'),settings['model'])
    plan=settings['plan']
    base=Contract()
    max_kw=min(MODELS[settings['model']],cfg['program_power_w']/1000,cfg['charge_current_a']*cfg['charge_voltage']/1000)
    return replace(base,household_profile=True,maximum_soc=cfg['max_soc'],program_power_w=cfg['program_power_w'],
                   baseline_load_kw=cfg['baseline_load_kw'],meter_factor=cfg['meter_factor'],
                   export_end_hhmm=cfg['export_end'],
                   cap_kwh=plan['capacity_kwh'],floor_min_pct=int(plan['fallback_reserve_soc']),
                   export_power_w=plan['export_power_w'],rte=plan['round_trip_efficiency'],
                   degradation=plan['wear_cost_per_kwh'],export_price_deduction=0,
                   charge_setpoint_v=cfg['charge_voltage'],idle_voltage_v=cfg['idle_voltage'],
                   hard_bound_v=cfg['hard_voltage'],never_empty_pct=cfg['minimum_soc'],
                   curve=tuple((soc,min(kw,max_kw)) for soc,kw in base.curve),
                   allow_export=cfg['allow_export'],chg_soc_target=min(90,cfg['max_soc']))


def policy_limits(cfg):
    return {'CHARGE_A':cfg['charge_current_a'],'IDLE_A':min(40,cfg['charge_current_a']),
            'CHARGE_V':cfg['charge_voltage'],'IDLE_V':cfg['idle_voltage'],
            'HARD_V':cfg['hard_voltage'],'PACK_LOW_V':cfg['idle_voltage'],
            'NEVER_EMPTY':cfg['minimum_soc'],'SOC_MAX':cfg['max_soc'],'PROGRAM_W':cfg['program_power_w'],
            'MOS_STOP_C':cfg['mos_stop_c'],'PROBE_STOP_C':cfg['probe_stop_c'],
            'ENVIRONMENT_STOP_C':cfg['environment_stop_c']}


def deadman_limits(cfg):
    return DeadmanLimits(charge_soc=cfg['max_soc'],charge_voltage=cfg['hard_voltage'],
                         mos_temperature=cfg['mos_stop_c'],probe_temperature=cfg['probe_stop_c'],
                         environment_temperature=cfg['environment_stop_c'],
                         export_soc=cfg['export_floor_soc'],export_voltage=cfg['idle_voltage'],
                         export_end=cfg['export_end'])


def context_digest(settings, zone):
    """Approval cannot survive an equipment, price, timezone or limit change."""
    context={key:settings.get(key) for key in ('installation_id','model','connection','equipment','battery','control','plan','bindings','signs')}
    context['pricing']={k:v for k,v in settings['pricing'].items() if k!='token'}
    context['zone']=zone
    return hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
