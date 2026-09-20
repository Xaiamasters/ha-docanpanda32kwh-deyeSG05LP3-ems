"""Validate household configuration and resolve portable sensor identities."""
from __future__ import annotations
import copy,json,re
from uuid import uuid4
from homeassistant.helpers import entity_registry as er
from pynordpool.const import AREAS,Currency
from .const import CORE_KEYS,MEASUREMENTS,MODELS,CAPACITY_KWH,DOMAIN
from .model import InputError,number,normalize
from .equipment import validate_connection
from .docan import validate_docan, BATTERY_KEYS

def binding(hass,entity_id,unit=None):
    if not isinstance(entity_id,str) or not re.fullmatch(r'sensor\.[a-z0-9_]+',entity_id):raise InputError('invalid_entity')
    row=er.async_get(hass).async_get(entity_id)
    if row and row.platform==DOMAIN:raise InputError('self_source')
    state=hass.states.get(entity_id)
    if state is None or state.state in ('unknown','unavailable'):raise InputError('source_unavailable')
    if unit:normalize(state.state,state.attributes.get('unit_of_measurement'),unit)
    return {'entity_id':entity_id,**({'platform':row.platform,'unique_id':row.unique_id} if row else {})}

def resolve(hass,b):
    if not isinstance(b,dict):return None
    if b.get('platform') and b.get('unique_id'):
        resolved=er.async_get(hass).async_get_entity_id('sensor',b['platform'],b['unique_id'])
        if resolved:return resolved
    return b.get('entity_id')

def validate_document(data):
    try:
        return _validate_document(data)
    except InputError:
        raise
    except (TypeError,KeyError,AttributeError,ValueError):
        raise InputError('invalid_document') from None


