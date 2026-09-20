import sys,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import aiohttp
from yarl import URL
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from custom_components.docan_deye_ems.forecast import SolarReader,power_curve,sensor_forecast,_VerifiedSession
from custom_components.docan_deye_ems.model import InputError
from custom_components.docan_deye_ems.optimizer import integrate

class ForecastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now=datetime(2026,9,20,10,tzinfo=timezone.utc)
        self.cfg={'solar_source':'forecast_solar','consent':True,'latitude':0,'longitude':0,'tilt':35,'azimuth':0}
        self.solar={'connection':'deye_dc','kwp':4}
    def estimate(self):
        day=self.now.replace(hour=0)
        watts={day+timedelta(hours=h): (1000 if h==12 else 0) for h in range(25)}
        return SimpleNamespace(watts=watts,wh_days={day:1000},timezone='UTC')
    def test_power_curve_conserves_trapezoidal_energy_and_rejects_missing_night(self):
        e=self.estimate();rows=power_curve(e)
        self.assertAlmostEqual(sum(r['kwh'] for r in rows),1)
        self.assertAlmostEqual(integrate(rows,self.now,self.now+timedelta(hours=4),'kwh'),1)
        e.watts[next(iter(e.watts))]=100
        with self.assertRaises(InputError):power_curve(e)
    async def test_no_location_consent_means_no_external_call(self):
        reader=SolarReader(None,{**self.cfg,'consent':False},self.solar)
        with patch('custom_components.docan_deye_ems.forecast.ForecastSolar') as client:
            with self.assertRaisesRegex(InputError,'consent'):await reader.read(self.now,self.now,self.now+timedelta(hours=1))
            client.assert_not_called()
    async def test_cache_rate_limit_and_stale_fails(self):
        call=AsyncMock(return_value=self.estimate())
        reader=SolarReader(None,self.cfg,self.solar)
        with patch('custom_components.docan_deye_ems.forecast.async_get_clientsession',return_value=None),patch('custom_components.docan_deye_ems.forecast.ForecastSolar',return_value=SimpleNamespace(estimate=call)):
            a=await reader.read(self.now,self.now,self.now+timedelta(hours=1))
            b=await reader.read(self.now+timedelta(minutes=20),self.now,self.now+timedelta(hours=1))
            self.assertIs(a,b);self.assertEqual(call.await_count,1)
            call.side_effect=TimeoutError('synthetic')
            with self.assertRaisesRegex(InputError,'solar_forecast_unavailable'):
                await reader.read(self.now+timedelta(hours=3),self.now,self.now+timedelta(hours=1))
    def test_stale_sensor_and_gaps_rejected(self):
        periods=[{'start':self.now.isoformat(),'end':(self.now+timedelta(hours=1)).isoformat(),'kwh':1}]
        state=SimpleNamespace(state='ready',attributes={'updated_at':self.now.isoformat(),'unit_of_measurement':'kWh','periods':periods})
        self.assertEqual(len(sensor_forecast(state,self.now)),1)
        state.attributes['updated_at']=(self.now-timedelta(hours=3)).isoformat()
        with self.assertRaises(InputError):sensor_forecast(state,self.now)

    async def test_real_client_transport_errors_are_redacted(self):
        session=SimpleNamespace(request=AsyncMock(side_effect=aiohttp.ClientResponseError(
            SimpleNamespace(real_url=URL('https://api.forecast.solar/estimate/0/0/private-placeholder')),
            (),status=500,message='synthetic private provider error')))
        reader=SolarReader(None,self.cfg,self.solar)
        with patch('custom_components.docan_deye_ems.forecast.async_get_clientsession',return_value=session):
            with self.assertRaises(InputError) as error:
                await reader.read(self.now,self.now,self.now+timedelta(hours=1))
        self.assertEqual(str(error.exception),'solar_forecast_unavailable')
        self.assertEqual(session.request.await_count,1)
        self.assertTrue(session.request.call_args.kwargs['ssl'])
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])

    async def test_forecast_transport_rejects_redirect_hosts_and_non_read_requests(self):
        session=SimpleNamespace(request=AsyncMock())
        guarded=_VerifiedSession(session)
        for method,url in [('GET','https://example.invalid/'),('POST','https://api.forecast.solar/'),('GET','http://api.forecast.solar/')]:
            with self.assertRaises(InputError):await guarded.request(method,URL(url))
        session.request.assert_not_called()

if __name__=='__main__':unittest.main()
