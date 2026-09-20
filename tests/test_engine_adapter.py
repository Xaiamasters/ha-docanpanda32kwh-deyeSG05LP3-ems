"""Native HA coordinator/store checks with synthetic controller observations."""
import copy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys,tempfile,unittest
from types import MappingProxyType
from unittest.mock import AsyncMock,patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry,entity_registry
from custom_components.docan_deye_ems.const import CORE_KEYS,DEFAULT_MODEL,DOMAIN
from custom_components.docan_deye_ems.coordinator import HouseholdCoordinator
from custom_components.docan_deye_ems.config_flow import HouseholdFlow
from custom_components.docan_deye_ems.engine_adapter import ProductionShadowAdapter
from custom_components.docan_deye_ems.engine.simulation import example_snapshot
from custom_components.docan_deye_ems.settings import validate_document,portable_export
from custom_components.docan_deye_ems.model import InputError
from control_peer import ControlPeer
from test_control_device import battery_frame
from custom_components.docan_deye_ems.docan import parse_frame


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.hass=HomeAssistant(self.temp.name)
        await self.hass.config.async_set_time_zone('Europe/Amsterdam')
        device_registry.async_setup(self.hass)
        await device_registry.async_load(self.hass,load_empty=True)
        await entity_registry.async_load(self.hass,load_empty=True)
        self.at=datetime.now(timezone.utc)
        self.local=self.at.astimezone(ZoneInfo('Europe/Amsterdam'))
        self.settings={'schema':1,'installation_id':'a'*32,'name':'Synthetic engine test','model':DEFAULT_MODEL,
                       'bms_link':'unknown','connection':'existing_ha_sensors','battery':{'source':'inverter'},
                       'diagram_reserve_soc':25,'location':{'enabled':False},'solar':{'connection':'none','kwp':0},
                       'bindings':{k:{'entity_id':'sensor.test_'+k} for k in (*CORE_KEYS,'price_curve','controller_snapshot')},
                       'signs':{'battery_power':1,'battery_current':1,'grid_power':1},'max_age':120,
                       'pricing':{'provider':'sensor','currency':'EUR','basis':'all_in','tax':0,'fee':0,'vat':0,'export_fee':0,
                                  'export_mode':'fixed','net_export_price':.05},
                       'plan':{'source':'production_shadow','capacity_kwh':32.15,'fallback_reserve_soc':64,
                               'export_power_w':7900,'round_trip_efficiency':.87,'wear_cost_per_kwh':.04,'export_price_deduction':0}}
        units={'battery_soc':'%','battery_power':'W','battery_voltage':'V','battery_current':'A','inverter_power':'W','load_power':'W','grid_power':'W'}
        values={'battery_soc':35,'battery_power':-5000,'battery_voltage':53,'battery_current':-94,'inverter_power':500,'load_power':500,'grid_power':5500}
        for key in CORE_KEYS:self.hass.states.async_set('sensor.test_'+key,values[key],{'unit_of_measurement':units[key]})
        self.frame=example_snapshot(self.local)
        self.pin={'for_date':self.local.date().isoformat(),'ceiling_pct':95,'export_clusters':[[76,80,64]],'reserve':{'pct':64}}
        self.hass.states.async_set('sensor.test_controller_snapshot','ready',{'schema':1,'updated_at':self.at.isoformat(),'snapshot':self.frame,'day_plan':self.pin})
        midnight=self.local.replace(hour=0,minute=0,second=0,microsecond=0)
        self.periods=[{'start':(midnight+timedelta(minutes=15*i)).isoformat(),
                       'end':(midnight+timedelta(minutes=15*(i+1))).isoformat(),'value':self.frame['prices'][i%96]}
                      for i in range(192)]
        self.hass.states.async_set('sensor.test_price_curve','ready',{'date':self.local.date().isoformat(),'unit_of_measurement':'EUR/kWh','periods':self.periods})
        self.entry=ConfigEntry(version=1,minor_version=1,domain=DOMAIN,title='Synthetic engine test',
                               data=self.settings,source='user',options={},unique_id='a'*32,
                               discovery_keys=MappingProxyType({}),subentries_data=[])
        self.coordinators=[]

    async def asyncTearDown(self):
        for c in self.coordinators:await c.async_shutdown()
        await self.hass.async_stop()
        self.temp.cleanup()

    async def coordinator(self):
        c=HouseholdCoordinator(self.hass,self.entry,validate_document(self.settings))
        self.coordinators.append(c)
        await c.async_initialize()
        return c

    async def test_native_coordinator_runs_engine_without_service_calls(self):
        c=await self.coordinator()
        with patch.object(type(self.hass.services),'async_call',new=AsyncMock(side_effect=AssertionError('No service calls'))):
            await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertTrue(c.data['ready'])
        self.assertEqual(c.data['plan']['mode'],'production_shadow')
        self.assertFalse(c.data['physical_authority'])
        self.assertFalse(c.data['plan']['physical_authority'])

    async def test_reload_retains_engine_state_and_config_change_resets_it(self):
        c=await self.coordinator();await c.async_refresh()
        self.assertTrue(c.production_engine.state.data['events'])
        c2=await self.coordinator()
        self.assertEqual(c.production_engine.state.data,c2.production_engine.state.data)
        modified=copy.deepcopy(self.settings);modified['plan']['capacity_kwh']=32
        changed=ProductionShadowAdapter(self.hass,modified);await changed.initialize()
        self.assertEqual(changed.state.data,{'events':[]})

    async def test_missing_or_stale_controller_frame_hides_plan(self):
        c=await self.coordinator()
        self.hass.states.async_set('sensor.test_controller_snapshot','unavailable')
        await c.async_refresh()
        self.assertFalse(c.data['ready'])
        self.assertEqual(c.data['errors']['plan'],'controller_snapshot_unavailable')
        self.hass.states.async_set('sensor.test_controller_snapshot','ready',{'schema':1,'updated_at':(self.at-timedelta(minutes=10)).isoformat(),'snapshot':self.frame,'day_plan':self.pin})
        await c.async_refresh()
        self.assertEqual(c.data['errors']['plan'],'controller_snapshot_stale')

    async def test_source_ages_include_transport_delay(self):
        c=await self.coordinator()
        self.frame['ages']['docan']=290
        self.hass.states.async_set('sensor.test_controller_snapshot','ready',{'schema':1,'updated_at':(self.at-timedelta(seconds=20)).isoformat(),'snapshot':self.frame,'day_plan':self.pin})
        await c.async_refresh()
        self.assertEqual(c.data['plan']['status'],'IDLE')
        self.assertFalse(c.data['ready'])
        self.assertIn('docan stale',c.data['plan']['reason'])

    async def test_native_setup_form_and_settings_export(self):
        flow=HouseholdFlow();flow.hass=self.hass;flow.d=copy.deepcopy(self.settings)
        result=await flow.async_step_production()
        self.assertEqual(result['step_id'],'production')
        supplied={k:v for k,v in self.settings['plan'].items() if k!='source'}
        supplied['controller_snapshot']='sensor.test_controller_snapshot'
        result=await flow.async_step_production(supplied)
        self.assertEqual(result['step_id'],'control_limits')
        self.assertFalse(result['errors'])
        self.assertEqual(portable_export(flow.d)['settings']['plan']['source'],'production_shadow')

    async def test_live_or_unknown_mode_not_accepted_by_settings(self):
        for mode in ('live','production_live'):
            data=copy.deepcopy(self.settings);data['plan']['source']=mode
            with self.assertRaises(InputError):validate_document(data)

    async def test_bad_saved_state_is_not_silently_discarded(self):
        adapter=ProductionShadowAdapter(self.hass,self.settings)
        await adapter.store.async_save({'context':adapter.context,'state':{'events':None}})
        with self.assertRaisesRegex(InputError,'invalid_saved_engine_state'):
            await adapter.initialize()

    async def test_model_profiles_bound_currents_and_program_power(self):
        for n in (6,8,12):
            data=copy.deepcopy(self.settings)
            data['model']=f'SUN-{n}K-SG05LP3-EU-SM2'
            data['plan']['export_power_w']=min(n*1000,7900)
            validated=validate_document(data)
            self.assertLessEqual(validated['control']['program_power_w'],n*1000)
            self.assertLessEqual(validated['control']['charge_current_a']*55.2,n*1000)

    async def test_direct_adapter_builds_frame_without_supplied_sensor_or_writes(self):
        peer=await ControlPeer('modbus_tcp').start()
        try:
            self.settings.update(connection='direct_deye',equipment=peer.connection(),
                                 battery={'source':'docan_usb','port':'/dev/ttyUSB0','address':0})
            self.settings['bindings']={'price_curve':self.settings['bindings']['price_curve']}
            c=await self.coordinator()
            battery=parse_frame(battery_frame(),0,controller=True)
            c.production_engine.observation.battery_reader=lambda _:battery
            with patch.object(type(self.hass.services),'async_call',new=AsyncMock(side_effect=AssertionError('No service calls'))),\
                 patch('custom_components.docan_deye_ems.coordinator.read_equipment',side_effect=AssertionError('No duplicate polling')),\
                 patch('custom_components.docan_deye_ems.coordinator.read_docan',side_effect=AssertionError('No duplicate BMS polling')):
                for _ in range(3):await c.async_refresh()
            self.assertTrue(c.data['ready'])
            self.assertEqual(c.data['values']['battery_soc'],55)
            self.assertEqual(c.data['plan']['mode'],'production_shadow')
            self.assertFalse(c.data['physical_authority'])
            self.assertEqual(peer.writes,[])
            peer.fault='disconnect'
            await c.async_refresh()
            self.assertFalse(c.data['ready'])
            self.assertIsNone(c.data['values']['battery_soc'])
            self.assertEqual(c.data['plan']['windows'],[])
        finally:await peer.close()

    async def test_direct_setup_omits_external_snapshot_requirement(self):
        self.settings.update(connection='direct_deye',equipment={'transport':'modbus_tcp','host':'127.0.0.1','port':502,'unit':1},
                             battery={'source':'docan_usb','port':'/dev/ttyUSB0','address':0})
        self.settings['bindings']={'price_curve':self.settings['bindings']['price_curve']}
        flow=HouseholdFlow();flow.hass=self.hass;flow.d=copy.deepcopy(self.settings)
        form=await flow.async_step_production()
        self.assertNotIn('controller_snapshot',{str(k) for k in form['data_schema'].schema})
        result=await flow.async_step_production({k:v for k,v in self.settings['plan'].items() if k!='source'})
        self.assertEqual(result['step_id'],'control_limits')
        self.assertNotIn('controller_snapshot',flow.d['bindings'])


if __name__=='__main__':unittest.main()
