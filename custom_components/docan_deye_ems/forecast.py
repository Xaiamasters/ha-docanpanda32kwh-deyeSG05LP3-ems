"""Read-only solar inputs. Coordinates leave HA only with explicit consent."""
from __future__ import annotations
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from forecast_solar import ForecastSolar, ForecastSolarError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .model import InputError, instant, number, day_bounds


class _VerifiedSession:
    """Keep TLS verification and forbid redirects for coordinate-bearing URLs."""
    def __init__(self, session):
        self.session = session

    async def request(self, method, url, **kwargs):
        if method != 'GET' or url.scheme != 'https' or url.host != 'api.forecast.solar':
            raise InputError('invalid_forecast_endpoint')
        kwargs.update(ssl=True, allow_redirects=False)
        return await self.session.request(method, url, **kwargs)


def sensor_forecast(state, now):
    if state is None or state.state in ('unknown', 'unavailable'):
        raise InputError('solar_forecast_unavailable')
    attrs = state.attributes
    updated = instant(attrs.get('updated_at'))
    if not -60 <= (now-updated).total_seconds() <= 7200:
        raise InputError('solar_forecast_stale')
    raw = attrs.get('periods')
    if not isinstance(raw, list) or not raw or len(raw) > 300 or attrs.get('unit_of_measurement') != 'kWh':
        raise InputError('invalid_solar_forecast')
    rows, previous = [], None
    for r in raw:
        a, b, energy = instant(r['start']), instant(r['end']), number(r['kwh'])
        if not 0 < (b-a).total_seconds() <= 3600 or energy < 0 or (previous and previous != a):
            raise InputError('invalid_solar_forecast')
        rows.append({'start': a.isoformat(), 'end': b.isoformat(), 'kwh': energy})
        previous = b
    return rows


def power_curve(estimate):
    """Integrate the provider's power curve into energy; require complete day markers.

    Only days explicitly present in the provider response are eligible. Zero
    endpoints are required before padding the night outside daylight samples.
    """
    zone = ZoneInfo(estimate.timezone)
    points = []
    for at, watts in estimate.watts.items():
        at = at.replace(tzinfo=zone) if at.tzinfo is None else at
        value = number(watts)
        if value < 0:
            raise InputError('invalid_solar_forecast')
        points.append((at.astimezone(timezone.utc), value))
    rows = []
    days = sorted({at.date() for at in estimate.wh_days})
    for day in days:
        start, end = day_bounds(day, zone.key)
        selected = sorted((a, w) for a, w in points if start <= a <= end)
        if len(selected) < 2 or selected[0][1] != 0 or selected[-1][1] != 0:
            raise InputError('solar_forecast_day_incomplete')
        if selected[0][0] > start:
            selected.insert(0, (start, 0))
        if selected[-1][0] < end:
            selected.append((end, 0))
        for (a, wa), (b, wb) in zip(selected, selected[1:]):
            if a >= b or ((wa or wb) and (b-a).total_seconds() > 7200):
                raise InputError('solar_forecast_gap')
            cursor = a
            while cursor < b:
                edge = min(b, cursor+timedelta(minutes=15))
                duration = (b-a).total_seconds()
                left = wa+(wb-wa)*(cursor-a).total_seconds()/duration
                right = wa+(wb-wa)*(edge-a).total_seconds()/duration
                rows.append({'start': cursor.isoformat(), 'end': edge.isoformat(),
                             'kwh': (left+right)/2*(edge-cursor).total_seconds()/3600000})
                cursor = edge
    if not rows:
        raise InputError('solar_forecast_unavailable')
    return rows


class SolarReader:
    def __init__(self, hass, settings, solar):
        self.hass, self.settings, self.solar = hass, settings, solar
        self.cached, self.updated, self.retry_at = None, None, None

    async def read(self, now, start, end, state=None):
        cfg = self.settings
        if cfg['solar_source'] == 'none':
            if self.solar['connection'] != 'none':
                raise InputError('solar_forecast_required')
            return [{'start': start.isoformat(), 'end': end.isoformat(), 'kwh': 0}]
        if cfg['solar_source'] == 'sensor':
            return sensor_forecast(state, now)
        if not cfg.get('consent'):
            raise InputError('forecast_location_consent_required')
        if self.cached and now-self.updated < timedelta(hours=1):
            return self.cached
        if self.retry_at and now < self.retry_at:
            if self.cached and now-self.updated < timedelta(hours=2):
                return self.cached
            raise InputError('solar_forecast_retry_pending')
        self.retry_at = now+timedelta(hours=1)
        try:
            client = ForecastSolar(latitude=cfg['latitude'], longitude=cfg['longitude'],
                declination=cfg['tilt'], azimuth=cfg['azimuth'], kwp=self.solar['kwp'],
                session=_VerifiedSession(async_get_clientsession(self.hass)))
            async with asyncio.timeout(20):
                estimate = await client.estimate()
            self.cached, self.updated = power_curve(estimate), now
            return self.cached
        except (ForecastSolarError, aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            # Never propagate endpoint URLs (coordinates) or provider exceptions.
            if self.cached and self.updated and now-self.updated < timedelta(hours=2):
                return self.cached
            raise InputError('solar_forecast_unavailable') from None
