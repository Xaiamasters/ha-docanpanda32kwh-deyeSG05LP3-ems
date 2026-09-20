"""Fixed-endpoint price reads only. No provider mutation or hardware transport."""
from __future__ import annotations
import asyncio
from datetime import datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pynordpool import NordPoolClient,NordPoolError,Currency
from .model import InputError,day_bounds,instant,normalize_periods
from .const import VERSION

TIBBER_URL='https://api.tibber.com/v1-beta/gql'
HOMES_QUERY='query DocanDeyeHomes { viewer { homes { id appNickname } } }'
PRICES_QUERY='query DocanDeyePrices($id: ID!) { viewer { home(id: $id) { currentSubscription { priceInfo(resolution: QUARTER_HOURLY) { today { startsAt total currency } tomorrow { startsAt total currency } } } } } }'

async def tibber_query(hass,token,query,variables=None):
    if query not in (HOMES_QUERY,PRICES_QUERY):raise InputError('unsupported_query')
    if not isinstance(token,str) or not token.strip():raise InputError('invalid_auth')
    try:
        async with asyncio.timeout(15):
            async with async_get_clientsession(hass).post(TIBBER_URL,
                headers={'Authorization':'Bearer '+token,'User-Agent':f'DocanDeyeEMS/{VERSION}'},
                json={'query':query,'variables':variables or {}},allow_redirects=False) as response:
                if response.status in (401,403):raise InputError('invalid_auth')
                if response.status!=200:raise InputError('provider_unavailable')
                data=await response.json()
        if data.get('errors'):raise InputError('provider_query_failed')
        return data['data']['viewer']
    except InputError:
        raise
    except (aiohttp.ClientError,TimeoutError,KeyError,ValueError,TypeError):
        raise InputError('provider_unavailable') from None

async def tibber_homes(hass,token):
    viewer=await tibber_query(hass,token,HOMES_QUERY)
    homes=[{'value':str(h['id']),'label':str(h.get('appNickname') or 'Tibber home')[:100]}
           for h in viewer.get('homes',[]) if h.get('id')]
    if not homes:raise InputError('no_tibber_homes')
    return homes

