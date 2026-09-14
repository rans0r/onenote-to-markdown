import struct

import pytest

from onenote_to_markdown import fsshttpb as F


def test_compact_u64_widths():
    # MS-FSSHTTPB 2.2.1.1 encodings: value << width | marker bit
    assert F.Reader(b"\x00").compact_u64() == 0
    assert F.Reader(bytes([(5 << 1) | 1])).compact_u64() == 5           # 7-bit
    assert F.Reader(struct.pack("<H", (300 << 2) | 2)).compact_u64() == 300   # 14-bit
    assert F.Reader(((70000 << 3) | 4).to_bytes(3, "little")).compact_u64() == 70000  # 21-bit
    assert F.Reader(b"\x80" + struct.pack("<Q", 1 << 40)).compact_u64() == 1 << 40   # 64-bit


def test_extended_guid_5bit_and_null():
    guid = bytes(range(16))
    r = F.Reader(bytes([(7 << 3) | 4]) + guid)
    g, n = r.extended_guid()
    assert n == 7 and g.bytes_le == guid
    assert F.Reader(b"\x00").extended_guid() is None


def test_headers_16_and_32_bit():
    v16 = (0x15 << 3) | (1 << 2) | 0b00 | (1 << 9)       # type 0x15, compound, length 1
    h = F.read_header(F.Reader(struct.pack("<H", v16)))
    assert (h.kind, h.type, h.length, h.compound) == ("start", 0x15, 1, True)
    v32 = 0b10 | (1 << 2) | (0x7A << 3) | (33 << 17)      # type 0x7A, compound, length 33
    h = F.read_header(F.Reader(struct.pack("<I", v32)))
    assert (h.type, h.length, h.compound, h.size) == (0x7A, 33, True, 4)
    assert F.read_header(F.Reader(bytes([(0x1D << 2) | 1]))).kind == "end"


def test_32bit_header_large_length():
    # length field 0x7FFF means a compact uint64 "large length" follows
    v32 = 0b10 | (0x02 << 3) | (0x7FFF << 17)
    data = struct.pack("<I", v32) + ((50000 << 3) | 4).to_bytes(3, "little")
    h = F.read_header(F.Reader(data))
    assert h.type == 0x02 and h.length == 50000


def test_parse_stream_object_tree():
    child = struct.pack("<H", (0x0B << 3) | 0b00 | (3 << 9)) + b"abc"
    parent = struct.pack("<H", (0x1D << 3) | (1 << 2) | (1 << 9)) + b"\x00" + child + bytes([(0x1D << 2) | 1])
    obj = F.parse_stream_object(F.Reader(parent))
    assert obj.type == 0x1D and obj.payload == b"\x00"
    assert [c.type for c in obj.children] == [0x0B]
    assert obj.children[0].payload == b"abc"


def test_mismatched_end_header_is_an_error():
    bad = struct.pack("<H", (0x1D << 3) | (1 << 2)) + bytes([(0x1E << 2) | 1])
    with pytest.raises(ValueError):
        F.parse_stream_object(F.Reader(bad))


def test_rejects_classic_format():
    classic = bytes(48) + bytes.fromhex("3fdd9a101b91f549a5d01791edc8aed8") + bytes(100)
    with pytest.raises(ValueError, match="alternative-packaging"):
        F.parse_package(classic)
