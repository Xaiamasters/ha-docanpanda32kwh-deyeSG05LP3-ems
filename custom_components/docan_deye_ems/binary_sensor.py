"""Readiness is observational, never permission to operate equipment."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import HouseholdEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Ready(entry.runtime_data)])


class Ready(HouseholdEntity, BinarySensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, 'ready', 'Observations ready')

    @property
    def is_on(self):
        return self.coordinator.data['ready']

    @property
    def extra_state_attributes(self):
        return {'physical_authority':self.coordinator.data['physical_authority'], 'mode':self.coordinator.data['mode'], 'errors': self.coordinator.data['errors']}
