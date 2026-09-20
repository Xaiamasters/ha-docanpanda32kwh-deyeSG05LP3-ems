"""Provider failure boundaries, timezone selection and private export contract."""
import sys,unittest,copy
from pathlib import Path
from datetime import datetime,date,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from custom_components.docan_deye_ems.pricing import PriceReader,tibber_query,HOMES_QUERY
from custom_components.docan_deye_ems.model import InputError,day_bounds
from custom_components.docan_deye_ems.settings import portable_export,validate_document

class Providers(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.cfg={'provider':'tibber','token':'synthetic-not-a-real-credential','home_id':'synthetic-home','currency':'EUR','basis':'all_in','tax':0,'fee':0,'vat':0,'export_fee':0}
  self.now=datetime(2026,9,19,8,tzinfo=timezone.utc)
 def tibber(self,day):
  a,b=day_bounds(day,'Europe/Amsterdam');rows=[]
  while a<b:
   rows.append({'startsAt':a.isoformat(),'total':-.05 if len(rows)<20 else .25,'currency':'EUR'});a+=timedelta(minutes=15)
  return {'home':{'currentSubscription':{'priceInfo':{'today':rows,'tomorrow':[]}}}}
 async def test_tibber_full_day_cached_and_negative_prices(self):
  with patch('custom_components.docan_deye_ems.pricing.tibber_query',new=AsyncMock(return_value=self.tibber(self.now.date()))) as query:
   reader=PriceReader(None,self.cfg,'Europe/Amsterdam');a=await reader.read(self.now);b=await reader.read(self.now+timedelta(minutes=30))
   self.assertEqual(len(a['periods']),96);self.assertIs(a,b);self.assertEqual(query.await_count,1);self.assertLess(a['periods'][0]['import'],0)
 async def test_missing_last_tibber_quarter_never_stretched(self):
  payload=self.tibber(self.now.date());payload['home']['currentSubscription']['priceInfo']['today'].pop()
  with patch('custom_components.docan_deye_ems.pricing.tibber_query',new=AsyncMock(return_value=payload)):
   with self.assertRaisesRegex(InputError,'incomplete_price_day'):await PriceReader(None,self.cfg,'Europe/Amsterdam').read(self.now)
 async def test_tibber_currency_mismatch(self):
  payload=self.tibber(self.now.date());payload['home']['currentSubscription']['priceInfo']['today'][0]['currency']='NOK'
  with patch('custom_components.docan_deye_ems.pricing.tibber_query',new=AsyncMock(return_value=payload)):
   with self.assertRaisesRegex(InputError,'currency_mismatch'):await PriceReader(None,self.cfg,'Europe/Amsterdam').read(self.now)
 async def test_auth_error_preserved_and_redirects_refused(self):
  class Response:
   status=401
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  client=SimpleNamespace(post=lambda *a,**kw:Response())
  with patch('custom_components.docan_deye_ems.pricing.async_get_clientsession',return_value=client):
   with self.assertRaisesRegex(InputError,'invalid_auth'):await tibber_query(None,self.cfg['token'],HOMES_QUERY)
  with self.assertRaisesRegex(InputError,'unsupported_query'):await tibber_query(None,'synthetic','mutation { changeSomething }')
 async def test_nordpool_uses_local_delivery_date_at_utc_boundary(self):
  now=datetime(2026,9,18,22,30,tzinfo=timezone.utc);a,b=day_bounds(date(2026,9,19),'Europe/Amsterdam');entries=[]
  while a<b:entries.append(SimpleNamespace(start=a,end=a+timedelta(minutes=15),entry={'NL':100}));a+=timedelta(minutes=15)
  call=AsyncMock(return_value=SimpleNamespace(prices_final=True,currency='EUR',entries=entries))
  cfg={**self.cfg,'provider':'nordpool','area':'NL','basis':'spot','tax':.1,'fee':.01,'vat':21}
  with patch('custom_components.docan_deye_ems.pricing.async_get_clientsession',return_value=None),patch('custom_components.docan_deye_ems.pricing.NordPoolClient',return_value=SimpleNamespace(async_get_delivery_period=call)):
   result=await PriceReader(None,cfg,'Europe/Amsterdam').read(now)
   self.assertEqual(call.call_args.args[0].date(),date(2026,9,19));self.assertAlmostEqual(result['periods'][0]['import'],.2541)
 def test_portable_export_never_includes_token_or_mutates_entry(self):
  source={'pricing':dict(self.cfg),'location':{'enabled':True,'address':'Synthetic private label'}}
  result=portable_export(source)
  self.assertNotIn('token',result['settings']['pricing']);self.assertEqual(source['pricing']['token'],self.cfg['token']);self.assertFalse(result['credentials_included'])
 def test_malformed_settings_always_safe_error(self):
  for value in (None,[],True,{'schema':1,'installation_id':None},{'schema':1,'installation_id':['bad']}):
   with self.assertRaises(InputError):validate_document(value)

if __name__=='__main__':unittest.main(verbosity=2)
