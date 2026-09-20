"""Public household defaults contain hardware facts, never a site's setup."""
from homeassistant.const import Platform
DOMAIN='docan_deye_ems'
NAME='Docan Panda & Deye EMS'
VERSION='0.5.0.dev2'
PLATFORMS=[Platform.SENSOR,Platform.BINARY_SENSOR]
MODELS={f'SUN-{n}K-SG05LP3-EU-SM2':n for n in (6,8,10,12)}
DEFAULT_MODEL='SUN-10K-SG05LP3-EU-SM2'
BATTERY_MODEL='Docan Panda 32 kWh'
CAPACITY_KWH=32.0
MEASUREMENTS={
 'battery_soc':('Battery state of charge','%'),
 'battery_power':('Battery power','W'),
 'battery_voltage':('Battery voltage','V'),
 'battery_current':('Battery current','A'),
 'inverter_power':('Inverter AC power','W'),
 'load_power':('Whole-home consumption','W'),
 'grid_power':('Grid import/export power','W'),
 'solar_power':('Total solar production','W'),
 'solar_today':('Solar energy today','kWh'),
 'load_today':('Home energy today','kWh'),
 'import_today':('Grid import today','kWh'),
 'export_today':('Grid export today','kWh'),
 'battery_temperature':('Battery temperature','°C'),
 'battery_cell_delta':('Battery cell voltage spread','V'),
 'battery_ac_power':('Dedicated battery AC power','W'),
}
CORE_KEYS=tuple(list(MEASUREMENTS)[:7])
