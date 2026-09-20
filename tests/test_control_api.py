"""Admin boundary and strict payloads for the installed dashboard endpoint."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock
from aiohttp import web
from homeassistant.components.http import KEY_HASS_USER
from custom_components.docan_deye_ems.dashboard import HouseholdView
from custom_components.docan_deye_ems.const import DOMAIN
from custom_components.docan_deye_ems.control_device import WriteDenied


class Request(dict):
    def __init__(self,entry,payload,*,admin=True,raw=None):
        super().__init__({KEY_HASS_USER:SimpleNamespace(is_admin=admin)})
        self.app={'hass':SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _:entry))}
        body=json.dumps(payload).encode() if raw is None else raw
        self.content_type='application/json';self.content_length=len(body)
        self.content=SimpleNamespace(read=AsyncMock(return_value=body))


class ControlApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        status={'active':False,'mode':'shadow','commissioned':True}
        self.control=SimpleNamespace(command=AsyncMock(return_value=status),status=lambda:status,mode='shadow',store=SimpleNamespace(get=lambda *_:False))
        self.coordinator=SimpleNamespace(control=self.control,data={'ready':True},async_set_updated_data=Mock())
        self.entry=SimpleNamespace(domain=DOMAIN,runtime_data=self.coordinator)
        self.view=HouseholdView()

    async def test_non_admin_has_no_command_access(self):
        with self.assertRaises(web.HTTPForbidden):await self.view.post(Request(self.entry,{'action':'stop','data':{}},admin=False),'test','control')
        self.control.command.assert_not_awaited()

    async def test_no_generic_service_or_register_payload(self):
        for payload in ({'register':130,'value':1},{'action':'enable_live','data':{},'register':130},
                        {'action':'stop','data':[]},['stop']):
            response=await self.view.post(Request(self.entry,payload),'test','control')
            self.assertEqual(response.status,400)
        self.control.command.assert_not_awaited()
        with self.assertRaises(web.HTTPBadRequest):await self.view.post(Request(self.entry,None,raw=b'x'*4097),'test','control')

    async def test_valid_request_publishes_actual_mode_without_extra_tick(self):
        response=await self.view.post(Request(self.entry,{'action':'stop','data':{}}),'test','control')
        self.assertEqual(response.status,200)
        self.control.command.assert_awaited_once_with('stop',{})
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertFalse(self.coordinator.async_set_updated_data.call_args.args[0]['physical_authority'])

    async def test_rejections_and_unexpected_errors_do_not_expose_exception_details(self):
        self.control.command.side_effect=WriteDenied('commissioning_required')
        response=await self.view.post(Request(self.entry,{'action':'enable_live','data':{}}),'test','control')
        self.assertEqual(response.status,409)
        self.control.command.side_effect=RuntimeError('PRIVATE_SYNTHETIC_DETAIL')
        response=await self.view.post(Request(self.entry,{'action':'enable_live','data':{}}),'test','control')
        self.assertEqual(response.status,503)
        self.assertNotIn('PRIVATE_SYNTHETIC_DETAIL',response.text)
