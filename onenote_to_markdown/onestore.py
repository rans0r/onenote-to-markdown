"""
MS-ONESTORE / MS-ONE object layer on top of the FSSHTTPB packaging.

Turns the raw object groups into a graph of `Node`s (JCID + properties +
resolved references) and exposes the section's object spaces (the section
itself and each page).
"""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import fsshttpb as F

# --------------------------------------------------------------------------- #
# Property / JCID names (MS-ONE §2.1.12 and §2.1.13)
# --------------------------------------------------------------------------- #
PROP_NAMES = {
    0x08001C00: "LayoutTightLayout", 0x14001C01: "PageWidth", 0x14001C02: "PageHeight",
    0x0C001C03: "OutlineElementChildLevel", 0x08001C04: "Bold", 0x08001C05: "Italic",
    0x08001C06: "Underline", 0x08001C07: "Strikethrough", 0x08001C08: "Superscript",
    0x08001C09: "Subscript", 0x1C001C0A: "Font", 0x10001C0B: "FontSize",
    0x14001C0C: "FontColor", 0x14001C0D: "Highlight", 0x1C001C12: "RgOutlineIndentDistance",
    0x0C001C13: "BodyTextAlignment", 0x14001C14: "OffsetFromParentHoriz",
    0x14001C15: "OffsetFromParentVert", 0x1C001C1A: "NumberListFormat",
    0x14001C1B: "LayoutMaxWidth", 0x14001C1C: "LayoutMaxHeight",
    0x24001C1F: "ContentChildNodes", 0x24001C20: "ElementChildNodes",
    0x08001E1E: "EnableHistory", 0x1C001C22: "RichEditTextUnicode", 0x24001C26: "ListNodes",
    0x1C001C30: "NotebookManagementEntityGuid", 0x08001C34: "OutlineElementRTL",
    0x14001C3B: "LanguageID", 0x14001C3E: "LayoutAlignmentInParent", 0x20001C3F: "PictureContainer",
    0x14001C4C: "PageMarginTop", 0x14001C4D: "PageMarginBottom", 0x14001C4E: "PageMarginLeft",
    0x14001C4F: "PageMarginRight", 0x1C001C52: "ListFont", 0x18001C65: "TopologyCreationTimeStamp",
    0x14001C84: "LayoutAlignmentSelf", 0x08001C87: "IsTitleTime", 0x08001C88: "IsBoilerText",
    0x14001C8B: "PageSize", 0x08001C8E: "PortraitPage", 0x08001C91: "EnforceOutlineStructure",
    0x08001C92: "EditRootRTL", 0x08001CB2: "CannotBeSelected", 0x08001CB4: "IsTitleText",
    0x08001CB5: "IsTitleDate", 0x14001CB7: "ListRestart", 0x08001CBD: "IsLayoutSizeSetByUser",
    0x14001CCB: "ListSpacingMu", 0x14001CDB: "LayoutOutlineReservedWidth",
    0x08001CDC: "LayoutResolveChildCollisions", 0x08001CDE: "IsReadOnly",
    0x14001CEC: "LayoutMinimumOutlineWidth", 0x14001CF1: "LayoutCollisionPriority",
    0x1C001CF3: "CachedTitleString", 0x08001CF9: "DescendantsCannotBeMoved",
    0x10001CFE: "RichEditTextLangID", 0x08001CFD: "LayoutTightAlignment",
    0x1C001D01: "Charset", 0x14001D09: "CreationTimeStamp", 0x08001D0C: "Deletable",
    0x14001D0E: "PageMarginOriginX", 0x14001D0F: "PageMarginOriginY", 0x08001D13: "IsBackground",
    0x14001D24: "IRecordMedia", 0x1C001D3C: "CachedTitleStringFromPage",
    0x14001D57: "RowCount", 0x14001D58: "ColumnCount", 0x08001D5E: "TableBordersVisible",
    0x24001D5F: "StructureElementChildNodes", 0x2C001D63: "ChildGraphSpaceElementNodes",
    0x1C001D66: "TableColumnWidths", 0x1C001D75: "Author", 0x20001D77: "AuthorOriginal",
    0x20001D78: "AuthorMostRecent", 0x18001D7A: "LastModifiedTimeStamp", 0x14001D7B: "LastModifiedTime",
    0x1C001D7D: "TableColumnsLocked", 0x1C001D9B: "EmbeddedFileContainer",
    0x20001D9B: "EmbeddedFileContainer", 0x1C001D9C: "EmbeddedFileName",
    0x1C001D9D: "SourceFilepath", 0x1C001D9E: "ConflictingUserName", 0x1C001DD7: "ImageFilename",
    0x00001DE9: "IsDeletedGraphSpaceContent", 0x1C001DF8: "ImageAltText", 0x14001DFF: "PageLevel",
    0x1C001E12: "TextRunIndex", 0x24001E13: "TextRunFormatting", 0x1C001E20: "WzHyperlinkUrl",
    0x08001E22: "TextRunIsEmbeddedObject", 0x08001E3E: "TableBordersVisible?",
    0x1C001E58: "ImageAltText", 0x08001E5E: "IsConflictObjectForRender",
    0x1400342C: "ParagraphStyle?", 0x2000342C: "ParagraphStyle", 0x1400342E: "ParagraphSpaceBefore",
    0x1400342F: "ParagraphSpaceAfter", 0x14003430: "ParagraphLineSpacingExact",
    0x24003442: "MetaDataObjectsAboveGraphSpace", 0x24003458: "TextRunDataObject",
    0x1C00345A: "ParagraphStyleId", 0x08003462: "HasVersionPages", 0x10003463: "ActionItemType",
    0x10003464: "NoteTagShape", 0x14003465: "NoteTagHighlightColor", 0x14003466: "NoteTagTextColor",
    0x14003467: "NoteTagPropertyStatus", 0x1C003468: "NoteTagLabel", 0x1400346E: "NoteTagCreated",
    0x1400346F: "NoteTagCompleted", 0x10003470: "ActionItemStatus", 0x0C003473: "ActionItemSchemaVersion",
    0x1C003478: "ReadingOrderRTL", 0x1400347F: "ParagraphAlignment", 0x20003488: "NoteTagDefinitionOid",
    0x04003489: "NoteTagStates", 0x1C003498: "TextExtendedAscii", 0x40003499: "TextRunData",
    0x1C0034CB: "ImageUploadState", 0x140034CD: "PictureWidth", 0x140034CE: "PictureHeight",
    0x1C001C3D: "PageTitleString?", 0x08001CB3: "ConflictPage?", 0x1C00349B: "SectionDisplayName?",
    0x08001E6B: "HasHyperlink?", 0x1C0034B4: "WzHyperlinkUrl?", 0x14001CD3: "TableCellBackgroundColor?",
    0x0800348A: "IsCollapsed?", 0x1C003457: "OutlineElementRTL?", 0x1C0034E0: "NoteTagStatesEx?",
}

