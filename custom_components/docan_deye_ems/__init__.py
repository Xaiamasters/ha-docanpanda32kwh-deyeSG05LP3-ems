"""Read-only household observations and a shadow dashboard."""
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
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        async_remove_dashboard(hass, entry)
        await entry.runtime_data.async_shutdown()
        ir.async_delete_issue(hass, DOMAIN, f'{entry.entry_id}_inputs')
        return True
    return False


async def async_remove_entry(hass,entry):
    from homeassistant.helpers.storage import Store
    await Store(hass,1,DOMAIN+'.'+entry.data['installation_id']+'.learning').async_remove()
    await Store(hass,1,DOMAIN+'.'+entry.data['installation_id']+'.engine').async_remove()
