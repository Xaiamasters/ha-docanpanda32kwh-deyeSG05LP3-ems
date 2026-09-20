"""Admin-only dashboard data, settings export and bounded commissioning actions."""
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

    async def post(self,request,entry_id,kind):
        if not request[KEY_HASS_USER].is_admin:raise web.HTTPForbidden()
        if kind!='control':raise web.HTTPNotFound()
        if request.content_type!='application/json' or (request.content_length or 0)>4096:
            raise web.HTTPBadRequest()
        entry=request.app['hass'].config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain!=DOMAIN or not getattr(entry,'runtime_data',None):raise web.HTTPNotFound()
        coordinator=entry.runtime_data
        if not coordinator.control:return self.json({'error':'direct_equipment_and_controller_plan_required'},status_code=409)
        from .control_device import DeviceError
        try:
            import json
            raw=await request.content.read(4097)
            if len(raw)>4096:raise ValueError()
            body=json.loads(raw)
            if not isinstance(body,dict) or set(body)!={'action','data'} or not isinstance(body['data'],dict):raise ValueError()
            result=await coordinator.control.command(body['action'],body['data'])
            # Activation already runs a policy tick. Do not run another just to
            # render the response; the next scheduled poll refreshes all inputs.
            from .control_host import reported_plan
            status=coordinator.control.status()
            coordinator.async_set_updated_data({**coordinator.data,'control':status,
                'plan':reported_plan(coordinator.data.get('plan',{}),status),
                'mode':coordinator.control.mode,'physical_authority':coordinator.control.store.get('active',False)})
            return self.json(result,headers={'Cache-Control':'no-store'})
        except DeviceError as exc:return self.json({'error':str(exc)},status_code=409)
        except (ValueError,TypeError,KeyError):return self.json({'error':'invalid_control_request'},status_code=400)
        except Exception:return self.json({'error':'control_operation_failed'},status_code=503)

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
    path='docan-deye-ems-'+entry.entry_id.lower()
    existing=hass.data.get(frontend.DATA_PANELS,{}).get(path)
    if existing:
        config={**existing.config,'entry_id':entry.entry_id,
                '_panel_custom':{**existing.config['_panel_custom'],'module_url':f'{BASE}/panel.js?v={VERSION}'}}
        frontend.async_register_built_in_panel(hass,component_name='custom',frontend_url_path=path,
            sidebar_title=entry.title,sidebar_icon='mdi:lightning-bolt',config=config,require_admin=True,update=True)
        return
    await panel_custom.async_register_panel(hass, frontend_url_path=path,
        webcomponent_name='docan-deye-ems-panel', sidebar_title=entry.title, sidebar_icon='mdi:lightning-bolt',
        module_url=f'{BASE}/panel.js?v={VERSION}', config={'entry_id': entry.entry_id}, require_admin=True)


def async_remove_dashboard(hass, entry):
    frontend.async_remove_panel(hass, 'docan-deye-ems-' + entry.entry_id.lower(),warn_if_unknown=False)
