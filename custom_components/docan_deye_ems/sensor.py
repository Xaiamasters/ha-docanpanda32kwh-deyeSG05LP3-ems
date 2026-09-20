"""Numeric observations plus a structured read-only plan."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from .const import MEASUREMENTS
from .entity import HouseholdEntity
from .equipment import direct_keys
from .docan import BATTERY_KEYS
from .analytics import charge_hours
from .model import instant


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities([Measurement(c, key, name, unit) for key, (name, unit) in MEASUREMENTS.items() if key in c.settings['bindings'] or key in direct_keys(c.settings) or (c.settings['battery']['source']=='docan_usb' and key in BATTERY_KEYS)]
                       + [Measurement(c, 'import_price', 'Electricity import price', c.settings['pricing']['currency'] + '/kWh'), Plan(c),OperatingMode(c)]
                       + [Estimate(c,key,name,unit,path) for key,name,unit,path in ESTIMATES]
                       + [DailyStatistics(c)]
                       + [Report(c,key,name,unit,path) for key,name,unit,path in REPORTS])


class Measurement(HouseholdEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit):
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = {'W': SensorDeviceClass.POWER, '%': SensorDeviceClass.BATTERY, 'V': SensorDeviceClass.VOLTAGE, 'A': SensorDeviceClass.CURRENT, 'kWh': SensorDeviceClass.ENERGY, '°C':SensorDeviceClass.TEMPERATURE}.get(unit)
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING if unit == 'kWh' else SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data['values'].get(self.key)

    @property
    def available(self):
        return super().available and self.native_value is not None


class Plan(HouseholdEntity, SensorEntity):
    _unrecorded_attributes = frozenset({'horizon','windows','assumptions','decision','scheduled_jobs'})
    def __init__(self, coordinator):
        super().__init__(coordinator, 'plan', 'Energy plan')

    @property
    def native_value(self):
        return self.coordinator.data['plan']['status']

    @property
    def extra_state_attributes(self):
        return self.coordinator.data['plan']


ESTIMATES = [
    ('solar_forecast','Solar forecast horizon','kWh',('plan','forecast_solar_kwh')),
    ('load_forecast','Load forecast horizon','kWh',('plan','forecast_load_kwh')),
    ('planned_import','Planned grid import','kWh',('plan','estimated_import_kwh')),
    ('planned_export','Planned grid export','kWh',('plan','estimated_export_kwh')),
    ('planned_net_cost','Planned net grid cost','currency',('plan','estimated_net_cost')),
    ('planned_wear_cost','Planned battery wear cost','currency',('plan','estimated_wear_cost')),
    ('projected_soc','Projected end charge','%',('plan','projected_soc')),
    ('load_forecast_mae','Load forecast mean absolute error','W',('metrics','load_forecast_mae_w')),
    ('solar_forecast_mae','Solar forecast mean absolute error','W',('metrics','solar_forecast_mae_w')),
    ('plan_cost_mae','Shadow versus observed cost difference','currency',('metrics','plan_cost_mae')),
    ('charge_efficiency','Charging efficiency used','%',('plan','efficiency','charge')),
    ('discharge_efficiency','Discharging efficiency used','%',('plan','efficiency','discharge')),
    ('active_load_model','Active load model',None,('plan','load_model','source')),
]


class Estimate(HouseholdEntity, SensorEntity):
    def __init__(self,c,key,name,unit,path):
        super().__init__(c,key,name)
        self.path=path
        self._attr_native_unit_of_measurement=c.settings['pricing']['currency'] if unit=='currency' else unit
        if unit is not None:
            self._attr_state_class=SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        value=self.coordinator.data
        path=self.path
        if path[0]=='plan':
            if (value.get('comparison_plan',{}).get('status')=='unavailable'
                and path[1] not in ('load_model','efficiency')):
                return None
            path=('comparison_plan',*path[1:])
        for key in path:
            value=value.get(key) if isinstance(value,dict) else None
        return value

    @property
    def available(self):
        return super().available and self.native_value is not None


class DailyStatistics(HouseholdEntity, SensorEntity):
    _unrecorded_attributes = frozenset({'days'})

    def __init__(self,c):
        super().__init__(c,'daily_statistics','Observed daily grid statistics')

    @property
    def native_value(self):
        return len(self.coordinator.data['metrics']['days'])

    @property
    def extra_state_attributes(self):
        return self.coordinator.data['metrics']


class OperatingMode(HouseholdEntity,SensorEntity):
    def __init__(self,c):super().__init__(c,'operating_mode','Operating mode')

    @property
    def native_value(self):return self.coordinator.data['mode']

    @property
    def extra_state_attributes(self):
        control=self.coordinator.data.get('control',{})
        return {key:control.get(key) for key in ('active','commissioned','stop','programmed_day','watchdogs','last_error')}


REPORTS = [
    ('controller_state', 'Controller state', None, ('command','state')),
    ('setpoint', 'Controller power setpoint equivalent', 'W', ('command','setpoint_w')),
    ('charge_current_setpoint', 'Controller charge current setpoint', 'A', ('command','charge_current_a')),
    ('planned_charge_hours', 'Planned grid charge hours', 'h', None),
    ('fictive_plan', 'Comparison plan', 'h', None),
    ('solar_charge', 'Solar battery charge estimate today', 'kWh', None),
    ('daily_regret', 'Daily hindsight regret', 'currency', ('metrics','daily_regret')),
    ('dp_vs_heuristic_regret_delta', 'Seven day DP versus controller schedule cost delta', 'currency', ('metrics','dp_vs_heuristic_7d_delta')),
    ('over_buy', 'Grid over buy versus hindsight', 'kWh', ('metrics','over_buy')),
    ('under_buy', 'Grid under buy versus hindsight', 'kWh', ('metrics','under_buy')),
    ('24h_horizon_energy_mae', '24 hour load forecast energy error', 'kWh', ('metrics','horizon_24h_energy_mae')),
    ('12h_horizon_energy_mae', '12 hour load forecast energy error', 'kWh', ('metrics','horizon_12h_energy_mae')),
    ('load_forecast_pinball_p50', 'Load forecast P50 pinball loss', 'W', ('metrics','load_forecast_pinball_p50')),
    ('load_forecast_pinball_p80', 'Load forecast P80 pinball loss', 'W', ('metrics','load_forecast_pinball_p80')),
]


class Report(HouseholdEntity, SensorEntity):
    _unrecorded_attributes = frozenset({'horizon', 'windows', 'scored_days'})

    def __init__(self, c, key, name, unit, path):
        super().__init__(c, key, name)
        self.path = path
        self._attr_native_unit_of_measurement = c.settings['pricing']['currency'] if unit=='currency' else unit
        if unit is not None:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = {'W':SensorDeviceClass.POWER, 'A':SensorDeviceClass.CURRENT,
                                  'kWh':SensorDeviceClass.ENERGY, 'h':SensorDeviceClass.DURATION}.get(unit)

    @property
    def native_value(self):
        data = self.coordinator.data
        if self.key in ('planned_charge_hours', 'fictive_plan'):
            plan = data.get('plan' if self.key=='planned_charge_hours' else 'comparison_plan', {})
            if not plan:
                return None
            return charge_hours(plan, instant(data['updated_at']))
        if self.key=='solar_charge':
            from zoneinfo import ZoneInfo
            today = instant(data['updated_at']).astimezone(ZoneInfo(data['time_zone'])).date().isoformat()
            row = next((r for r in data['metrics']['days'] if r['date']==today), {})
            return row.get('solar_charge_estimate_kwh') if row.get('solar_charge_coverage_seconds', 0)>0 else None
        value = data
        for key in self.path:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    @property
    def available(self):
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if self.key in ('setpoint', 'charge_current_setpoint', 'controller_state'):
            return data.get('command', {})
        if self.key in ('planned_charge_hours', 'fictive_plan'):
            plan = data.get('plan' if self.key=='planned_charge_hours' else 'comparison_plan', {})
            return {key:plan.get(key) for key in ('status','reason','updated_at','horizon','windows','physical_authority','comparison_policy','load_model','solar_source')}
        if self.key=='solar_charge':
            from zoneinfo import ZoneInfo
            day = instant(data['updated_at']).astimezone(ZoneInfo(data['time_zone'])).date().isoformat()
            row = next((r for r in data['metrics']['days'] if r['date']==day), {})
            return {'date':day, 'coverage_seconds':row.get('solar_charge_coverage_seconds', 0),
                    'basis':'Solar-first estimate with entered AC/DC efficiencies; DC PV serves converted home load first. Mixed PV without a split is unavailable. Partial coverage, not a dedicated meter.'}
        metrics = data.get('metrics', {})
        return {key:metrics.get(key) for key in ('regret_date','regret_status','regret_basis','comparison_basis',
            'paired_days','scored_days','horizon_12h_samples','horizon_24h_samples','pinball_p50_samples','pinball_p80_samples')}