class PriceReader:
    def __init__(self,hass,settings,zone):
        self.hass=hass;self.settings=settings;self.zone=zone;self.cache={};self.retry_at=None
        self.tomorrow_retry_at = None

    def export_prices(self, periods):
        mode = self.settings.get('export_mode')
        return [{**p, **({'export': self.settings['net_export_price']} if mode == 'fixed' else {}),
                 'origin': 'published'} for p in periods]

    async def horizon(self, now, current, price_state=None):
        """Return today's curve plus a complete published tomorrow, when available."""
        day = now.astimezone(ZoneInfo(self.zone)).date()
        tomorrow = day+timedelta(days=1)
        if self.settings['provider'] == 'sensor':
            try:
                cfg = self.settings
                rows = price_state.attributes.get('periods', [])
                future = normalize_periods(rows,tomorrow,self.zone,currency=cfg['currency'],
                    unit=price_state.attributes.get('unit_of_measurement',''),basis=cfg['basis'],
                    tax=cfg['tax'],fee=cfg['fee'],vat=cfg['vat'],export_fee=cfg['export_fee'])
                self.cache[tomorrow] = {'periods': future}
            except (InputError, AttributeError, TypeError, KeyError):
                self.cache.pop(tomorrow, None)
        elif tomorrow not in self.cache and (not self.tomorrow_retry_at or now >= self.tomorrow_retry_at):
            self.tomorrow_retry_at = now+timedelta(hours=1)
            try:
                if self.settings['provider'] == 'tibber':
                    saved = self.cache.pop(day, None)
                    try:
                        await self.read(now)
                    finally:
                        if saved and day not in self.cache:
                            self.cache[day] = saved
                else:
                    future_reader = PriceReader(self.hass,self.settings,self.zone)
                    next_now = datetime.combine(tomorrow,time(12),tzinfo=ZoneInfo(self.zone))
                    self.cache[tomorrow] = await future_reader.read(next_now)
            except (InputError, ValueError, KeyError, TypeError):
                pass  # Today's published prices remain usable; no fabricated tomorrow.
        periods = current['periods'] + self.cache.get(tomorrow, {}).get('periods', [])
        return self.export_prices(periods)

    async def read(self,now,price_state=None):
        cfg=self.settings;day=now.astimezone(ZoneInfo(self.zone)).date();provider=cfg['provider']
        if provider=='sensor':
            if price_state is None or price_state.state in ('unavailable','unknown'):raise InputError('price_source_missing')
            attrs=price_state.attributes
            if attrs.get('date')!=day.isoformat():raise InputError('wrong_price_day')
            rows=attrs.get('periods')
            if not isinstance(rows,list) or len(rows)>400:raise InputError('invalid_price_rows')
            periods=normalize_periods(rows,day,self.zone,currency=cfg['currency'],unit=attrs.get('unit_of_measurement',''),basis=cfg['basis'],tax=cfg['tax'],fee=cfg['fee'],vat=cfg['vat'],export_fee=cfg['export_fee'])
            return {'provider':provider,'date':str(day),'currency':cfg['currency'],'periods':periods,'basis':cfg['basis']}
        if day in self.cache:return self.cache[day]
        if self.retry_at and now<self.retry_at:raise InputError('provider_retry_pending')
        self.retry_at=now+timedelta(minutes=5)
        if provider=='tibber':
            viewer=await tibber_query(self.hass,cfg.get('token'),PRICES_QUERY,{'id':cfg['home_id']})
            info=(viewer.get('home',{}).get('currentSubscription') or {}).get('priceInfo') or {}
            for key,date_ in [('today',day),('tomorrow',day+timedelta(days=1))]:
                data=info.get(key) or []
                if not data:continue
                if any(r.get('currency')!=cfg['currency'] for r in data):raise InputError('currency_mismatch')
                data=sorted(data,key=lambda r:instant(r['startsAt']))
                # The query explicitly requests quarter-hour resolution. Never stretch
                # a missing interval to the next observed timestamp or midnight.
                rows=[{'start':r['startsAt'],'end':(instant(r['startsAt'])+timedelta(minutes=15)).isoformat(),'value':r['total']} for r in data]
                self.cache[date_]={'provider':provider,'date':str(date_),'currency':cfg['currency'],
                    'periods':normalize_periods(rows,date_,self.zone,currency=cfg['currency'],unit=cfg['currency']+'/kWh'),'basis':'all_in'}
        elif provider=='nordpool':
            try:
                async with asyncio.timeout(15):
                    delivery_day=datetime.combine(day,time(12),tzinfo=ZoneInfo(self.zone))
                    result=await NordPoolClient(async_get_clientsession(self.hass)).async_get_delivery_period(delivery_day,Currency(cfg['currency']),[cfg['area']])
                if not result.prices_final or result.currency!=cfg['currency']:raise InputError('prices_not_final')
                rows=[{'start':r.start.isoformat(),'end':r.end.isoformat(),'value':r.entry.get(cfg['area'])} for r in result.entries]
                periods=normalize_periods(rows,day,self.zone,currency=cfg['currency'],unit=cfg['currency']+'/MWh',basis='spot',tax=cfg['tax'],fee=cfg['fee'],vat=cfg['vat'],export_fee=cfg['export_fee'])
                self.cache[day]={'provider':provider,'date':str(day),'currency':cfg['currency'],'periods':periods,'basis':'spot_plus_household_tariff'}
            except InputError:raise
            except (NordPoolError,aiohttp.ClientError,TimeoutError,KeyError,ValueError):raise InputError('provider_unavailable') from None
        else:raise InputError('unsupported_provider')
        self.cache={d:v for d,v in self.cache.items() if d>=day}
        if day not in self.cache:raise InputError('prices_unavailable')
        return self.cache[day]
