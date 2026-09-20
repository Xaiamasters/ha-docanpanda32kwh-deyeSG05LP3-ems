"""Stable entity identity for backup restores and portable imports."""
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, VERSION, BATTERY_MODEL


class HouseholdEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        self.key = key
        d = coordinator.settings
        self._attr_name = name
        self._attr_unique_id = f'{d["installation_id"]}_{key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, d['installation_id'])},
                                           name=d['name'], manufacturer='Community project',
                                           model=f'{BATTERY_MODEL} / {d["model"]}', sw_version=VERSION)
