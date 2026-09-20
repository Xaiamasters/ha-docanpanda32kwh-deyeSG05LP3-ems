"""Independent synthetic wire peer for tests only; never ship as an integration."""
import asyncio
import struct


def checksum(data):
    value = 65535
    for item in data:
        value ^= item
        for _ in range(8):
            if value % 2:
                value = (value // 2) ^ 40961
            else:
                value //= 2
    return value.to_bytes(2, 'little')


class EquipmentPeer:
    def __init__(self, protocol):
        self.protocol = protocol
        self.fault = None
        self.requests = []
        self.connections = set()
        self.tasks = set()
        self.registers = {0: 5, 587: 5210, 588: 57, 589: 0, 590: 400, 591: 770,
                          625: 100, 636: 1200, 653: 1300, 672: 500, 673: 300,
                          520: 27, 521: 13, 526: 84, 529: 102}

    async def start(self, port=0):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in tuple(self.connections):
            writer.close()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.add(writer)
        try:
            while True:
                if self.protocol == 'modbus_tcp':
                    head = await reader.readexactly(6)
                    transaction, protocol, size = struct.unpack('>HHH', head)
                    assert protocol == 0 and size == 6
                    request = await reader.readexactly(size)
                elif self.protocol == 'solarman_v5':
                    head = await reader.readexactly(11)
                    start, size, control, sequence, serial = struct.unpack('<BHHHI', head)
                    payload = await reader.readexactly(size + 2)
                    assert start == 165 and control == 17680 and payload[-1] == 21
                    assert (sum(head[1:]) + sum(payload[:-2])) % 256 == payload[-2]
                    request = payload[15:-4]
                    assert payload[15:-2][-2:] == checksum(request)
                else:
                    frame = await reader.readexactly(8)
                    request = frame[:-2]
                    assert frame[-2:] == checksum(request)
                unit, function, address, count = struct.unpack('>BBHH', request)
                self.requests.append((function, address, count))
                # Independent allowlist; any write/unexpected read fails the test.
                assert function == 3
                assert (address, count) in ((0, 1), (587, 5), (625, 1), (636, 1), (653, 1), (672, 2), (520, 2), (526, 1), (529, 1))
                if self.fault == 'disconnect':
                    return
                if self.fault == 'timeout':
                    await asyncio.sleep(10)
                body = bytes((unit, 3, count * 2)) + b''.join(self.registers[a].to_bytes(2, 'big') for a in range(address, address + count))
                if self.fault == 'exception':
                    body = bytes((unit, 131, 2))
                if self.protocol == 'modbus_tcp':
                    if self.fault == 'transaction':
                        transaction += 1
                    result = struct.pack('>HHH', transaction, 0, len(body)) + body
                else:
                    rtu = body + checksum(body)
                    if self.fault == 'crc':
                        rtu = rtu[:-1] + bytes((rtu[-1] ^ 255,))
                    if self.protocol == 'solarman_v5':
                        if self.fault == 'double_crc':
                            rtu += bytes(2)
                        payload = b'\x02\x01' + bytes(12) + rtu
                        result = struct.pack('<BHHHI', 165, len(payload), 5392, sequence | 256, serial + (1 if self.fault == 'serial' else 0)) + payload
                        result += bytes((sum(result[1:]) % 256, 21))
                    else:
                        result = rtu
                if self.fault == 'truncate':
                    writer.write(result[:4])
                    await writer.drain()
                    return
                # Deliberate fragmentation tests stream framing.
                writer.write(result[:3])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(result[3:])
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.connections.discard(writer)
            self.tasks.discard(task)