def _validate_document(data):
    """Strict shape at import/runtime; direct endpoints use the bounded reader."""
    if not isinstance(data,dict):raise InputError('invalid_document')
    d=copy.deepcopy(data)
    allowed={'schema','installation_id','name','model','bms_link','diagram_reserve_soc','connection','equipment','bindings','location','solar','pricing','plan','signs','max_age','battery','forecast','control'}
    if set(d)-allowed or d.get('schema')!=1:raise InputError('invalid_document')
    if not re.fullmatch(r'[a-f0-9]{32}',d.get('installation_id','')):raise InputError('invalid_document')
    if not isinstance(d.get('name'),str) or not 1<=len(d['name'].strip())<=80:raise InputError('invalid_name')
    if d.get('model') not in MODELS or d.get('connection') not in ('existing_ha_sensors','direct_deye'):raise InputError('invalid_profile')
    from .control_profile import validate_control
    d['control']=validate_control(d.get('control'),d['model'])
    direct=d['connection']=='direct_deye'
    d['battery']=validate_docan(d.get('battery',{'source':'inverter'}))
    independent=d['battery']['source']=='docan_usb'
    if direct:d['equipment']=validate_connection(d.get('equipment'))
    elif 'equipment' in d:raise InputError('invalid_connection')
    if d.get('bms_link') not in ('can','rs485','unknown'):raise InputError('invalid_profile')
    if not 0<=number(d.get('diagram_reserve_soc'))<=99:raise InputError('invalid_planning_limits')
    bindings=d.get('bindings',{})
    if set(bindings)-(set(MEASUREMENTS)|{'price_curve','observed_plan','solar_forecast','controller_snapshot'}):raise InputError('invalid_entity')
    for b in bindings.values():
        if not isinstance(b,dict) or set(b)-{'entity_id','platform','unique_id'} or not re.fullmatch(r'sensor\.[a-z0-9_]+',b.get('entity_id','')):raise InputError('invalid_entity')
        if any(not isinstance(v,str) or len(v)>255 for v in b.values()):raise InputError('invalid_entity')
    if not direct and any(k not in bindings for k in CORE_KEYS if not (independent and k in BATTERY_KEYS)):raise InputError('missing_source')
    if independent and any(k in bindings for k in BATTERY_KEYS):raise InputError('invalid_entity')
    if direct and any(k in bindings for k in CORE_KEYS):raise InputError('invalid_entity')
    if set(d.get('signs',{}))!={'battery_power','battery_current','grid_power'} or any(type(v) is not int or v not in (-1,1) for v in d['signs'].values()):raise InputError('invalid_sign')
    if not 30<=number(d.get('max_age'))<=3600:raise InputError('invalid_age')
    loc=d.get('location',{})
    if set(loc)-{'enabled','latitude','longitude','address'} or type(loc.get('enabled')) is not bool:raise InputError('invalid_location')
    if loc['enabled']:
        if not -90<=number(loc.get('latitude'))<=90 or not -180<=number(loc.get('longitude'))<=180:raise InputError('invalid_location')
        if 'address' in loc and (not isinstance(loc['address'],str) or len(loc['address'])>300):raise InputError('invalid_location')
    else:d['location']={'enabled':False}
    solar=d.get('solar',{})
    if set(solar)!={'connection','kwp'} or solar['connection'] not in ('none','deye_dc','external_ac','mixed'):raise InputError('invalid_solar')
    if not 0<=number(solar['kwp'])<=100:raise InputError('invalid_solar')
    if solar['connection']=='none':
        if solar['kwp']!=0:raise InputError('invalid_solar')
    elif not solar['kwp']>0 or (not (direct and solar['connection']=='deye_dc') and 'solar_power' not in bindings):raise InputError('missing_solar')
    if direct and (any(v!=1 for v in d['signs'].values()) or any(k in bindings for k in ('solar_today','load_today','import_today','export_today'))):raise InputError('invalid_entity')
    if direct and solar['connection']=='deye_dc' and 'solar_power' in bindings:raise InputError('invalid_entity')
    p=d.get('pricing',{})
    if set(p)-{'provider','currency','area','token','home_id','basis','tax','fee','vat','export_fee','export_mode','net_export_price'}:raise InputError('invalid_tariff')
    if 'export_mode' in p:
        if p['export_mode'] not in ('fixed','curve','spot'):raise InputError('invalid_tariff')
        if p['export_mode']=='fixed' and not -10<=number(p.get('net_export_price'))<=10:raise InputError('invalid_tariff')
        if p['export_mode']=='curve' and p.get('provider')!='sensor':raise InputError('invalid_tariff')
        if p['export_mode']=='spot' and p.get('provider')!='nordpool':raise InputError('invalid_tariff')
    if p.get('provider') not in ('tibber','nordpool','sensor') or p.get('currency') not in [c.value for c in Currency]:raise InputError('invalid_tariff')
    if p.get('basis') not in ('all_in','spot'):raise InputError('invalid_tariff')
    if any(not 0<=number(p.get(k))<=10 for k in ('tax','fee','export_fee')) or not 0<=number(p.get('vat'))<=100:raise InputError('invalid_tariff')
    if p['basis']=='all_in' and any(p[k] for k in ('tax','fee','vat')):raise InputError('all_in_tax_twice')
    if p['provider']=='tibber':
        if p['basis']!='all_in' or not isinstance(p.get('token'),str) or not 0<len(p['token'])<=500 or not isinstance(p.get('home_id'),str) or not p['home_id']:raise InputError('invalid_auth')
    elif p['provider']=='nordpool':
        if p.get('area') not in set(AREAS)-{'SYS'} or p['basis']!='spot':raise InputError('invalid_tariff')
    elif 'price_curve' not in bindings:raise InputError('missing_price_source')
    k=d.get('plan',{})
    if k.get('source') not in ('observed','shadow_estimate','forecast_shadow','production_shadow'):raise InputError('invalid_plan')
    if k['source']=='production_shadow':
        required={'source','capacity_kwh','fallback_reserve_soc','export_power_w','round_trip_efficiency','wear_cost_per_kwh','export_price_deduction'}
        direct_frame=d.get('connection')=='direct_deye' and d['battery']['source']=='docan_usb'
        if set(k)!=required or ('controller_snapshot' not in bindings and not direct_frame):raise InputError('missing_controller_snapshot')
        if not 20<=number(k['capacity_kwh'])<=40 or not 25<=number(k['fallback_reserve_soc'])<=70:raise InputError('invalid_planning_limits')
        if not 0<number(k['export_power_w'])<=MODELS[d['model']]*1000:raise InputError('invalid_planning_limits')
        if not 0<number(k['round_trip_efficiency'])<=1 or not 0<=number(k['wear_cost_per_kwh'])<=1:raise InputError('invalid_planning_limits')
        if p['currency']!='EUR':raise InputError('production_policy_requires_eur')
        if not -2<=number(k['export_price_deduction'])<=2:raise InputError('invalid_planning_limits')
        if k['export_power_w']>10000 or k['export_power_w']%10:raise InputError('invalid_planning_limits')
        if k['export_price_deduction']!=0:raise InputError('use_separate_export_tariff')
        if not d['control']['minimum_soc']<=k['fallback_reserve_soc']<d['control']['max_soc']:raise InputError('inconsistent_control_limits')
    elif k['source']=='observed':
        if set(k)!={'source'} or 'observed_plan' not in bindings:raise InputError('missing_plan_source')
    elif k['source']=='shadow_estimate':
        from datetime import time
        required={'source','capacity_kwh','target_soc','reserve_soc','charge_power_kw','charge_efficiency','charge_deadline'}
        if set(k)!=required or k['capacity_kwh']!=CAPACITY_KWH:raise InputError('invalid_planning_limits')
        if not 0<=number(k['reserve_soc'])<number(k['target_soc'])<=100 or not 0<number(k['charge_power_kw'])<=MODELS[d['model']] or not 0<number(k['charge_efficiency'])<=100:raise InputError('invalid_planning_limits')
        try:t=time.fromisoformat(k['charge_deadline'])
        except (ValueError,TypeError):raise InputError('invalid_deadline') from None
        if t.tzinfo or t.second:raise InputError('invalid_deadline')
    else:
        from datetime import time
        required={'source','capacity_kwh','reserve_soc','target_soc','terminal_soc','max_soc',
                  'charge_power_kw','discharge_power_kw','charge_efficiency','discharge_efficiency',
                  'grid_import_limit_kw','grid_export_limit_kw','cycle_cost_per_kwh','charge_deadline',
                  'allow_battery_export','export_on_anticipated'}
        if set(k)!=required or k['capacity_kwh']!=CAPACITY_KWH:raise InputError('invalid_planning_limits')
        if not 0<=number(k['reserve_soc'])<=number(k['terminal_soc'])<=number(k['max_soc'])<=100:raise InputError('invalid_planning_limits')
        if not k['reserve_soc']<=number(k['target_soc'])<=k['max_soc']:raise InputError('invalid_planning_limits')
        for key in ('charge_power_kw','discharge_power_kw'):
            if not 0<number(k[key])<=MODELS[d['model']]:raise InputError('invalid_planning_limits')
        for key in ('charge_efficiency','discharge_efficiency'):
            if not 0<number(k[key])<=100:raise InputError('invalid_planning_limits')
        if not 0<number(k['grid_import_limit_kw'])<=50 or not 0<=number(k['grid_export_limit_kw'])<=50 or not 0<=number(k['cycle_cost_per_kwh'])<=10:raise InputError('invalid_planning_limits')
        if any(type(k[x]) is not bool for x in ('allow_battery_export','export_on_anticipated')):raise InputError('invalid_plan')
        try:t=time.fromisoformat(k['charge_deadline'])
        except (ValueError,TypeError):raise InputError('invalid_deadline') from None
        if t.tzinfo or t.second:raise InputError('invalid_deadline')
        if p.get('export_mode') not in ('fixed','curve','spot'):raise InputError('export_price_required')
        f=d.get('forecast',{})
        permitted={'solar_source','latitude','longitude','tilt','azimuth','consent','baseline_load_kw','anticipate_prices','learn_efficiency'}
        if set(f)-permitted or f.get('solar_source') not in ('none','sensor','forecast_solar'):raise InputError('invalid_forecast_settings')
        if not 0<=number(f.get('baseline_load_kw'))<=50:raise InputError('invalid_forecast_settings')
        if any(type(f.get(x)) is not bool for x in ('anticipate_prices','learn_efficiency')):raise InputError('invalid_forecast_settings')
        if f['solar_source']=='none' and solar['connection']!='none':raise InputError('solar_forecast_required')
        if f['solar_source']=='sensor' and 'solar_forecast' not in bindings:raise InputError('solar_forecast_required')
        if f['solar_source']=='forecast_solar':
            if f.get('consent') is not True:raise InputError('forecast_location_consent_required')
            if not -90<=number(f.get('latitude'))<=90 or not -180<=number(f.get('longitude'))<=180:raise InputError('invalid_location')
            if not 0<=number(f.get('tilt'))<=90 or not -180<=number(f.get('azimuth'))<=180 or solar['kwp']<=0:raise InputError('invalid_forecast_settings')
        if f['learn_efficiency'] and 'battery_ac_power' not in bindings:raise InputError('dedicated_ac_measurement_required')
    if k['source']!='forecast_shadow':
        d.pop('forecast',None)
        bindings.pop('solar_forecast',None)
        bindings.pop('battery_ac_power',None)
    return d

def portable_export(data):
    clean=copy.deepcopy(data);clean.get('pricing',{}).pop('token',None)
    return {'format':'docan-deye-ems-settings','version':1,'settings':clean,'credentials_included':False}
