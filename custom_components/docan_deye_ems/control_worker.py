"""Fixed-role guard process, launched with private configuration on standard input.

This file is executable without importing the Home Assistant integration. It
accepts no shell commands and exposes no network listener or general write API.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo


def load_modules():
    root=Path(__file__).resolve().parent
    # The worker needs only the transport, policy and storage modules. Importing
    # __init__.py would start HA-dependent imports in this standalone process.
    package=types.ModuleType('_docan_guard')
    package.__path__=[str(root)]
    sys.modules['_docan_guard']=package
    from _docan_guard.control_device import DeyeDevice,CommissionedAuthority
    from _docan_guard.control_observation import EquipmentObservation
    from _docan_guard.control_runtime import Watchdog,identity
    from _docan_guard.control_store import ControlStore,DeviceLease
    from _docan_guard.control_profile import deadman_limits
    return DeyeDevice,CommissionedAuthority,EquipmentObservation,Watchdog,identity,ControlStore,DeviceLease,deadman_limits


async def run(cfg):
    DeyeDevice,Authority,Observation,Watchdog,identity,Store,Lease,limits=load_modules()
    if set(cfg)!={'role','store','lock_dir','connection','battery','context','run_id','zone','limits'}:
        raise ValueError('invalid_worker_configuration')
    store=Store(cfg['store'],cfg['lock_dir'])
    device=DeyeDevice(cfg['connection'])
    observation=Observation(device,cfg['battery']) if cfg['role'] in ('charge','export') else None
    guard=Watchdog(cfg['role'],device,observation,store,limits=limits(cfg['limits']),
                   authority=Authority(cfg['connection'],store,cfg['context']))
    lease=Lease(store.lock_dir/(identity(device.connection)+'.'+cfg['role']+'.lock'))
    zone=ZoneInfo(cfg['zone'])
    interval={'charge':10,'export':10,'auditor':15,'supervisor':5}[cfg['role']]
    last_stop=None
    try:
        while store.get('watchdog_run_id')==cfg['run_id']:
            latch=store.latch
            if latch and latch['id']==last_stop:
                from _docan_guard.control_device import STOP_VALUES
                fields=(await device.observe())['fields']
                if all(fields[k]==v for k,v in STOP_VALUES.items()):
                    import time
                    store.save('watchdog_'+cfg['role'],time.time())
                    await asyncio.sleep(interval)
                    continue
            result=await guard.once(datetime.now(zone))
            if result and result['minimal_readback_verified'] and store.latch:last_stop=store.latch['id']
            await asyncio.sleep(interval)
    finally:
        lease.close();store.close()


def main():
    raw=sys.stdin.buffer.readline(16385)
    if len(raw)>16384:raise ValueError('worker_configuration_too_large')
    asyncio.run(run(json.loads(raw)))


if __name__=='__main__':
    try:main()
    except BaseException:
        # State and heartbeat are the evidence channel. Do not leak endpoint or
        # interpreter exception context into HA logs through child stderr.
        sys.exit(1)
