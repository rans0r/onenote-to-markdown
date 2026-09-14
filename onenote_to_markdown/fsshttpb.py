"""
Minimal MS-FSSHTTPB (File Synchronization via SOAP over HTTP Protocol, Binary)
reader – just enough to unpack the "alternative packaging" that OneNote uses
for .one section files stored on OneDrive / SharePoint (see MS-ONESTORE §2.8).

The packaging is a tree of *stream objects*.  Each stream object has a
16- or 32-bit header carrying (type, length, compound-flag); compound objects
contain child stream objects and are terminated by an 8- or 16-bit end header.
"""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# --------------------------------------------------------------------------- #
# Primitive readers
# --------------------------------------------------------------------------- #
class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def peek(self, n: int) -> bytes:
        return self.data[self.pos:self.pos + n]

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EOFError(f"read past end: pos={self.pos} n={n} len={len(self.data)}")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack_from("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self.read(4))[0]

    def guid(self) -> uuid.UUID:
        return uuid.UUID(bytes_le=self.read(16))

    # MS-FSSHTTPB 2.2.1.1 Compact Unsigned 64-bit Integer
    def compact_u64(self) -> int:
        b0 = self.data[self.pos]
        if b0 == 0:
            self.pos += 1
            return 0
        # number of trailing zero bits in b0 tells the width
        width = 1
        while width <= 8 and not (b0 >> (width - 1)) & 1:
            width += 1
        if width == 9:  # b0 == 0x80 -> 64-bit value follows
            self.pos += 1
            return struct.unpack_from("<Q", self.read(8))[0]
        raw = int.from_bytes(self.read(width), "little")
        return raw >> width

    # MS-FSSHTTPB 2.2.1.7 Extended GUID
    def extended_guid(self) -> Optional[Tuple[uuid.UUID, int]]:
        b0 = self.data[self.pos]
        if b0 == 0:
            self.pos += 1
            return None
        if b0 & 0x07 == 0x04:            # 5-bit uint
            self.pos += 1
            value = b0 >> 3
        elif b0 & 0x3F == 0x20:          # 10-bit uint
            value = self.u16() >> 6
        elif b0 & 0x7F == 0x40:          # 17-bit uint
            value = int.from_bytes(self.read(3), "little") >> 7
        elif b0 == 0x80:                 # 32-bit uint
            self.pos += 1
            value = self.u32()
        else:
            raise ValueError(f"bad extended guid lead byte {b0:#x} at {self.pos}")
        return (self.guid(), value)

    # MS-FSSHTTPB 2.2.1.9 Serial Number
    def serial_number(self) -> Optional[Tuple[uuid.UUID, int]]:
        t = self.u8()
        if t == 0:
            return None
        if t == 128:
            g = self.guid()
            v = struct.unpack_from("<Q", self.read(8))[0]
            return (g, v)
        raise ValueError(f"bad serial number type {t} at {self.pos}")

    # MS-FSSHTTPB 2.2.1.10 Cell ID
    def cell_id(self):
        return (self.extended_guid(), self.extended_guid())

    # MS-FSSHTTPB 2.2.1.8 Extended GUID Array
    def extended_guid_array(self):
        n = self.compact_u64()
        return [self.extended_guid() for _ in range(n)]

    # MS-FSSHTTPB 2.2.1.11 Cell ID Array
    def cell_id_array(self):
        n = self.compact_u64()
        return [self.cell_id() for _ in range(n)]

    # MS-FSSHTTPB 2.2.1.3 Binary Item
    def binary_item(self) -> bytes:
        n = self.compact_u64()
        return self.read(n)


# --------------------------------------------------------------------------- #
# Stream object headers (MS-FSSHTTPB 2.2.1.5)
# --------------------------------------------------------------------------- #
@dataclass
class Header:
    kind: str          # 'start' or 'end'
    type: int
    length: int = 0
    compound: bool = False
    size: int = 0      # header size in bytes


def read_header(r: Reader) -> Header:
    b0 = r.data[r.pos]
    lo = b0 & 0x3
    if lo == 0b00:      # 16-bit start
        v = r.u16()
        return Header("start", (v >> 3) & 0x3F, v >> 9, bool((v >> 2) & 1), 2)
    if lo == 0b10:      # 32-bit start
        v = r.u32()
        length = v >> 17
        if length == 0x7FFF:            # "large length" follows as a compact uint64
            length = r.compact_u64()
        return Header("start", (v >> 3) & 0x3FFF, length, bool((v >> 2) & 1), 4)
    if lo == 0b01:      # 8-bit end
        v = r.u8()
        return Header("end", v >> 2, 0, False, 1)
    # 0b11 -> 16-bit end
    v = r.u16()
    return Header("end", v >> 2, 0, False, 2)


@dataclass
class StreamObject:
    type: int
    payload: bytes
    children: List["StreamObject"] = field(default_factory=list)
    offset: int = 0

    def find(self, t: int) -> List["StreamObject"]:
        return [c for c in self.children if c.type == t]

    def first(self, t: int) -> Optional["StreamObject"]:
        for c in self.children:
            if c.type == t:
                return c
        return None


def parse_stream_object(r: Reader) -> StreamObject:
    """Parse one stream object (and, if compound, its children)."""
    start = r.pos
    h = read_header(r)
    if h.kind != "start":
        raise ValueError(f"expected start header at {start}, got end of type {h.type:#x}")
    payload = r.read(h.length)
    obj = StreamObject(h.type, payload, offset=start)
    if h.compound:
        while True:
            if r.eof():
                raise EOFError(f"unterminated compound object type {h.type:#x} from {start}")
            nxt = r.peek(1)[0] & 0x3
            if nxt in (0b01, 0b11):
                e = read_header(r)
                if e.type != h.type:
                    raise ValueError(
                        f"end header type {e.type:#x} does not match start {h.type:#x} at {r.pos}")
                break
            obj.children.append(parse_stream_object(r))
    return obj


# Stream object type ids we care about (MS-FSSHTTPB 2.2.1.5)
T_DATA_ELEMENT = 0x01
T_OBJECT_DATA_BLOB = 0x02
T_OBJECT_GROUP_BLOB_DECLARE = 0x05
T_STORAGE_MANIFEST_ROOT_DECLARE = 0x07
T_REVISION_MANIFEST_ROOT_DECLARE = 0x0A
T_CELL_MANIFEST_CURRENT_REVISION = 0x0B
T_STORAGE_MANIFEST_SCHEMA_GUID = 0x0C
T_STORAGE_INDEX_REVISION_MAPPING = 0x0D
T_STORAGE_INDEX_CELL_MAPPING = 0x0E
T_STORAGE_INDEX_MANIFEST_MAPPING = 0x11
T_DATA_ELEMENT_PACKAGE = 0x15
T_OBJECT_GROUP_OBJECT_DATA = 0x16
T_OBJECT_GROUP_OBJECT_DECLARE = 0x18
T_REVISION_MANIFEST_OBJECT_GROUP_REFS = 0x19
T_REVISION_MANIFEST = 0x1A
T_OBJECT_GROUP_OBJECT_DATA_BLOB_REF = 0x1C
T_OBJECT_GROUP_DECLARATIONS = 0x1D
T_OBJECT_GROUP_DATA = 0x1E
T_OBJECT_GROUP_METADATA = 0x78
T_OBJECT_GROUP_METADATA_DECLARATIONS = 0x79
T_ONE_PACKAGING_START = 0x7A

DE_STORAGE_INDEX = 1
DE_STORAGE_MANIFEST = 2
DE_CELL_MANIFEST = 3
DE_REVISION_MANIFEST = 4
DE_OBJECT_GROUP = 5
DE_DATA_ELEMENT_FRAGMENT = 6
DE_OBJECT_DATA_BLOB = 10


# --------------------------------------------------------------------------- #
# Parsed data elements
# --------------------------------------------------------------------------- #
@dataclass
class DataElement:
    guid: Tuple[uuid.UUID, int]
    serial: object
    de_type: int
    obj: StreamObject
    body: Reader


@dataclass
class ObjectDecl:
    oid: Tuple[uuid.UUID, int]
    partition: int
    data_size: int = 0
    ref_count: int = 0
    cell_ref_count: int = 0
    blob_ref: Optional[Tuple[uuid.UUID, int]] = None   # for BLOB declarations


@dataclass
class GroupObject:
    decl: ObjectDecl
    refs: list                       # extended guids of referenced objects
    cell_refs: list                  # cell ids referenced
    data: bytes


@dataclass
class ObjectGroup:
    guid: Tuple[uuid.UUID, int]
    objects: List[GroupObject]


@dataclass
class Package:
    storage_index: List[DataElement]
    storage_manifests: List[DataElement]
    cell_manifests: dict            # de guid -> current revision extguid
    revision_manifests: dict        # de guid -> (revision id, base id, roots[(root, obj)], group refs[])
    object_groups: dict             # de guid -> ObjectGroup
    blobs: dict                     # de guid -> bytes
    elements: List[DataElement]


def parse_data_element(obj: StreamObject) -> DataElement:
    r = Reader(obj.payload)
    guid = r.extended_guid()
    serial = r.serial_number()
    de_type = r.compact_u64()
    return DataElement(guid, serial, de_type, obj, r)


def _parse_object_group(de: DataElement) -> ObjectGroup:
    decls: List[ObjectDecl] = []
    decl_root = de.obj.first(T_OBJECT_GROUP_DECLARATIONS)
    if decl_root:
        for c in decl_root.children:
            r = Reader(c.payload)
            if c.type == T_OBJECT_GROUP_OBJECT_DECLARE:
                oid = r.extended_guid()
                decls.append(ObjectDecl(oid, r.compact_u64(), r.compact_u64(),
                                        r.compact_u64(), r.compact_u64()))
            elif c.type == T_OBJECT_GROUP_BLOB_DECLARE:
                oid = r.extended_guid()
                blob = r.extended_guid()
                decls.append(ObjectDecl(oid, r.compact_u64(), 0, r.compact_u64(),
                                        r.compact_u64(), blob_ref=blob))
    objects: List[GroupObject] = []
    data_root = de.obj.first(T_OBJECT_GROUP_DATA)
    if data_root:
        i = 0
        for c in data_root.children:
            r = Reader(c.payload)
            if c.type == T_OBJECT_GROUP_OBJECT_DATA:
                refs = r.extended_guid_array()
                cells = r.cell_id_array()
                data = r.binary_item()
                objects.append(GroupObject(decls[i], refs, cells, data))
                i += 1
            elif c.type == T_OBJECT_GROUP_OBJECT_DATA_BLOB_REF:
                refs = r.extended_guid_array()
                cells = r.cell_id_array()
                blob = r.extended_guid()
                d = decls[i]
                d.blob_ref = d.blob_ref or blob
                objects.append(GroupObject(d, refs, cells, b""))
                i += 1
    return ObjectGroup(de.guid, objects)


def parse_package(data: bytes) -> Package:
    r = Reader(data)
    # MS-ONESTORE 2.8.1 header
    guid_file_type = r.guid()
    r.guid()            # guidFile
    r.guid()            # guidLegacyFileVersion
    guid_file_format = r.guid()
    if str(guid_file_format).lower() != "638de92f-a6d4-4bc1-9a36-b3fc2511a5b7":
        raise ValueError("not a OneNote alternative-packaging file "
                         f"(guidFileFormat={guid_file_format})")
    r.u32()             # reserved
    root = parse_stream_object(r)          # type 0x7A packaging start
    if root.type != T_ONE_PACKAGING_START:
        raise ValueError(f"unexpected packaging root type {root.type:#x}")
    pkg_obj = root.first(T_DATA_ELEMENT_PACKAGE)
    if pkg_obj is None:
        raise ValueError("no data element package")

    pkg = Package([], [], {}, {}, {}, {}, [])
    for child in pkg_obj.children:
        if child.type != T_DATA_ELEMENT:
            continue
        de = parse_data_element(child)
        pkg.elements.append(de)
        if de.de_type == DE_STORAGE_INDEX:
            pkg.storage_index.append(de)
        elif de.de_type == DE_STORAGE_MANIFEST:
            pkg.storage_manifests.append(de)
        elif de.de_type == DE_CELL_MANIFEST:
            cur = de.obj.first(T_CELL_MANIFEST_CURRENT_REVISION)
            pkg.cell_manifests[de.guid] = Reader(cur.payload).extended_guid() if cur else None
        elif de.de_type == DE_REVISION_MANIFEST:
            rm = de.obj.first(T_REVISION_MANIFEST)
            rr = Reader(rm.payload)
            rev_id, base_id = rr.extended_guid(), rr.extended_guid()
            roots, groups = [], []
            for c in de.obj.children:
                cr = Reader(c.payload)
                if c.type == T_REVISION_MANIFEST_ROOT_DECLARE:
                    roots.append((cr.extended_guid(), cr.extended_guid()))
                elif c.type == T_REVISION_MANIFEST_OBJECT_GROUP_REFS:
                    groups.append(cr.extended_guid())
            pkg.revision_manifests[de.guid] = (rev_id, base_id, roots, groups)
        elif de.de_type == DE_OBJECT_GROUP:
            pkg.object_groups[de.guid] = _parse_object_group(de)
        elif de.de_type == DE_OBJECT_DATA_BLOB:
            b = de.obj.first(T_OBJECT_DATA_BLOB)
            pkg.blobs[de.guid] = Reader(b.payload).binary_item() if b else b""
    return pkg


def storage_index_mappings(pkg: Package):
    """Return (manifest_mapping, cell_mappings{cellid->de guid}, revision_mappings{rev->de guid})."""
    manifest = None
    cells, revisions = {}, {}
    for de in pkg.storage_index:
        for c in de.obj.children:
            r = Reader(c.payload)
            if c.type == T_STORAGE_INDEX_MANIFEST_MAPPING:
                manifest = (r.extended_guid(), r.serial_number())
            elif c.type == T_STORAGE_INDEX_CELL_MAPPING:
                cid = r.cell_id()
                cells[cid] = (r.extended_guid(), r.serial_number())
            elif c.type == T_STORAGE_INDEX_REVISION_MAPPING:
                rev = r.extended_guid()
                revisions[rev] = (r.extended_guid(), r.serial_number())
    return manifest, cells, revisions


def dump_tree(obj: StreamObject, depth: int = 0, out=print):
    out(f"{'  ' * depth}type={obj.type:#04x} len={len(obj.payload)} children={len(obj.children)} @{obj.offset}")
    for c in obj.children:
        dump_tree(c, depth + 1, out)
