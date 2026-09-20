"""Synthetic Docan protocol frames; no real serial adapters or identifiers."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from custom_components.docan_deye_ems.docan import checksum,telemetry_query,parse_frame,validate_docan,read_docan
from custom_components.docan_deye_ems.model import InputError

def frame(current=1234,address=0):
    data=bytearray(64)
    data[2]=55;data[3:5]=(5280).to_bytes(2,'big');data[5]=16
    for i in range(16):data[6+i*2:8+i*2]=(3300+i).to_bytes(2,'big')
    data[38:40]=(250).to_bytes(2,'big');data[44]=2
    data[45:49]=(250).to_bytes(2,'big')*2
    data[49:51]=current.to_bytes(2,'big',signed=True)
    data[51:53]=(999).to_bytes(2,'big')  # Resistance must not be decoded as current.
    info=data.hex().upper();n=len(info);length=((-sum((n>>s)&15 for s in (8,4,0)))&15)<<12|n
    body=f'22{address:02X}4A00{length:04X}'+info
    return ('~'+body+checksum(body)+'\r').encode()

class DocanTests(unittest.TestCase):
    def test_signed_current_separate_from_resistance_and_no_private_fields(self):
        for raw,expected in [(1234,-12.34),(-1234,12.34),(0,0)]:
            result=parse_frame(frame(raw),0)
            self.assertEqual(result['battery_current'],expected)
            self.assertAlmostEqual(result['battery_power'],expected*52.8)
            self.assertNotIn('serial',result);self.assertNotIn('raw',result)
            self.assertAlmostEqual(result['battery_cell_delta'],.015)
    def test_fixed_query_and_reject_serial_urls(self):
        self.assertIn(b'4642E00201',telemetry_query(0))
        for port in ('socket://host:23','/etc/passwd','/dev/serial/by-id/../foo','COM0'):
            with self.assertRaises(InputError):validate_docan({'source':'docan_usb','port':port,'address':0})
        for address in (-1,16,True):
            with self.assertRaises(InputError):telemetry_query(address)
    def test_corrupt_wrong_address_and_truncation(self):
        for bad in (frame()[:-3],frame().replace(b'4A00',b'4A01'),b'x'+frame()[1:],frame()+b'extra'):
            with self.assertRaises(InputError):parse_frame(bad,0)
        with self.assertRaises(InputError):parse_frame(frame(address=1),0)
    def test_transport_only_sends_one_read_query_and_closes(self):
        class Port:
            def __init__(self):self.data=bytearray(frame());self.sent=[];self.closed=False
            def __enter__(self):return self
            def __exit__(self,*args):self.closed=True
            def reset_input_buffer(self):pass
            def write(self,data):self.sent.append(data)
            def read(self,n):r=bytes(self.data[:n]);del self.data[:n];return r
        port=Port()
        with patch('serial.Serial',return_value=port):
            read_docan({'source':'docan_usb','port':'COM7','address':0})
        self.assertEqual(port.sent,[telemetry_query(0)])
        self.assertTrue(port.closed)

if __name__=='__main__':unittest.main()