JCID_NAMES = {
    0x00120001: "ReadOnlyPersistablePropertyContainerForAuthor",
    0x00020001: "PersistablePropertyContainerForTOC",
    0x00060007: "SectionNode", 0x00060008: "PageSeriesNode", 0x0006000B: "PageNode",
    0x0006000C: "OutlineNode", 0x0006000D: "OutlineElementNode", 0x0006000E: "RichTextOENode",
    0x00060011: "ImageNode", 0x00060012: "NumberListNode", 0x00060019: "OutlineGroup",
    0x00060022: "TableNode", 0x00060023: "TableRowNode", 0x00060024: "TableCellNode",
    0x0006002C: "TitleNode", 0x00020030: "PageMetaData", 0x00020031: "SectionMetaData",
    0x00060035: "EmbeddedFileNode", 0x00060037: "PageManifestNode", 0x00020038: "ConflictPageMetaData",
    0x0006003C: "VersionHistoryContent", 0x0006003D: "VersionProxy",
    0x00120043: "NoteTagSharedDefinitionContainer", 0x00020044: "RevisionMetaData",
    0x00020046: "VersionHistoryMetaData", 0x0012004D: "ParagraphStyleObject",
    0x00120051: "ParagraphStyleObjectForText", 0x00060014: "InkNode",
    0x00060058: "EmbeddedMediaNode", 0x0002003B: "InkContainer", 0x00020047: "InkStroke", 0x00120048: "InkStrokeProps", 0x00020054: "PageAnnotations",
}

# property value types
PT_NODATA, PT_BOOL, PT_1, PT_2, PT_4, PT_8, PT_LDATA = 1, 2, 3, 4, 5, 6, 7
PT_OID, PT_OIDS, PT_OSID, PT_OSIDS, PT_CTX, PT_CTXS = 8, 9, 10, 11, 12, 13
PT_ARRAY_OF_PROPSETS, PT_PROPSET = 0x10, 0x11

ExGuid = Tuple[uuid.UUID, int]


def prop_name(pid: int) -> str:
    return PROP_NAMES.get(pid, f"prop_{pid:08X}")


# --------------------------------------------------------------------------- #
# Property sets (MS-ONESTORE §2.6)
# --------------------------------------------------------------------------- #
@dataclass
class PropValue:
    pid: int
    ptype: int
    value: object      # bytes / bool / int / list / PropertySet / ExGuid ...

    @property
    def name(self):
        return prop_name(self.pid)


