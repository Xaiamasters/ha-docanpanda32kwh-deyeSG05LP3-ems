"""Published delivery intervals with separate import and net export tariffs."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo


def exports(prices):
    return getattr(prices, 'exports', prices)


class DeliveryPrices(list):
    """A list-compatible import curve retaining actual quarter-hour instants.

    Every supplied interval must be published, contiguous and inside the full
    local delivery day. DST never creates a fabricated price or extra energy.
    Firmware programs use wall clocks, so on transition days charging is planned
    after the clock change. The repeated hour cannot accidentally run twice.
    """
    def __init__(self, rows, day, zone, *, allow_export=True):
        self.zone = ZoneInfo(zone)
        self.day = day
        self.begin = datetime.combine(day, datetime.min.time(), self.zone).astimezone(timezone.utc)
        self.end = datetime.combine(day+timedelta(days=1), datetime.min.time(), self.zone).astimezone(timezone.utc)
        self.starts = []
        imports, sell = [], []
        ordered = sorted(rows, key=lambda r: datetime.fromisoformat(r['start']).timestamp())
        expected = self.begin
        for row in ordered:
            start, end = (datetime.fromisoformat(row[k]).astimezone(timezone.utc) for k in ('start','end'))
            if datetime.fromisoformat(row['start']).tzinfo is None or datetime.fromisoformat(row['end']).tzinfo is None:
                raise ValueError('price_timezone_required')
            if not self.begin <= start < self.end:
                continue
            if start != expected or end-start != timedelta(minutes=15) or end > self.end:
                raise ValueError('incomplete_published_price_day')
            if row.get('origin','published') != 'published':
                raise ValueError('published_prices_required_for_control')
            values = [row.get('import'), row.get('export')]
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                raise ValueError('both_tariffs_required')
            self.starts.append(start)
            imports.append(values[0]);sell.append(values[1])
            expected = end
        if expected != self.end or len(imports) not in (92,94,96,98,100):
            raise ValueError('incomplete_published_price_day')
        super().__init__(imports)
        self.exports = sell
        self.allow_export = allow_export
        self.transition = len(self) != 96
        changed = [i for i in range(1,len(self)) if self.starts[i].astimezone(self.zone).utcoffset()
                   != self.starts[i-1].astimezone(self.zone).utcoffset()]
        safe=[]
        for index in changed:
            delta=self.starts[index].astimezone(self.zone).utcoffset()-self.starts[index-1].astimezone(self.zone).utcoffset()
            # After a backward change, exclude the entire repeated wall-clock
            # range. Firmware cannot distinguish its first and second occurrence.
            safe.append(index+max(0,int(-delta.total_seconds()/900)))
        self.earliest_charge_slot = max(safe,default=0)
        # A 00:00 program boundary is not used. One minute cannot be represented
        # by this quarter-hour planner, so the first candidate is 00:15.
        self.earliest_charge_slot = max(1,self.earliest_charge_slot)

    def instant(self, slot):
        if not 0 <= slot <= len(self):raise ValueError('slot_outside_delivery_day')
        return self.end if slot==len(self) else self.starts[slot]

    def label(self, slot):
        return self.instant(slot).astimezone(self.zone).strftime('%H:%M:00')

    def slot_at(self, at):
        stamp=at.astimezone(timezone.utc)
        if not self.begin <= stamp < self.end:raise ValueError('wrong_control_day')
        return int((stamp-self.begin).total_seconds()//900)

    def wall_slot(self, hour, minute=0):
        stamp=datetime.combine(self.day,datetime.min.time(),self.zone).replace(hour=hour,minute=minute).astimezone(timezone.utc)
        return int((stamp-self.begin).total_seconds()//900)

    def contract(self, base):
        hour,minute=map(int,base.export_end_hhmm.split(':'))
        cutoff=len(self) if hour==24 else self.wall_slot(hour,minute)
        return replace(base,day_slots=len(self),deadline_slot=self.wall_slot(18),
                       export_start_slot=self.wall_slot(18),window_end_slot=self.wall_slot(16,15),
                       export_end_slot=cutoff,
                       earliest_charge_slot=self.earliest_charge_slot,allow_export=self.allow_export)
