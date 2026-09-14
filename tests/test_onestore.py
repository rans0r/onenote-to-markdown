import struct
import uuid

from onenote_to_markdown import onestore as O


def _propset(props):
    """Build an ObjectSpaceObjectPropSet body: (prid, payload bytes) pairs."""
    body = struct.pack("<H", len(props)) + b"".join(struct.pack("<I", p) for p, _ in props)
    body += b"".join(d for _, d in props)
    return body


def test_property_types_and_positional_references():
    oid_a = (uuid.uuid4(), 1)
    oid_b = (uuid.uuid4(), 2)
    text = "hello".encode("utf-16le")
    props = [
        (0x08001C04 | (1 << 31), b""),                       # Bold = True (value in prid bit)
        (0x10001C0B, struct.pack("<H", 22)),                  # FontSize (2 bytes)
        (0x1C001C22, struct.pack("<I", len(text)) + text),   # RichEditTextUnicode (len + data)
        (0x24001C20, struct.pack("<I", 2)),                   # ElementChildNodes: 2 object ids
    ]
    # OID stream header: count=2, OsidStreamNotPresent bit set; 2 dummy compact ids
    header = struct.pack("<I", 2 | (1 << 31)) + b"\x00" * 8
    data = header + _propset(props)
    ps = O.parse_object_prop_set(data, [oid_a, oid_b], [])
    assert ps.get(0x08001C04) is True
    assert ps.get(0x10001C0B) == 22
    assert ps.get(0x1C001C22) == text
    assert ps.get(0x24001C20) == [oid_a, oid_b]


def test_nested_property_set_array():
    inner = _propset([(0x14003470, struct.pack("<I", 1))])          # ActionItemStatus = 1
    outer = _propset([(0x40003489, struct.pack("<I", 1) + struct.pack("<I", 0x44003489) + inner)])
    header = struct.pack("<I", 0 | (1 << 31))
    ps = O.parse_object_prop_set(header + outer, [], [])
    states = ps.get(0x40003489)
    assert len(states) == 1 and states[0].get(0x14003470) == 1


def test_prop_and_jcid_names():
    assert O.prop_name(0x1C001C22) == "RichEditTextUnicode"
    assert O.prop_name(0x12345678).startswith("prop_")
    assert O.Node((uuid.uuid4(), 1), 0x0006000E).jcid_name == "RichTextOENode"