class PropertySet:
    def __init__(self):
        self.props: List[PropValue] = []

    def get(self, pid: int, default=None):
        for p in self.props:
            if p.pid == pid:
                return p.value
        return default

    def has(self, pid: int) -> bool:
        return any(p.pid == pid for p in self.props)

    def __iter__(self):
        return iter(self.props)

    def __repr__(self):
        return "{" + ", ".join(f"{p.name}={_short(p.value)}" for p in self.props) + "}"


def _short(v):
    if isinstance(v, bytes):
        return f"bytes[{len(v)}]"
    if isinstance(v, list):
        return f"[{', '.join(_short(x) for x in v[:4])}{'…' if len(v) > 4 else ''}]"
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], uuid.UUID):
        return f"oid({str(v[0])[:8]},{v[1]})"
    return repr(v)


class _RefCursor:
    """Sequential consumer of object / object-space / context references."""

    def __init__(self, oids, osids, ctxids):
        self.oids, self.osids, self.ctxids = list(oids), list(osids), list(ctxids)
        self.i = self.j = self.k = 0

    def oid(self):
        v = self.oids[self.i] if self.i < len(self.oids) else None
        self.i += 1
        return v

    def osid(self):
        v = self.osids[self.j] if self.j < len(self.osids) else None
        self.j += 1
        return v

    def ctx(self):
        v = self.ctxids[self.k] if self.k < len(self.ctxids) else None
        self.k += 1
        return v


def _parse_property_set(r: F.Reader, refs: _RefCursor) -> PropertySet:
    ps = PropertySet()
    count = r.u16()
    prids = [r.u32() for _ in range(count)]
    for prid in prids:
        pid = prid & 0x7FFFFFFF
        ptype = (prid >> 26) & 0x1F
        boolval = bool(prid >> 31)
        if ptype == PT_NODATA:
            val = None
        elif ptype == PT_BOOL:
            val = boolval
        elif ptype == PT_1:
            val = r.u8()
        elif ptype == PT_2:
            val = r.u16()
        elif ptype == PT_4:
            val = r.u32()
        elif ptype == PT_8:
            val = struct.unpack_from("<Q", r.read(8))[0]
        elif ptype == PT_LDATA:
            val = r.read(r.u32())
        elif ptype == PT_OID:
            val = refs.oid()
        elif ptype == PT_OIDS:
            val = [refs.oid() for _ in range(r.u32())]
        elif ptype == PT_OSID:
            val = refs.osid()
        elif ptype == PT_OSIDS:
            val = [refs.osid() for _ in range(r.u32())]
        elif ptype == PT_CTX:
            val = refs.ctx()
        elif ptype == PT_CTXS:
            val = [refs.ctx() for _ in range(r.u32())]
        elif ptype == PT_ARRAY_OF_PROPSETS:
            n = r.u32()
            val = []
            if n:
                r.u32()   # prid of the element property sets (always type 0x11)
                val = [_parse_property_set(r, refs) for _ in range(n)]
        elif ptype == PT_PROPSET:
            val = _parse_property_set(r, refs)
        else:
            raise ValueError(f"unknown property type {ptype:#x} for pid {pid:#x} at {r.pos}")
        ps.props.append(PropValue(pid, ptype, val))
    return ps


def _read_stream_header(r: F.Reader):
    v = r.u32()
    count = v & 0xFFFFFF
    ext = bool((v >> 30) & 1)
    osid_absent = bool((v >> 31) & 1)
    return count, ext, osid_absent


def parse_object_prop_set(data: bytes, obj_refs: list, cell_refs: list) -> PropertySet:
    """ObjectSpaceObjectPropSet (MS-ONESTORE §2.6.1).

    In the alternative packaging the compact IDs in the OID / OSID streams are
    positional: the i-th entry corresponds to obj_refs[i] / cell_refs[i]
    (MS-ONESTORE §2.8.x), so we ignore the compact id bytes themselves.
    """
    r = F.Reader(data)
    n_oids, ext, osid_absent = _read_stream_header(r)
    r.read(4 * n_oids)
    n_osids = n_ctx = 0
    if not osid_absent:
        n_osids, ext2, _ = _read_stream_header(r)
        r.read(4 * n_osids)
        if ext2:
            n_ctx, _, _ = _read_stream_header(r)
            r.read(4 * n_ctx)
    oids = obj_refs[:n_oids] if n_oids else []
    osids = cell_refs[:n_osids] if n_osids else []
    ctx = cell_refs[n_osids:n_osids + n_ctx] if n_ctx else []
    return _parse_property_set(r, _RefCursor(oids, osids, ctx))


