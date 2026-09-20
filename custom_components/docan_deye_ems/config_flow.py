"""Household-owned configuration. No discovery of remote systems or plant writes."""
from __future__ import annotations
import copy
import json
from uuid import uuid4
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from pynordpool.const import AREAS, Currency
from .const import DOMAIN, NAME, MODELS, DEFAULT_MODEL, CORE_KEYS, MEASUREMENTS, CAPACITY_KWH
from .model import InputError, number
from .settings import binding, resolve, validate_document
from .pricing import tibber_homes
from .equipment import TRANSPORTS, validate_connection, read_equipment
from .docan import BATTERY_KEYS, validate_docan, read_docan


def choice(values):
    return selector.SelectSelector(selector.SelectSelectorConfig(options=list(values), mode=selector.SelectSelectorMode.DROPDOWN))


def numeric(low, high, step=1):
    return selector.NumberSelector(selector.NumberSelectorConfig(min=low, max=high, step=step, mode=selector.NumberSelectorMode.BOX))


SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain='sensor'))


class HouseholdFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.d = {'schema': 1, 'installation_id': uuid4().hex, 'name': NAME,
                  'connection': 'existing_ha_sensors', 'bindings': {}, 'signs': {},
                  'location': {'enabled': False}, 'solar': {}, 'pricing': {}, 'plan': {}}
        self.reconfigure_entry = None
        self.homes = []
        self.equipment_preview = {}

    def form(self, step, schema, error=None):
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema),
                                    errors={'base': error} if error else {})

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id='user', menu_options=['hardware', 'restore'])

    async def async_step_reconfigure(self, user_input=None):
        self.reconfigure_entry = self._get_reconfigure_entry()
        self.d = copy.deepcopy(dict(self.reconfigure_entry.data))
        return await self.async_step_hardware()

    async def async_step_restore(self, user_input=None):
        error = None
        if user_input:
            try:
                if len(user_input['document']) > 65536:
                    raise InputError('invalid_document')
                doc = json.loads(user_input['document'])
                if doc.get('format') != 'docan-deye-ems-settings' or doc.get('version') != 1:
                    raise InputError('invalid_document')
                candidate = doc['settings']
                if candidate.get('pricing', {}).get('provider') == 'tibber':
                    candidate['pricing']['token'] = 'REAUTHENTICATION_REQUIRED'
                self.d = validate_document(candidate)
                self.d['pricing'].pop('token', None)
                await self.async_set_unique_id(self.d['installation_id'])
                self._abort_if_unique_id_configured()
                return await self.async_step_hardware()
            except (ValueError, TypeError, KeyError, AttributeError):
                error = 'invalid_document'
        return self.form('restore', {vol.Required('document'): selector.TextSelector(selector.TextSelectorConfig(multiline=True))}, error)

    async def async_step_hardware(self, user_input=None):
        if user_input:
            self.d.update(user_input)
            return await self.async_step_battery()
        return self.form('hardware', {
            vol.Required('name', default=self.d['name']): str,
            vol.Required('model', default=self.d.get('model', DEFAULT_MODEL)): choice(MODELS),
            vol.Required('bms_link', default=self.d.get('bms_link', 'unknown')): choice(['can', 'rs485', 'unknown']),
            vol.Required('diagram_reserve_soc', **({'default': self.d['diagram_reserve_soc']} if 'diagram_reserve_soc' in self.d else {})): numeric(0, 99),
        })

    async def async_step_battery(self, user_input=None):
        error = None
        old = self.d.get('battery', {'source': 'docan_usb'})
        if user_input:
            try:
                if user_input['source'] == 'inverter':
                    self.d['battery'] = {'source': 'inverter'}
                    return await self.async_step_connection()
                value = number(user_input.get('address', 0))
                if not value.is_integer():
                    raise InputError('invalid_battery_connection')
                cfg = validate_docan({'source': 'docan_usb', 'port': user_input.get('port'), 'address': int(value)})
                for entry in self._async_current_entries():
                    if self.reconfigure_entry and entry.entry_id == self.reconfigure_entry.entry_id:
                        continue
                    if entry.data.get('battery', {}).get('port') == cfg['port']:
                        raise InputError('battery_port_in_use')
                self.battery_preview = await self.hass.async_add_executor_job(read_docan, cfg)
                self.d['battery'] = cfg
                for key in BATTERY_KEYS:
                    self.d['bindings'].pop(key, None)
                return await self.async_step_battery_check()
            except InputError as err:
                error = str(err)
        return self.form('battery', {
            vol.Required('source', default=old['source']): choice([
                {'value': 'docan_usb', 'label': 'Direct Docan USB/RS485'},
                {'value': 'inverter', 'label': 'Inverter readings or existing battery sensors'}]),
            vol.Optional('port', **({'default': old['port']} if old.get('port') else {})): str,
            vol.Optional('address', default=old.get('address', 0)): numeric(0, 15),
        }, error)

    async def async_step_battery_check(self, user_input=None):
        if user_input and user_input.get('readings_match'):
            return await self.async_step_connection()
        return self.async_show_form(step_id='battery_check',
            data_schema=vol.Schema({vol.Required('readings_match', default=False): bool}),
            errors={'base': 'check_equipment_readings'} if user_input is not None else {},
            description_placeholders={k: str(round(v, 3)) for k, v in self.battery_preview.items()})

    async def async_step_connection(self, user_input=None):
        if user_input:
            method = user_input['method']
            if method == 'existing_ha_sensors':
                self.d['connection'] = method
                self.d.pop('equipment', None)
                return await self.async_step_measurements()
            if self.d.get('connection') != 'direct_deye':
                self.d['bindings'] = {k: v for k, v in self.d['bindings'].items() if k not in MEASUREMENTS}
            self.d['connection'] = 'direct_deye'
            old = self.d.get('equipment', {})
            self.d['equipment'] = {**old, 'transport': method}
            if old.get('transport') != method:
                self.d['equipment'].pop('port', None)
            return await self.async_step_equipment()
        default = self.d.get('equipment', {}).get('transport', 'solarman_v5')
        if self.reconfigure_entry and self.d['connection'] == 'existing_ha_sensors':
            default = 'existing_ha_sensors'
        labels = {'solarman_v5': 'Solarman V5 logger', 'modbus_tcp': 'Modbus TCP gateway',
                  'modbus_rtu_tcp': 'Transparent Modbus RTU-over-TCP gateway',
                  'existing_ha_sensors': 'Existing Home Assistant sensors'}
        return self.form('connection', {vol.Required('method', default=default): choice([
            {'value': key, 'label': label} for key, label in labels.items()])})

    async def async_step_equipment(self, user_input=None):
        error = None
        old = self.d['equipment']
        if user_input:
            try:
                candidate = {'transport': old['transport'], **user_input}
                for key in ('port', 'unit', 'serial'):
                    if key in candidate:
                        value = number(candidate[key])
                        if not value.is_integer():
                            raise InputError('invalid_connection')
                        candidate[key] = int(value)
                candidate = validate_connection(candidate)
                for entry in self._async_current_entries():
                    if self.reconfigure_entry and entry.entry_id == self.reconfigure_entry.entry_id:
                        continue
                    other = entry.data.get('equipment', {})
                    if all(other.get(k) == candidate[k] for k in ('host', 'port', 'unit')):
                        raise InputError('equipment_already_configured')
                self.equipment_preview = await read_equipment(candidate)
                self.d['equipment'] = candidate
                self.d['max_age'] = 180
                self.d['signs'] = {k: 1 for k in ('battery_power', 'battery_current', 'grid_power')}
                return await self.async_step_equipment_check()
            except InputError as err:
                error = str(err)
        schema = {vol.Required('host', **({'default': old['host']} if old.get('host') else {})): str,
                  vol.Required('port', default=old.get('port', 8899 if old['transport'] == 'solarman_v5' else 502)): numeric(1, 65535),
                  vol.Required('unit', default=old.get('unit', 1)): numeric(1, 247)}
        if old['transport'] == 'solarman_v5':
            schema[vol.Required('serial', **({'default': old['serial']} if old.get('serial') else {}))] = numeric(1, 4294967295)
        return self.form('equipment', schema, error)

    async def async_step_equipment_check(self, user_input=None):
        if user_input and user_input.get('readings_match'):
            return await self.async_step_location()
        return self.async_show_form(step_id='equipment_check',
            data_schema=vol.Schema({vol.Required('readings_match', default=False): bool}),
            errors={'base': 'check_equipment_readings'} if user_input is not None else {},
            description_placeholders={k: str(v) for k, v in self.equipment_preview.items()})

    async def async_step_measurements(self, user_input=None):
        error = None
        if user_input:
            try:
                required = [k for k in CORE_KEYS if not (self.d.get('battery',{}).get('source')=='docan_usb' and k in BATTERY_KEYS)]
                new = {k: binding(self.hass, user_input[k], MEASUREMENTS[k][1]) for k in required}
                for k in ('solar_today', 'load_today', 'import_today', 'export_today'):
                    self.d['bindings'].pop(k, None)
                    if user_input.get(k):
                        new[k] = binding(self.hass, user_input[k], 'kWh')
                self.d['bindings'].update(new)
                self.d['max_age'] = user_input['max_age']
                self.d['signs'] = {k: 1 if user_input[k + '_direction'] == 'positive_' + p else -1
                                   for k, p in [('battery_power', 'discharge'), ('battery_current', 'discharge'), ('grid_power', 'import')]}
                return await self.async_step_location()
            except InputError as err:
                error = str(err)
        schema = {}
        for k in (*CORE_KEYS, 'solar_today', 'load_today', 'import_today', 'export_today'):
            if self.d.get('battery',{}).get('source')=='docan_usb' and k in BATTERY_KEYS:
                continue
            marker = vol.Required if k in CORE_KEYS else vol.Optional
            old = resolve(self.hass, self.d['bindings'].get(k))
            schema[marker(k, default=old) if old else marker(k)] = SENSOR
        for k, positive, negative in [('battery_power', 'discharge', 'charge'), ('battery_current', 'discharge', 'charge'), ('grid_power', 'import', 'export')]:
            schema[vol.Required(k + '_direction', default='positive_' + (positive if self.d['signs'].get(k, 1) == 1 else negative))] = choice(['positive_' + positive, 'positive_' + negative])
        schema[vol.Required('max_age', default=self.d.get('max_age', 180))] = numeric(30, 3600)
        return self.form('measurements', schema, error)

    async def async_step_location(self, user_input=None):
        if user_input:
            self.d['location']['enabled'] = user_input['enabled']
            if user_input['enabled']:
                return await self.async_step_home()
            return await self.async_step_solar()
        return self.form('location', {vol.Required('enabled', default=self.d['location'].get('enabled', False)): bool})

    async def async_step_home(self, user_input=None):
        error = None
        if user_input:
            try:
                loc = user_input['position']
                latitude, longitude = number(loc['latitude']), number(loc['longitude'])
                if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                    raise InputError('invalid_location')
                self.d['location'] = {'enabled': True, 'latitude': latitude, 'longitude': longitude,
                                      'address': user_input.get('address', '')}
                return await self.async_step_solar()
            except (ValueError, TypeError, KeyError):
                error = 'invalid_location'
        old = self.d['location']
        position = vol.Required('position', default={k: old[k] for k in ('latitude', 'longitude')}) if 'latitude' in old else vol.Required('position')
        return self.form('home', {vol.Optional('address', default=old.get('address', '')): str,
                                  position: selector.LocationSelector(selector.LocationSelectorConfig(radius=False))}, error)

    async def async_step_solar(self, user_input=None):
        error = None
        if user_input:
            try:
                kind = user_input['connection']
                if kind == 'none':
                    self.d['solar'] = {'connection': 'none', 'kwp': 0}
                    self.d['bindings'].pop('solar_power', None)
                else:
                    if not 0 < number(user_input.get('kwp')) <= 100:
                        raise InputError('invalid_solar')
                    if self.d['connection'] == 'direct_deye' and kind == 'deye_dc':
                        self.d['bindings'].pop('solar_power', None)
                    else:
                        self.d['bindings']['solar_power'] = binding(self.hass, user_input.get('solar_power'), 'W')
                    self.d['solar'] = {'connection': kind, 'kwp': user_input['kwp']}
                return await self.async_step_pricing()
            except InputError as err:
                error = str(err)
        old = resolve(self.hass, self.d['bindings'].get('solar_power'))
        return self.form('solar', {
            vol.Required('connection', default=self.d['solar'].get('connection', 'deye_dc')): choice(['deye_dc', 'external_ac', 'mixed', 'none']),
            vol.Optional('kwp', **({'default': self.d['solar']['kwp']} if self.d['solar'].get('kwp') else {})): numeric(0, 100, 0.01),
            vol.Optional('solar_power', **({'default': old} if old else {})): SENSOR,
        }, error)

    async def async_step_pricing(self, user_input=None):
        if user_input:
            if self.d['pricing'].get('provider') != user_input['provider']:
                self.d['pricing'] = {}
                self.d['bindings'].pop('price_curve', None)
            self.d['pricing'].update(user_input)
            return await getattr(self, 'async_step_' + user_input['provider'])()
        return self.form('pricing', {vol.Required('provider', default=self.d['pricing'].get('provider', 'tibber')): choice(['tibber', 'nordpool', 'sensor']),
                                     vol.Required('currency', default=self.d['pricing'].get('currency', 'EUR')): choice([c.value for c in Currency])})

    async def async_step_tibber(self, user_input=None):
        error = None
        if user_input is not None:
            try:
                token = user_input.get('token') or self.d['pricing'].get('token')
                self.homes = await tibber_homes(self.hass, token)
                self.d['pricing'].update(token=token, basis='all_in', tax=0, fee=0, vat=0, export_fee=0)
                return await self.async_step_tibber_home()
            except InputError as err:
                error = str(err)
        field = vol.Optional('token') if self.d['pricing'].get('token') else vol.Required('token')
        return self.form('tibber', {field: selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))}, error)

    async def async_step_tibber_home(self, user_input=None):
        if user_input:
            self.d['pricing'].update(user_input)
            return await self.async_step_export_tariff()
        return self.form('tibber_home', {vol.Required('home_id'): choice(self.homes)})

    async def async_step_nordpool(self, user_input=None):
        if user_input:
            self.d['pricing'].update(user_input, basis='spot')
            return await self.async_step_tariff()
        return self.form('nordpool', {vol.Required('area', **({'default': self.d['pricing']['area']} if 'area' in self.d['pricing'] else {})): choice(sorted(set(AREAS) - {'SYS'}))})

    async def async_step_sensor(self, user_input=None):
        error = None
        if user_input:
            try:
                self.d['bindings']['price_curve'] = binding(self.hass, user_input['price_curve'])
                self.d['pricing']['basis'] = user_input['basis']
                return await self.async_step_tariff()
            except InputError as err:
                error = str(err)
        old = resolve(self.hass, self.d['bindings'].get('price_curve'))
        return self.form('sensor', {vol.Required('price_curve', **({'default': old} if old else {})): SENSOR,
                                    vol.Required('basis', default=self.d['pricing'].get('basis', 'all_in')): choice(['all_in', 'spot'])}, error)

    async def async_step_tariff(self, user_input=None):
        error = None
        if user_input:
            if self.d['pricing']['basis'] == 'all_in' and any(user_input[k] for k in ('tax', 'fee', 'vat')):
                error = 'all_in_tax_twice'
            else:
                self.d['pricing'].update(user_input)
                return await self.async_step_export_tariff()
        return self.form('tariff', {vol.Required(k, default=self.d['pricing'].get(k, 0)): numeric(0, 100 if k == 'vat' else 10, 0.001)
                                     for k in ('tax', 'fee', 'vat', 'export_fee')}, error)

    async def async_step_plan(self, user_input=None):
        if user_input:
            source = user_input['source']
            if self.d['plan'].get('source') != source:
                self.d['plan'] = {'source': source}
            if source == 'forecast_shadow':
                return await self.async_step_forecast()
            if source == 'production_shadow':
                return await self.async_step_production()
            return await (self.async_step_observed() if source == 'observed' else self.async_step_limits())
        options=[{'value':'forecast_shadow','label':'Forecast optimizer (shadow)'},
                 {'value':'shadow_estimate','label':'Charging estimate'},
                 {'value':'observed','label':'Supplied controller plan'},
                 {'value':'production_shadow','label':'Automatic charge and export engine'}]
        default='production_shadow' if self.d.get('connection')=='direct_deye' and self.d.get('battery',{}).get('source')=='docan_usb' else 'forecast_shadow'
        return self.form('plan', {vol.Required('source', default=self.d['plan'].get('source',default)): choice(options)})

    async def async_step_production(self, user_input=None):
        error=None
        direct_frame=self.d.get('connection')=='direct_deye' and self.d.get('battery',{}).get('source')=='docan_usb'
        if user_input:
            try:
                values=dict(user_input)
                if direct_frame:
                    self.d['bindings'].pop('controller_snapshot',None)
                    values.pop('controller_snapshot',None)
                else:
                    self.d['bindings']['controller_snapshot']=binding(self.hass,values.pop('controller_snapshot'))
                self.d['plan']={'source':'production_shadow','export_price_deduction':0,**values}
                validate_document(self.d)
                return await self.async_step_control_limits()
            except InputError as err:
                error=str(err)
        old=self.d['plan']
        entity=resolve(self.hass,self.d['bindings'].get('controller_snapshot'))
        schema={} if direct_frame else {vol.Required('controller_snapshot',**({'default':entity} if entity else {})):SENSOR}
        fields={'capacity_kwh':(32.0,20,40,.01),'fallback_reserve_soc':(64,25,70,1),
                'export_power_w':(min(7900,MODELS[self.d['model']]*1000),10,min(10000,MODELS[self.d['model']]*1000),10),
                'round_trip_efficiency':(.87,.01,1,.01),'wear_cost_per_kwh':(.04,0,1,.001)}
        for key,(default,low,high,step) in fields.items():
            schema[vol.Required(key,default=old.get(key,default))]=numeric(low,high,step)
        return self.form('production',schema,error)

    async def async_step_control_limits(self,user_input=None):
        from .control_profile import defaults,validate_control
        error=None
        if user_input:
            try:
                self.d['control']=validate_control(user_input,self.d['model'])
                validate_document(self.d)
                return await self.async_step_finish()
            except (ValueError,InputError):error='invalid_control_profile'
        cfg={**defaults(self.d['model']),**self.d.get('control',{})}
        max_w=MODELS[self.d['model']]*1000
        fields={'charge_current_a':(1,min(160,int(max_w/55.2)),1),'program_power_w':(100,min(10000,max_w),10),
                'charge_voltage':(50,55.2,.01),'idle_voltage':(48,52,.01),'hard_voltage':(50.1,55.3,.01),
                'max_soc':(50,95,1),'minimum_soc':(25,70,1),'export_floor_soc':(25,90,1),
                'mos_stop_c':(35,80,1),'probe_stop_c':(30,45,1),'environment_stop_c':(30,50,1),
                'baseline_load_kw':(.05,10,.05),'meter_factor':(.5,1,.01)}
        schema={vol.Required(key,default=cfg[key]):numeric(*bounds) for key,bounds in fields.items()}
        schema.update({vol.Required('allow_export',default=cfg['allow_export']):bool,
                       vol.Required('export_end',default=cfg['export_end']):str})
        return self.form('control_limits',schema,error)

    async def async_step_export_tariff(self, user_input=None):
        error = None
        if user_input:
            if user_input['export_mode'] == 'curve' and self.d['pricing']['provider'] != 'sensor':
                error = 'export_curve_requires_sensor'
            elif user_input['export_mode']=='spot' and self.d['pricing']['provider']!='nordpool':
                error='spot_export_requires_nordpool'
            else:
                self.d['pricing'].update(user_input)
                return await self.async_step_plan()
        old = self.d['pricing']
        return self.form('export_tariff', {
            vol.Required('export_mode', default=old.get('export_mode','fixed')): choice(['fixed','curve','spot']),
            vol.Required('net_export_price', default=old.get('net_export_price',0)): numeric(-10,10,.001)
        }, error)

    async def async_step_forecast(self, user_input=None):
        error = None
        old = self.d.get('forecast', {})
        if user_input:
            try:
                cfg = dict(user_input)
                entity = cfg.pop('solar_forecast', None)
                ac = cfg.pop('battery_ac_power', None)
                position = cfg.pop('position', None)
                if cfg['solar_source'] == 'forecast_solar':
                    if not cfg.get('consent'):
                        raise InputError('forecast_location_consent_required')
                    if position is None:
                        raise InputError('invalid_location')
                    cfg.update(latitude=position['latitude'],longitude=position['longitude'])
                    self.d['bindings'].pop('solar_forecast',None)
                elif cfg['solar_source'] == 'sensor':
                    self.d['bindings']['solar_forecast']=binding(self.hass,entity)
                    for key in ('consent','tilt','azimuth'):
                        cfg.pop(key,None)
                else:
                    if self.d['solar']['connection']!='none':
                        raise InputError('solar_forecast_required')
                    self.d['bindings'].pop('solar_forecast',None)
                    for key in ('consent','tilt','azimuth'):
                        cfg.pop(key,None)
                self.d['bindings'].pop('battery_ac_power',None)
                if cfg['learn_efficiency']:
                    self.d['bindings']['battery_ac_power']=binding(self.hass,ac,'W')
                self.d['forecast']=cfg
                return await self.async_step_optimizer()
            except (InputError, KeyError, TypeError) as err:
                error = str(err) if isinstance(err,InputError) else 'invalid_forecast_settings'
        schema={
            vol.Required('solar_source',default=old.get('solar_source','none' if self.d['solar']['connection']=='none' else 'forecast_solar')):choice(['forecast_solar','sensor','none']),
            vol.Optional('position', **({'default':{k:old[k] for k in ('latitude','longitude')}} if 'latitude' in old else {})):selector.LocationSelector(selector.LocationSelectorConfig(radius=False)),
            vol.Required('tilt',default=old.get('tilt',35)):numeric(0,90),
            vol.Required('azimuth',default=old.get('azimuth',0)):numeric(-180,180),
            vol.Required('consent',default=old.get('consent',False)):bool,
            vol.Required('baseline_load_kw',default=old.get('baseline_load_kw',.5)):numeric(0,50,.01),
            vol.Required('anticipate_prices',default=old.get('anticipate_prices',False)):bool,
            vol.Required('learn_efficiency',default=old.get('learn_efficiency',False)):bool,
        }
        for key in ('solar_forecast','battery_ac_power'):
            entity=resolve(self.hass,self.d['bindings'].get(key))
            schema[vol.Optional(key,**({'default':entity} if entity else {}))]=SENSOR
        return self.form('forecast',schema,error)

    async def async_step_optimizer(self, user_input=None):
        error = None
        if user_input:
            self.d['plan']={'source':'forecast_shadow','capacity_kwh':CAPACITY_KWH,**user_input}
            self.d['bindings'].pop('observed_plan',None)
            try:
                validate_document(self.d)
                return await self.async_step_finish()
            except InputError as err:
                error=str(err)
        old=self.d['plan']
        defaults={'reserve_soc':20,'target_soc':80,'terminal_soc':40,'max_soc':95,
                  'charge_power_kw':min(5,MODELS[self.d['model']]),'discharge_power_kw':min(5,MODELS[self.d['model']]),
                  'charge_efficiency':95,'discharge_efficiency':95,'grid_import_limit_kw':10,
                  'grid_export_limit_kw':5,'cycle_cost_per_kwh':.02}
        schema={}
        for key,value in defaults.items():
            high=100 if key.endswith('_soc') or key.endswith('_efficiency') else MODELS[self.d['model']] if key in ('charge_power_kw','discharge_power_kw') else 10 if key=='cycle_cost_per_kwh' else 50
            schema[vol.Required(key,default=old.get(key,value))]=numeric(0,high,.01)
        schema[vol.Required('charge_deadline',default=old.get('charge_deadline','17:00:00'))]=selector.TimeSelector()
        for key in ('allow_battery_export','export_on_anticipated'):
            schema[vol.Required(key,default=old.get(key,False))]=bool
        return self.form('optimizer',schema,error)

    async def async_step_observed(self, user_input=None):
        error = None
        if user_input:
            try:
                self.d['bindings']['observed_plan'] = binding(self.hass, user_input['observed_plan'])
                return await self.async_step_finish()
            except InputError as err:
                error = str(err)
        old = resolve(self.hass, self.d['bindings'].get('observed_plan'))
        return self.form('observed', {vol.Required('observed_plan', **({'default': old} if old else {})): SENSOR}, error)

    async def async_step_limits(self, user_input=None):
        error = None
        if user_input:
            self.d['plan'] = {'source': 'shadow_estimate', 'capacity_kwh': CAPACITY_KWH, **user_input}
            self.d['bindings'].pop('observed_plan', None)
            try:
                validate_document(self.d)
                return await self.async_step_finish()
            except InputError as err:
                error = str(err)
        d = self.d['plan']
        return self.form('limits', {
            vol.Required('reserve_soc', **({'default': d['reserve_soc']} if 'reserve_soc' in d else {})): numeric(0, 99),
            vol.Required('target_soc', **({'default': d['target_soc']} if 'target_soc' in d else {})): numeric(1, 100),
            vol.Required('charge_power_kw', **({'default': d['charge_power_kw']} if 'charge_power_kw' in d else {})): numeric(0.1, MODELS[self.d['model']], 0.1),
            vol.Required('charge_efficiency', **({'default': d['charge_efficiency']} if 'charge_efficiency' in d else {})): numeric(1, 100, 0.1),
            vol.Required('charge_deadline', **({'default': d['charge_deadline']} if 'charge_deadline' in d else {})): selector.TimeSelector(),
        }, error)

    async def async_step_finish(self, user_input=None):
        try:
            data = validate_document(self.d)
        except InputError as err:
            return self.async_abort(reason=str(err))
        if user_input is not None:
            if self.reconfigure_entry:
                current=getattr(self.reconfigure_entry,'runtime_data',None)
                if current and current.control:
                    await current.control.command('stop',{})
                return self.async_update_reload_and_abort(self.reconfigure_entry, data=data)
            await self.async_set_unique_id(data['installation_id'])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=data['name'], data=data)
        return self.async_show_form(step_id='finish', data_schema=vol.Schema({}), description_placeholders={'name': data['name'], 'model': data['model'], 'plan': data['plan']['source']})
