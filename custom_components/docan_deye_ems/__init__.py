"""Household telemetry, planning and explicitly commissioned equipment control."""
from .const import PLATFORMS
from .coordinator import HouseholdCoordinator
from .dashboard import async_add_dashboard, async_remove_dashboard
from .settings import validate_document
from homeassistant.helpers import issue_registry as ir
from .const import DOMAIN


async def async_setup_entry(hass, entry):
    coordinator = HouseholdCoordinator(hass, entry, validate_document(dict(entry.data)))
    await coordinator.async_initialize()
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_add_dashboard(hass, entry)
    return True


async def async_unload_entry(hass, entry):
    # Keep the integration and independent guards available when a STOP could
    # not be verified. Unloading platforms first would hide the failed stop.
    control=entry.runtime_data.control
    if control:
        from .control_device import WriteDenied
        try:await control.close()
        except WriteDenied:return False
        entry.runtime_data.control=None
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Preserve the route during reload so HA does not detach a visible
        # custom panel while it is receiving state updates. Removal is explicit.
        if entry.disabled_by is not None:async_remove_dashboard(hass,entry)
        await entry.runtime_data.async_shutdown()
        ir.async_delete_issue(hass, DOMAIN, f'{entry.entry_id}_inputs')
        ir.async_delete_issue(hass, DOMAIN, f'{entry.entry_id}_control_stop')
        return True
    return False


async def async_remove_entry(hass,entry):
    async_remove_dashboard(hass,entry)
    from homeassistant.helpers.storage import Store
    await Store(hass,1,DOMAIN+'.'+entry.data['installation_id']+'.learning').async_remove()
    await Store(hass,1,DOMAIN+'.'+entry.data['installation_id']+'.engine').async_remove()
