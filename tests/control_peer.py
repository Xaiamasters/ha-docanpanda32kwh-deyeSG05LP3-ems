"""Independent loopback protocol emulator with explicit control fault injection."""
import asyncio
import struct
from equipment_peer import checksum


class ControlPeer:
    def __init__(self, protocol):
        self.protocol = protocol
        self.requests = []
        self.writes = []
        self.fault = None
        self.connections = set()
        self.tasks = set()
        self.errors = []
        self.registers = {0:5,111:0,128:40,130:0,141:1,142:2,143:7900,144:0,145:0,146:255,
                          220:0,231:790,587:5280,588:55,589:0,590:65536-5280,591:65536-10000,625:5600}
        self.registers.update({636:500,653:500,672:300,673:200,520:120,521:20,526:50,529:40})
        self.registers.update({i:0 for i in range(553,559)})
        for i,hhmm in enumerate((0,1200,1830,2330,2340,2350)):
            self.registers.update({148+i:hhmm,154+i:8000,160+i:5520 if i==1 else 4900,
                                   166+i:95 if i==1 else 25,172+i:1 if i==1 else 0})

    async def start(self):
        self.server = await asyncio.start_server(self.handle,'127.0.0.1',0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    def connection(self):
        return {'transport':self.protocol,'host':'127.0.0.1','port':self.port,'unit':1,'serial':123456789}

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for w in tuple(self.connections):w.close()
        for t in tuple(self.tasks):t.cancel()
        await asyncio.gather(*tuple(self.tasks),return_exceptions=True)

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task);self.connections.add(writer)
        try:
            if self.protocol=='modbus_tcp':
                tid,proto,size=struct.unpack('>HHH',await reader.readexactly(6))
                assert proto==0 and size in (6,9)
                body=await reader.readexactly(size)
            elif self.protocol=='solarman_v5':
                header=await reader.readexactly(11)
                start,size,code,seq,serial=struct.unpack('<BHHHI',header)
                tail=await reader.readexactly(size+2)
                assert start==165 and code==17680 and tail[-1]==21
                assert (sum(header[1:])+sum(tail[:-2]))%256==tail[-2]
                rtu=tail[15:-2];assert rtu[-2:]==checksum(rtu[:-2]);body=rtu[:-2]
            else:
                head=await reader.readexactly(2)
                rtu=head+await reader.readexactly(6 if head[1]==3 else 9)
                assert rtu[-2:]==checksum(rtu[:-2]);body=rtu[:-2]
            unit,fn,address,count=struct.unpack('>BBHH',body[:6])
            self.requests.append((fn,address,count))
            assert unit==1 and fn in (3,16)
            if self.fault=='disconnect':return
            if self.fault=='timeout':await asyncio.sleep(2);return
            if fn==3:
                assert all(i in self.registers for i in range(address,address+count))
                reply=bytes((unit,3,count*2))+b''.join(self.registers[i].to_bytes(2,'big') for i in range(address,address+count))
            else:
                assert count==1 and len(body)==9 and body[6]==2
                assert address in (128,130,141,142,143,145,146,231) or 148<=address<=177
                value=int.from_bytes(body[7:9],'big')
                self.writes.append((address,value))
                if self.fault!='ignored_write':self.registers[address]=value
                if self.fault=='lost_ack':return
                reply=struct.pack('>BBHH',unit,16,address+(1 if self.fault=='wrong_echo' else 0),count)
            if self.fault=='exception':reply=bytes((unit,fn|128,2))
            if self.fault=='wrong_unit':reply=bytes((unit+1,))+reply[1:]
            if self.protocol=='modbus_tcp':
                response=struct.pack('>HHH',tid+(1 if self.fault=='wrong_sequence' else 0),0,len(reply))+reply
            else:
                rtu=reply+checksum(reply)
                if self.fault=='bad_crc':rtu=rtu[:-1]+bytes((rtu[-1]^255,))
                if self.protocol=='solarman_v5':
                    if self.fault=='double_crc':rtu+=bytes(2)
                    payload=b'\x02\x01'+bytes(12)+rtu
                    response=struct.pack('<BHHHI',165,len(payload),5392,seq+(1 if self.fault=='wrong_sequence' else 0),serial+(1 if self.fault=='wrong_serial' else 0))+payload
                    response+=bytes((sum(response[1:])%256,21))
                else:response=rtu
            if self.fault=='truncate':response=response[:4]
            writer.write(response[:3]);await writer.drain();await asyncio.sleep(0)
            writer.write(response[3:]);await writer.drain()
        except (asyncio.IncompleteReadError,ConnectionError):pass
        except Exception as exc:
            self.errors.append(type(exc).__name__+':'+str(exc))
        finally:
            writer.close()
            try:await writer.wait_closed()
            except OSError:pass
            self.connections.discard(writer);self.tasks.discard(task)
