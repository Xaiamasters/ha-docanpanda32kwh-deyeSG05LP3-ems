"""Poll read-only local telemetry and prices; never register plant services."""
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
import logging
import hashlib
import json
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from .const import DOMAIN, MEASUREMENTS, CORE_KEYS, VERSION, CAPACITY_KWH
from .model import InputError, normalize, observed_plan, shadow_plan, instant, day_bounds
from .pricing import PriceReader
from .settings import resolve
from .equipment import read_equipment, direct_keys
from .docan import read_docan, BATTERY_KEYS
from .learning import Learning
from .forecast import SolarReader
from .optimizer import make_slots, optimize
from .engine_adapter import ProductionShadowAdapter
from .control_device import DeviceError

LOGGER = logging.getLogger(__name__)


class HouseholdCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, settings):
        super().__init__(hass, LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(seconds=30))
        self.settings = settings
        self.entry = entry
        self.prices = PriceReader(hass, settings['pricing'], hass.config.time_zone)
        self.store = Store(hass,1,DOMAIN+'.'+settings['installation_id']+'.learning')
        self.learning = Learning()
        self.last_save = None
        self.solar_reader = SolarReader(hass,settings.get('forecast',{}),settings['solar'])
        self.production_engine=ProductionShadowAdapter(hass,settings) if settings['plan']['source']=='production_shadow' else None

    async def async_initialize(self):
        saved = await self.store.async_load()
        # Rebinding equipment, signs, a tariff or forecast invalidates learned
        # comparisons. Credentials and display/map labels are excluded.
        cfg=self.settings
        context={k:cfg.get(k) for k in ('connection','equipment','battery','bindings','signs','solar','forecast')}
        context['pricing']={k:v for k,v in cfg['pricing'].items() if k!='token'}
        digest=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
        if not isinstance(saved,dict) or saved.get('context')!=digest:
            saved=None
        self.learning = Learning(saved)
        self.learning.data['context']=digest
        if self.production_engine:
            await self.production_engine.initialize()

    async def async_shutdown(self):
        await super().async_shutdown()
        await self.store.async_save(self.learning.data)
        if self.production_engine:
            await self.production_engine.save()

    async def forecast_plan(self, now, price_data, sources, values):
        d, zone = self.settings, self.hass.config.time_zone
        periods = await self.prices.horizon(now,price_data,sources.get('price_curve'))
        self.learning.learn_prices(periods,now)
        end = instant(periods[-1]['end'])
        local_day = now.astimezone(ZoneInfo(zone)).date()
        if d['forecast']['anticipate_prices'] and end == day_bounds(local_day,zone)[1]:
            tomorrow_end = day_bounds(local_day+timedelta(days=1),zone)[1]
            periods += self.learning.anticipate(end,tomorrow_end,zone)
        end = instant(periods[-1]['end'])
        solar = await self.solar_reader.read(now,now,end,sources.get('solar_forecast'))
        load, load_meta = self.learning.load_forecast(now,end,now,zone,d['forecast']['baseline_load_kw'])
        limits = dict(d['plan'])
        efficiency = self.learning.efficiencies(limits['charge_efficiency'],limits['discharge_efficiency'],d['forecast']['learn_efficiency'])
        limits.update(charge_efficiency=efficiency['charge'],discharge_efficiency=efficiency['discharge'])
        deadline = datetime.combine(local_day,time.fromisoformat(limits['charge_deadline']),tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
        # A passed target is not silently moved to tomorrow. After the deadline,
        # optimize against the terminal reserve and explicitly surface that fact.
        active_deadline = deadline if deadline > now else None
        slots = make_slots(periods,solar,load,now,active_deadline)
        plan = await self.hass.async_add_executor_job(optimize,slots,values['battery_soc'],limits,active_deadline)
        plan.update(date=local_day.isoformat(),updated_at=now.isoformat(),load_model=load_meta,
                    solar_source=d['forecast']['solar_source'],efficiency=efficiency,
                    deadline=deadline.isoformat(),deadline_passed=deadline<=now,
                    forecast_solar_kwh=sum(r['pv_kwh'] for r in slots),
                    forecast_load_kwh=sum(r['load_kwh'] for r in slots),
                    anticipated_intervals=sum(r['price_origin']=='anticipated' for r in slots))
        self.learning.remember_plan(plan,now)
        price_data={**price_data,'periods':periods}
        return plan, price_data

    async def _async_update_data(self):
        now = datetime.now(timezone.utc)
        d = self.settings
        values, failures, sources = {}, {}, {}
        keys = direct_keys(d)
        controller_frame=None
        observer=self.production_engine.observation if self.production_engine else None
        if observer:
            try:
                controller_frame=await observer.read(now.astimezone(ZoneInfo(self.hass.config.time_zone)),owner_stop=False)
                values.update({k:v for k,v in observer.measurements.items() if k in keys or k in BATTERY_KEYS})
            except (DeviceError,InputError,OSError,TimeoutError,ValueError):
                values.update({k:None for k in (*keys,*BATTERY_KEYS)})
                failures['equipment']='controller_observation_unavailable'
        elif keys:
            try:
                observed = await read_equipment(d['equipment'])
                values.update({k: observed[k] for k in keys})
                for key in keys:
                    if values[key] is None:
                        failures[key] = 'source_unavailable'
            except InputError as err:
                values.update({k: None for k in keys})
                failures['equipment'] = str(err)
        for key, b in d['bindings'].items():
            entity_id = resolve(self.hass, b)
            sources[key] = self.hass.states.get(entity_id) if entity_id else None
            if key not in MEASUREMENTS:
                continue
            state = sources[key]
            try:
                if state is None or state.state in ('unknown', 'unavailable'):
                    raise InputError('source_unavailable')
                age = (now - state.last_reported).total_seconds()
                if age < -5 or age > d['max_age']:
                    raise InputError('source_stale')
                values[key] = normalize(state.state, state.attributes.get('unit_of_measurement'), MEASUREMENTS[key][1], d['signs'].get(key, 1))
            except InputError as err:
                values[key] = None
                failures[key] = str(err)
        if d['battery']['source']=='docan_usb' and observer is None:
            try:
                values.update(await self.hass.async_add_executor_job(read_docan,d['battery']))
            except InputError as err:
                # Independent BMS failure must never fall back to inverter SoC.
                values.update({key:None for key in BATTERY_KEYS})
                failures['battery']=str(err)
        if d['solar']['connection']=='none':
            values['solar_power']=0.0
        core_ready = all(values.get(k) is not None for k in CORE_KEYS)
        if d['solar']['connection'] != 'none' and values.get('solar_power') is None:
            core_ready = False
        price_data = None
        try:
            price_data = await self.prices.read(now, sources.get('price_curve'))
        except InputError as err:
            failures['prices'] = str(err)
        except (ValueError, TypeError, KeyError, AttributeError):
            failures['prices'] = 'invalid_price_response'
        plan = {'mode': d['plan']['source'], 'status': 'not_ready', 'windows': [], 'physical_authority': False}
        try:
            if not core_ready:
                raise InputError('measurements_not_ready')
            if price_data is None:
                raise InputError('prices_not_ready')
            if d['plan']['source'] == 'observed':
                state = sources.get('observed_plan')
                if state is None:
                    raise InputError('plan_unavailable')
                plan = observed_plan(state.state, state.attributes, now, self.hass.config.time_zone, d['max_age'])
            elif d['plan']['source']=='forecast_shadow':
                plan, price_data = await self.forecast_plan(now,price_data,sources,values)
            elif d['plan']['source']=='production_shadow':
                periods=await self.prices.horizon(now,price_data,sources.get('price_curve'))
                plan=await self.production_engine.plan(now,sources.get('controller_snapshot'),periods,controller_frame)
                if not plan['inputs_valid']:
                    failures['plan']='controller_inputs_not_ready'
            else:
                plan = shadow_plan(price_data['periods'], values['battery_soc'], d['plan'], now, self.hass.config.time_zone)
        except InputError as err:
            failures['plan'] = str(err)
        except (ValueError, TypeError, KeyError, AttributeError):
            failures['plan'] = 'invalid_plan_response'
        active = next((p for p in price_data['periods'] if instant(p['start']) <= now < instant(p['end'])), None) if price_data else None
        values['import_price'] = active['import'] if active else None
        export = d['pricing'].get('net_export_price') if d['pricing'].get('export_mode')=='fixed' else active.get('export') if active else None
        self.learning.observe(now,values,values['import_price'],export)
        metrics = self.learning.metrics(now,self.hass.config.time_zone)
        if self.last_save is None or now-self.last_save>=timedelta(minutes=5):
            self.store.async_delay_save(lambda:self.learning.data,10)
            self.last_save=now
        ready = core_ready and not any(k in failures for k in ('prices', 'plan'))
        begin, end = day_bounds(now.astimezone(ZoneInfo(self.hass.config.time_zone)).date(), self.hass.config.time_zone)
        issue = f'{self.entry.entry_id}_inputs'
        if not ready:
            ir.async_create_issue(self.hass, DOMAIN, issue, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key='inputs_not_ready', translation_placeholders={'name': d['name']})
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue)
        # Explicit allowlist: token, address, provider home ID and source identities never enter the panel payload.
        return {'version': VERSION, 'name': d['name'], 'updated_at': now.isoformat(),
                'time_zone': self.hass.config.time_zone, 'day': {'start': begin.isoformat(), 'end': end.isoformat()}, 'ready': ready, 'mode': 'shadow_only',
                'physical_authority': False, 'model': d['model'], 'capacity_kwh': CAPACITY_KWH,
                'values': values, 'errors': failures, 'prices': price_data, 'plan': plan,
                'metrics':metrics,'battery_source':d['battery']['source'],
                'solar': dict(d['solar']), 'location': {k: v for k, v in d['location'].items() if k != 'address'},
                'reserve_soc': d['diagram_reserve_soc']}
