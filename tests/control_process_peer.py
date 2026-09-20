"""Spawnable lab workers. No physical connections or serial ports are used."""
import asyncio
from datetime import datetime, timezone
import time

from custom_components.docan_deye_ems.control_device import DeyeDevice, LabAuthority
from custom_components.docan_deye_ems.control_runtime import Watchdog
from custom_components.docan_deye_ems.control_store import ControlStore, DeviceLease


def supervisor_worker(connection, path, ready, finish, result):
    LabAuthority(connection)  # Reject physical endpoints before any connection.
    async def run():
        store=ControlStore(path)
        device=DeyeDevice(connection,timeout=.5)
        guard=Watchdog('supervisor',device,None,store,max_heartbeat_age=.4,require_watchdogs=False)
        ready.set()
        try:
            while not finish.is_set():
                outcome=await guard.once(datetime.now(timezone.utc))
                if outcome:
                    result.put(outcome)
                    return
                await asyncio.sleep(.03)
        finally:store.close()
    asyncio.run(run())


def stalled_controller(path, lease_path, ready):
    store=ControlStore(path)
    lease=DeviceLease(lease_path)
    store.save('active',True)
    store.save('heartbeat',time.time())
    ready.set()
    try:
        while True:time.sleep(.1)
    finally:lease.close();store.close()
