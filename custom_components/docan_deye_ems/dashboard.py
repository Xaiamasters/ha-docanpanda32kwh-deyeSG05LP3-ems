"""Admin-only observations and portable settings export. No write endpoint."""
from pathlib import Path
from aiohttp import web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import HomeAssistantView, StaticPathConfig, KEY_HASS_USER
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN, VERSION, MEASUREMENTS
from .settings import portable_export

BASE = '/docan_deye_ems_static'


class HouseholdView(HomeAssistantView):
    url = '/api/docan_deye_ems/{entry_id}/{kind}'
    name = 'api:docan_deye_ems:read'
    requires_auth = True

    async def get(self, request, entry_id, kind):
        if not request[KEY_HASS_USER].is_admin:
            raise web.HTTPForbidden()
        hass = request.app['hass']
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not getattr(entry, 'runtime_data', None):
            raise web.HTTPNotFound()
        if kind == 'settings':
            return self.json(portable_export(dict(entry.data)), headers={'Cache-Control': 'no-store'})
        if kind != 'snapshot':
            raise web.HTTPNotFound()
        data = dict(entry.runtime_data.data)
        registry = er.async_get(hass)
        data['history_entities'] = {key: registry.async_get_entity_id('sensor', DOMAIN, f'{entry.data["installation_id"]}_{key}') for key in (*MEASUREMENTS, 'import_price')}
        return self.json(data, headers={'Cache-Control': 'no-store'})


async def async_add_dashboard(hass, entry):
    if not hass.data.get(DOMAIN + '_http'):
        await hass.http.async_register_static_paths([StaticPathConfig(BASE, str(Path(__file__).parent / 'frontend'), cache_headers=False)])
        hass.http.register_view(HouseholdView())
        hass.data[DOMAIN + '_http'] = True
    await panel_custom.async_register_panel(hass, frontend_url_path='docan-deye-ems-' + entry.entry_id.lower(),
        webcomponent_name='docan-deye-ems-panel', sidebar_title=entry.title, sidebar_icon='mdi:lightning-bolt',
        module_url=f'{BASE}/panel.js?v={VERSION}', config={'entry_id': entry.entry_id}, require_admin=True)


def async_remove_dashboard(hass, entry):
    frontend.async_remove_panel(hass, 'docan-deye-ems-' + entry.entry_id.lower())
