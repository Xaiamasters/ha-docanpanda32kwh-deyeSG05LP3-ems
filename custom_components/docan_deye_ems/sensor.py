"""Numeric observations plus a structured read-only plan."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from .const import MEASUREMENTS
from .entity import HouseholdEntity
from .equipment import direct_keys
from .docan import BATTERY_KEYS


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities([Measurement(c, key, name, unit) for key, (name, unit) in MEASUREMENTS.items() if key in c.settings['bindings'] or key in direct_keys(c.settings) or (c.settings['battery']['source']=='docan_usb' and key in BATTERY_KEYS)]
                       + [Measurement(c, 'import_price', 'Electricity import price', c.settings['pricing']['currency'] + '/kWh'), Plan(c)]
                       + ([Estimate(c,key,name,unit,path) for key,name,unit,path in ESTIMATES]+[DailyStatistics(c)] if c.settings['plan']['source']=='forecast_shadow' else []))


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
        super().__init__(coordinator, 'plan', 'Observed or shadow plan')

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
        for key in self.path:
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
