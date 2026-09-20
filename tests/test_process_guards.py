"""Complete guard subprocesses with an owned synthetic serial device and TCP.

The only /dev entry created is a uniquely named symlink to this test's own PTY.
Systems without permission skip this fixture; CI and the disposable lab provide
an empty writable /dev/serial/by-id directory for it.
"""
import asyncio
import os
from pathlib import Path
import select
import threading
import unittest
from uuid import uuid4

import test_control_host as host_fixture
from test_control_device import battery_frame
from custom_components.docan_deye_ems.docan import checksum,telemetry_query


class ProcessGuards(unittest.IsolatedAsyncioTestCase):
    async def test_guard_process_stops_without_parent_controller_tick(self):
        if os.name=='nt':self.skipTest('POSIX pseudo-terminal fixture')
        import pty
        directory=Path('/dev/serial/by-id')
        try:directory.mkdir(parents=True,exist_ok=True)
        except PermissionError:self.skipTest('Writable synthetic serial fixture directory required')
        master,slave=pty.openpty()
        link=directory/('docan-ems-fixture-'+uuid4().hex)
        try:link.symlink_to(os.ttyname(slave))
        except PermissionError:
            os.close(master);os.close(slave);self.skipTest('Writable synthetic serial fixture directory required')
        ending=threading.Event();requests=[];errors=[]
        raw=battery_frame();body=raw[1:-5].decode();payload=bytearray.fromhex(body[12:])
        payload[53:55]=b'\x00\x00'
        body=body[:12]+payload.hex().upper();response=('~'+body+checksum(body)+'\r').encode()
        def serial_peer():
            data=bytearray()
            while not ending.is_set():
                if not select.select([master],[],[],.1)[0]:continue
                try:data.extend(os.read(master,1024))
                except OSError:continue
                while b'\r' in data:
                    index=data.index(13)+1;request=bytes(data[:index]);del data[:index]
                    if request!=telemetry_query(0):errors.append('unexpected_serial_message');continue
                    requests.append(request);os.write(master,response)
        thread=threading.Thread(target=serial_peer,daemon=True);thread.start()
        fixture=host_fixture.HostTests('test_real_process_guards_start_and_are_reaped')
        initialized=False
        try:
            await fixture.asyncSetUp();initialized=True
            fixture.settings['battery']['port']=str(link)
            from custom_components.docan_deye_ems.control_profile import context_digest
            fixture.host.context=context_digest(fixture.settings,'UTC')
            await fixture.commission();await fixture.host.guards.close()
            fixture.host.guards=host_fixture.GuardProcesses(fixture.host.store,fixture.settings,fixture.host.context,'UTC')
            await fixture.host.guards.start()
            await fixture.host.session.enable(fixture.host.now())
            async with asyncio.timeout(20):
                while len(requests)<2:await asyncio.sleep(.1)
            fixture.peer.registers[130]=1
            # The parent's control tick is absent. The supervisor must stop an
            # installation whose controller heartbeat disappeared.
            fixture.host.store.save('heartbeat',0)
            async with asyncio.timeout(25):
                while not fixture.host.store.latch or fixture.peer.registers[130]!=0:await asyncio.sleep(.1)
            self.assertEqual(fixture.host.store.latch['why'],'controller_heartbeat_lost')
            self.assertTrue(requests);self.assertEqual(errors,[])
            self.assertFalse(fixture.host.store.get('active'))
        finally:
            if initialized:await fixture.asyncTearDown()
            ending.set();thread.join(timeout=2)
            link.unlink();os.close(master);os.close(slave)