# --------------------------------------------------------------------------- #
# Object graph
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    oid: ExGuid
    jcid: int = 0
    props: PropertySet = field(default_factory=PropertySet)
    file_data: Optional[bytes] = None      # partition 2 payload (image / embedded file bytes)
    space: Optional["ObjectSpace"] = None

    @property
    def jcid_name(self):
        return JCID_NAMES.get(self.jcid, f"jcid_{self.jcid:08X}")

    def get(self, pid, default=None):
        return self.props.get(pid, default)

    def __repr__(self):
        return f"<{self.jcid_name} {str(self.oid[0])[:8]}:{self.oid[1]} {self.props}>"


@dataclass
class ObjectSpace:
    cell: tuple
    revision: ExGuid
    roots: Dict[int, ExGuid]          # root role -> object id
    nodes: Dict[ExGuid, Node]

    def node(self, oid: ExGuid) -> Optional[Node]:
        return self.nodes.get(oid) if oid else None

    def resolve(self, oids) -> List[Node]:
        out = []
        for o in oids or []:
            n = self.node(o)
            if n is not None:
                out.append(n)
        return out

    @property
    def root(self) -> Optional[Node]:
        # role 1 = default content root
        oid = self.roots.get(1)
        return self.node(oid) if oid else None

    @property
    def metadata_root(self) -> Optional[Node]:
        oid = self.roots.get(2)
        return self.node(oid) if oid else None


class Section:
    """A parsed .one file: a section object space plus one object space per page."""

    def __init__(self, data: bytes):
        self.pkg = F.parse_package(data)
        manifest, cell_map, rev_map = F.storage_index_mappings(self.pkg)
        self._rev_map = rev_map
        self.spaces: List[ObjectSpace] = []
        self.space_by_cell: Dict[tuple, ObjectSpace] = {}
        for cell, (de_guid, _) in cell_map.items():
            cur_rev = self.pkg.cell_manifests.get(de_guid)
            if cur_rev is None:
                continue
            groups, roots = self._resolve_revision(cur_rev)
            nodes = self._build_nodes(groups)
            sp = ObjectSpace(cell, cur_rev, roots, nodes)
            for n in nodes.values():
                n.space = sp
            self.spaces.append(sp)
            self.space_by_cell[cell] = sp
            # a cell id is (EXGUID1, EXGUID2); object-space references from
            # property sets carry the same pair, so index under both shapes
            self.space_by_cell[cell[0]] = sp

    # -- revisions -----------------------------------------------------------
    def _resolve_revision(self, rev_id: ExGuid):
        """Follow the base-revision chain; returns (ordered group guids, roots)."""
        chain = []
        seen = set()
        cur = rev_id
        while cur and cur not in seen:
            seen.add(cur)
            mapping = self._rev_map.get(cur)
            if not mapping:
                break
            rm = self.pkg.revision_manifests.get(mapping[0])
            if not rm:
                break
            chain.append(rm)
            cur = rm[1]
        groups, roots = [], {}
        for rm in reversed(chain):           # oldest first so newer overrides
            _, _, rm_roots, rm_groups = rm
            for root, obj in rm_roots:
                roots[root[1]] = obj
            groups.extend(rm_groups)
        return groups, roots

    def _build_nodes(self, group_guids) -> Dict[ExGuid, Node]:
        nodes: Dict[ExGuid, Node] = {}
        for g in group_guids:
            og = self.pkg.object_groups.get(g)
            if og is None:
                continue
            for o in og.objects:
                node = nodes.setdefault(o.decl.oid, Node(o.decl.oid))
                data = o.data
                if o.decl.blob_ref is not None and not data:
                    data = self.pkg.blobs.get(o.decl.blob_ref, b"")
                if o.decl.partition == 4:
                    node.jcid = struct.unpack_from("<I", data)[0] if len(data) >= 4 else 0
                elif o.decl.partition == 1:
                    node.props = parse_object_prop_set(data, o.refs, o.cell_refs)
                elif o.decl.partition == 2:
                    node.file_data = data
        return nodes

    # -- convenience ---------------------------------------------------------
    def space_for(self, osid) -> Optional[ObjectSpace]:
        if osid is None:
            return None
        return self.space_by_cell.get(osid) or self.space_by_cell.get((osid, osid))

    @property
    def section_space(self) -> Optional[ObjectSpace]:
        for sp in self.spaces:
            r = sp.root
            if r is not None and r.jcid == 0x00060007:
                return sp
        return None

    def all_nodes(self):
        for sp in self.spaces:
            yield from sp.nodes.values()
