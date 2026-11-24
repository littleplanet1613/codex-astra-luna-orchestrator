"""Path-scoped drafting tools for standalone AutoCAD."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Literal

import pythoncom
import win32com.client
import win32process

DWG_2018 = 64
PLOT_LAYOUT = 5
MILLIMETERS = 1
VIEWPORT_CUSTOM_SCALE = 1
MTEXT_CONTENT = 2
ADD_MLEADER_R22 = 1605
PDF_DEVICES = {
    "dwg to pdf.pc3", "autocad pdf (general documentation).pc3",
    "autocad pdf (high quality print).pc3", "autocad pdf (smallest file).pc3",
    "autocad pdf (web and mobile).pc3",
}
TEXT_TYPES = {"AcDbText", "AcDbMText", "AcDbMLeader"}
GEOMETRY_TYPES = {"AcDbPoint", "AcDbLine", "AcDbCircle", "AcDbArc",
                  "AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"}
EDIT_TYPES = GEOMETRY_TYPES | TEXT_TYPES | {"AcDbBlockReference", "AcDbAlignedDimension"}
TOOLS = (
    "autocad_identity", "active_drawing", "activate_drawing", "save_copy", "save_drawing",
    "new_working_dwg", "open_working_copy", "open_working_dwg",
    "close_working_dwg", "list_layouts", "drawing_units", "upsert_layer", "add_geometry", "add_geometry_batch", "edit_entity",
    "add_annotation", "define_marker_block", "define_block_from_entities",
    "insert_block", "inspect_block_attributes", "set_block_attribute",
    "configure_sheet", "set_plan_viewport", "export_sheet_pdf",
)


def positive(value, label):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive and finite.")
    return value


def cad_name(value):
    if not value or value != value.strip() or re.search(r'[<>/\\":;?*|=,\x00-\x1f]', value):
        raise ValueError("Expected a local AutoCAD name without path or reserved characters.")
    return value


def lookup(collection, name):
    return next((item for item in collection if item.Name.casefold() == name.casefold()), None)


def separate_points(first, second):
    if math.dist(first, second) < 1e-10:
        raise ValueError("Identical consecutive points are not allowed.")


def existing_asset(collection, name):
    item = lookup(collection, cad_name(name))
    if item is None:
        raise ValueError(f"Named AutoCAD resource does not exist: {name}")
    return item


def array(values):
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, list(values))


def set_short_variable(doc, name, value):
    # AutoCAD's integer system variables require the original 16-bit Variant type.
    doc.SetVariable(name, win32com.client.VARIANT(pythoncom.VT_I2, int(value)))


def pdf_device(layout, name):
    candidates = {item.casefold(): item for item in layout.GetPlotDeviceNames()}
    if name.casefold() not in PDF_DEVICES or name.casefold() not in candidates:
        raise ValueError("Select an installed AutoCAD PDF PC3 device.")
    return candidates[name.casefold()]


def active_profile(app):
    try:
        return str(app.Preferences.Profiles.ActiveProfile)
    except (pythoncom.com_error, AttributeError):
        if app.Documents.Count:
            return str(app.ActiveDocument.GetVariable("CPROFILE"))
        raise RuntimeError("The active AutoCAD profile could not be read on the Start tab.") from None


def add_mleader(space, points):
    # R22 declares the leader index as an [out] Long*. pywin32 allocates it.
    result = space._oleobj_.InvokeTypes(
        ADD_MLEADER_R22, 0, pythoncom.DISPATCH_METHOD, (pythoncom.VT_DISPATCH, 0),
        ((pythoncom.VT_VARIANT, 1), (pythoncom.VT_BYREF | pythoncom.VT_I4, 2)), points,
    )
    entity = result[0] if isinstance(result, tuple) else result
    return win32com.client.Dispatch(entity)


class DrawingTools:
    def __init__(self, backend, product):
        self.backend = backend
        self.product = product
        self.raw_document = backend.document

    def register(self):
        self.backend.document = self.document
        for name in ("active_drawing", "activate_drawing", "save_copy", "save_drawing"):
            self.backend.server.remove_tool(name)
        for name in TOOLS:
            self.backend.tool(getattr(self, name))

    @staticmethod
    def workspace():
        configured = os.environ.get("AUTOCAD2018_WORKSPACE")
        if not configured:
            raise ValueError("CAD writes require an explicitly configured working-copy directory.")
        return Path(configured).resolve(strict=True)

    def receipt_path(self, path):
        root = self.workspace()
        identifier = hashlib.sha256(os.path.normcase(str(path.relative_to(root))).encode("utf-8")).hexdigest()
        return root / ".cad-managed" / identifier

    @staticmethod
    def file_identity(path):
        properties = path.stat()
        if properties.st_nlink != 1:
            raise ValueError("A managed drawing must have exactly one hard link.")
        return {"path": os.path.normcase(str(path)), "dev": properties.st_dev, "inode": properties.st_ino}

    def record_managed(self, path, *, refresh=False):
        receipt = self.receipt_path(path)
        receipt.parent.mkdir(exist_ok=True)
        if receipt.parent.resolve() != receipt.parent:
            raise ValueError("Managed-copy receipts must live directly inside the working directory.")
        record = self.file_identity(path)
        if not refresh:
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(record, stream)
        else:
            with tempfile.NamedTemporaryFile(
                mode="w", prefix=".receipt-", dir=receipt.parent, encoding="utf-8", delete=False,
            ) as stream:
                json.dump(record, stream)
                staged = Path(stream.name)
            staged.replace(receipt)

    def working_path(self, name, suffix=".dwg", *, new=False):
        root = self.workspace()
        supplied = Path(name)
        if not root.is_dir() or not supplied.is_absolute() or supplied.suffix.casefold() != suffix:
            raise ValueError(f"Expected an absolute {suffix} path in the working-copy directory.")
        path = supplied.resolve()
        if (supplied != path or not path.is_relative_to(root) or not path.parent.is_dir()
                or any(part.casefold() == ".cad-managed" or part.casefold().startswith(".cad-stage-")
                       for part in path.relative_to(root).parts)):
            raise ValueError("The resolved path must be inside the working-copy directory, without aliases.")
        if new:
            if (supplied.exists() or supplied.is_symlink()
                    or (suffix == ".dwg" and self.receipt_path(path).exists())):
                raise FileExistsError("The output already exists.")
        elif not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("Expected an existing, non-hard-linked working file.")
        else:
            receipt = self.receipt_path(path)
            if (not receipt.is_file() or receipt.resolve() != receipt
                    or json.loads(receipt.read_text(encoding="utf-8")) != self.file_identity(path)):
                raise ValueError("The DWG is not a managed copy. Use open_working_copy to stage it.")
        return path

    def document(self, expected=None, writable=False):
        if writable:
            self.working_path(expected or "")
        return self.raw_document(expected, writable)

    @contextmanager
    def change(self, drawing_path):
        doc = self.document(drawing_path, True)
        doc.StartUndoMark()
        try:
            yield doc
        finally:
            doc.EndUndoMark()

    def app_for_document_switch(self):
        app = self.backend.app()
        self.ready_documents(app)
        return app

    @staticmethod
    def ready_documents(app):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if app.GetAcadState().IsQuiescent:
                    documents = app.Documents
                    documents.Count
                    return documents
            except (pythoncom.com_error, AttributeError):
                pass
            pythoncom.PumpWaitingMessages()
            time.sleep(0.25)
        raise RuntimeError("AutoCAD did not become ready for a document switch.")

    def ready_document(self, app, doc):
        # Opening or creating a DWG can queue startup routines after the call returns.
        # Wait and rebind this same document, without issuing another Open or Add.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if app.GetAcadState().IsQuiescent:
                    candidate = win32com.client.Dispatch(doc._oleobj_)
                    if candidate.GetVariable("CMDACTIVE") == 0:
                        return candidate
            except (pythoncom.com_error, AttributeError):
                pass
            pythoncom.PumpWaitingMessages()
            time.sleep(0.25)
        raise RuntimeError("The new or opened drawing is waiting for an AutoCAD startup operation. Inspect it before retrying.")

    def identity(self, app):
        hwnd = app.HWND
        return {
            "version": str(app.Version), "product": self.product,
            "pid": win32process.GetWindowThreadProcessId(hwnd)[1], "hwnd": hwnd,
            "profile": active_profile(app), "documents": app.Documents.Count,
        }

    def autocad_identity(self) -> dict:
        """Report the verified COM application identity without requiring an open DWG."""
        return self.identity(self.backend.app())

    def active_drawing(self) -> dict:
        """Report verified AutoCAD PID, HWND, profile, version and active DWG."""
        return self.drawing_state(self.backend.app())

    def drawing_state(self, app):
        doc = app.ActiveDocument
        return {
            **self.identity(app),
            "path": doc.FullName, "name": doc.Name, "saved": bool(doc.Saved),
            "model_entities": doc.ModelSpace.Count, "space": doc.GetVariable("CTAB"),
            "insunits": int(doc.GetVariable("INSUNITS")),
            "ucs_name": str(doc.GetVariable("UCSNAME")),
            "ucs_origin": list(doc.GetVariable("UCSORG")),
            "ucs_x": list(doc.GetVariable("UCSXDIR")),
            "ucs_y": list(doc.GetVariable("UCSYDIR")),
        }

    def activate_drawing(self, drawing_path: str) -> dict:
        """Activate an already open drawing by exact path and report the verified AutoCAD identity."""
        if not Path(drawing_path).is_absolute():
            raise ValueError("An absolute drawing path is required.")
        app = self.app_for_document_switch()
        for doc in app.Documents:
            if doc.Path and self.backend.norm(doc.FullName) == self.backend.norm(drawing_path):
                doc.Activate()
                return self.drawing_state(app)
        raise ValueError("Requested DWG is not open.")

    def new_working_dwg(self, output_path: str) -> dict:
        """Create a blank drawing under the working directory, using the default AutoCAD template."""
        path = self.working_path(output_path, new=True)
        app = self.app_for_document_switch()
        doc = self.ready_document(app, app.Documents.Add())
        if doc.FullName:
            raise RuntimeError("AutoCAD did not return a new, unsaved drawing.")
        promoted = self.stage_save_and_reopen(app, doc, path)
        return {"path": str(path), "saved": bool(promoted.Saved)}

    def stage_save_and_reopen(self, app, doc, target):
        # AutoCAD SaveAs may overwrite an existing path. Only give it a private directory.
        directory = Path(tempfile.mkdtemp(prefix=".cad-stage-", dir=target.parent))
        staged = directory / "drawing.dwg"
        doc.SaveAs(str(staged), DWG_2018)
        if not doc.Saved or self.backend.norm(doc.FullName) != self.backend.norm(str(staged)):
            raise RuntimeError(f"SaveAs did not confirm the private staging file: {staged}")
        doc.Close(False)
        staged.rename(target)  # No replacement on Windows, even if another writer won the race.
        self.record_managed(target)
        documents = self.ready_documents(app)
        return self.ready_document(app, documents.Open(str(target), False))

    def save_copy(self, drawing_path: str, output_path: str) -> dict:
        """Save an active working DWG under a new working path, including its in-memory edits."""
        target = self.working_path(output_path, new=True)
        doc = self.document(drawing_path, True)
        promoted = self.stage_save_and_reopen(doc.Application, doc, target)
        return {"path": promoted.FullName, "saved": bool(promoted.Saved), "model_entities": promoted.ModelSpace.Count}

    def save_drawing(self, drawing_path: str) -> dict:
        """Save an active managed DWG and refresh its disk identity after an AutoCAD save."""
        path = self.working_path(drawing_path)
        doc = self.document(drawing_path, True)
        doc.Save()
        if not doc.Saved:
            raise RuntimeError("AutoCAD did not confirm the document was saved.")
        self.record_managed(path, refresh=True)
        return {"path": doc.FullName, "saved": True}

    def open_working_copy(self, source_path: str, output_path: str) -> dict:
        """Copy an existing DWG on disk to a new work path and open only that copy."""
        source = Path(source_path)
        if not source.is_absolute() or source.suffix.casefold() != ".dwg" or not source.is_file():
            raise ValueError("The source must be an existing DWG specified by absolute path.")
        target = self.working_path(output_path, new=True)
        app = self.app_for_document_switch()
        with tempfile.TemporaryDirectory(prefix=".cad-copy-", dir=target.parent) as directory:
            staged = Path(directory) / "copy.dwg"
            with source.open("rb") as original, staged.open("xb") as copy:
                before = os.fstat(original.fileno())
                shutil.copyfileobj(original, copy)
                after = os.fstat(original.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError("The source changed while being copied.")
            staged.rename(target)
        self.record_managed(target)
        doc = self.ready_document(app, app.Documents.Open(str(target), False))
        return {"path": doc.FullName, "source_on_disk": str(source), "saved": bool(doc.Saved)}

    def open_working_dwg(self, drawing_path: str) -> dict:
        """Reopen or activate an existing working DWG by exact path."""
        path = self.working_path(drawing_path)
        app = self.app_for_document_switch()
        for doc in app.Documents:
            if doc.Path and self.backend.norm(doc.FullName) == self.backend.norm(str(path)):
                doc.Activate()
                return {"path": str(path), "already_open": True}
        doc = self.ready_document(app, app.Documents.Open(str(path), False))
        return {"path": doc.FullName, "already_open": False}

    def close_working_dwg(self, drawing_path: str) -> dict:
        """Close the explicitly named active work copy, only if it is already saved."""
        self.working_path(drawing_path)
        app = self.app_for_document_switch()
        doc = app.ActiveDocument
        if not doc.Path or self.backend.norm(doc.FullName) != self.backend.norm(drawing_path) or not doc.Saved:
            raise ValueError("The matching work copy must be active and already saved.")
        doc.Close(False)
        return {"path": drawing_path, "closed": True}

    def list_layouts(self, drawing_path: str) -> dict:
        """Read model and paper layout names and plotting settings."""
        doc = self.document(drawing_path)
        return {"path": doc.FullName, "layouts": [
            {"name": layout.Name, "model": bool(layout.ModelType), "device": layout.ConfigName,
             "media": layout.CanonicalMediaName, "objects": layout.Block.Count}
            for layout in doc.Layouts
        ]}

    def space(self, doc, layout):
        return doc.ModelSpace if layout is None else self.sheet(doc, layout).Block

    @staticmethod
    def sheet(doc, name):
        layout = existing_asset(doc.Layouts, name)
        if layout.ModelType:
            raise ValueError("Expected a named paper-space layout.")
        return layout

    def layer(self, doc, name):
        layer = existing_asset(doc.Layers, name)
        if layer.Lock or layer.Freeze:
            raise ValueError("The drawing layer is locked or frozen.")
        return layer.Name

    def entity(self, doc, handle, layout, allowed):
        if not re.fullmatch(r"[0-9a-fA-F]+", handle):
            raise ValueError("Expected an AutoCAD hexadecimal handle.")
        entity = doc.HandleToObject(handle)
        if entity.OwnerID != self.space(doc, layout).ObjectID or entity.ObjectName not in allowed:
            raise ValueError("Entity type or model/paper-space ownership does not match the requested edit.")
        self.layer(doc, entity.Layer)
        return entity

    def record(self, doc, entity):
        result = self.backend.entity_data(doc, entity)
        if entity.ObjectName in {"AcDbCircle", "AcDbArc"}:
            result.update(Center=list(entity.Center), Radius=entity.Radius)
        if entity.ObjectName == "AcDbPoint":
            result["Coordinates"] = list(entity.Coordinates)
        return result

    def upsert_layer(
        self, drawing_path: str, name: str, aci: int | None = None,
        linetype: str | None = None, visible: bool | None = None,
        locked: bool | None = None, plottable: bool | None = None,
    ) -> dict:
        """Create a named layer or update only its supplied properties."""
        cad_name(name)
        if aci is not None and not 1 <= aci <= 255:
            raise ValueError("Layer ACI color must be in 1–255.")
        with self.change(drawing_path) as doc:
            selected = existing_asset(doc.Linetypes, linetype).Name if linetype is not None else None
            layer = lookup(doc.Layers, name)
            created = layer is None
            if created:
                layer = doc.Layers.Add(name)
            for key, value in (("Color", aci), ("Linetype", selected), ("LayerOn", visible),
                               ("Lock", locked), ("Plottable", plottable)):
                if value is not None:
                    setattr(layer, key, value)
            return {"name": layer.Name, "created": created, "aci": layer.Color,
                    "linetype": layer.Linetype, "locked": bool(layer.Lock)}

    def geometry_coordinates(self, kind, points, radius, angles_rad, closed):
        if kind not in {"point", "line", "circle", "arc", "polyline", "polyline3d"}:
            raise ValueError("Unsupported geometry kind.")
        coordinates = [self.backend.xyz(point) for point in points]
        if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in coordinates):
            raise ValueError("Expected finite WCS XYZ coordinates.")
        if kind in {"point", "circle", "arc"} and len(coordinates) != 1:
            raise ValueError("Expected one WCS point.")
        if kind == "line" and len(coordinates) != 2:
            raise ValueError("Expected two WCS endpoints.")
        if kind in {"polyline", "polyline3d"} and len(coordinates) < (3 if closed else 2):
            raise ValueError("Too few polyline vertices.")
        for a, b in zip(coordinates, coordinates[1:]):
            separate_points(a, b)
        if closed:
            if kind not in {"polyline", "polyline3d"}:
                raise ValueError("Only polylines take a closed flag.")
            separate_points(coordinates[0], coordinates[-1])
        if kind == "polyline" and any(p[2] != coordinates[0][2] for p in coordinates):
            raise ValueError("A planar WCS polyline requires a common Z. Use polyline3d for varied Z.")
        if kind in {"circle", "arc"}:
            positive(radius if radius is not None else 0, "Radius")
        if kind == "arc" and (angles_rad is None or len(angles_rad) != 2
                              or not all(math.isfinite(angle) for angle in angles_rad)):
            raise ValueError("Two finite arc angles in radians are required.")
        return coordinates

    @staticmethod
    def create_geometry(space, name, kind, coordinates, radius, angles_rad, closed):
        points_v = [array(point) for point in coordinates]
        if kind == "point":
            entity = space.AddPoint(points_v[0])
        elif kind == "line":
            entity = space.AddLine(*points_v)
        elif kind == "circle":
            entity = space.AddCircle(points_v[0], radius)
        elif kind == "arc":
            entity = space.AddArc(points_v[0], radius, *angles_rad)
        elif kind == "polyline":
            entity = space.AddLightWeightPolyline(array(v for point in coordinates for v in point[:2]))
            entity.Normal = array([0.0, 0.0, 1.0])
            entity.Elevation = coordinates[0][2]
            entity.Closed = closed
        else:
            entity = space.Add3DPoly(array(v for point in coordinates for v in point))
            entity.Closed = closed
        entity.Layer = name
        entity.Update()
        return entity

    def add_geometry(
        self, drawing_path: str, layer: str,
        kind: Literal["point", "line", "circle", "arc", "polyline", "polyline3d"],
        points: list[list[float]], layout: str | None = None, radius: float | None = None,
        angles_rad: list[float] | None = None, closed: bool = False,
    ) -> dict:
        """Create geometry from WCS XYZ coordinates, on the specified layer and space."""
        coordinates = self.geometry_coordinates(kind, points, radius, angles_rad, closed)
        with self.change(drawing_path) as doc:
            entity = self.create_geometry(self.space(doc, layout), self.layer(doc, layer),
                                          kind, coordinates, radius, angles_rad, closed)
            return self.record(doc, entity)

    def drawing_units(self, drawing_path: str) -> dict:
        """Read unit metadata and UCS axes without changing the drawing."""
        doc = self.document(drawing_path)
        keys = ("INSUNITS", "MEASUREMENT", "LUNITS", "LUPREC", "UCSNAME",
                "UCSORG", "UCSXDIR", "UCSYDIR", "CTAB")
        variables = {key: doc.GetVariable(key) for key in keys}
        return {"path": drawing_path, "variables": variables,
                "geometry_units_confirmed": False}

    def add_geometry_batch(
        self, drawing_path: str, layer: str,
        kind: Literal["point", "line", "circle", "arc", "polyline", "polyline3d"],
        items: list[list[list[float]]], expected_insunits: int,
        layout: str | None = None, radius: float | None = None,
        angles_rad: list[float] | None = None, closed: bool = False,
    ) -> dict:
        """Create up to 200 same-kind objects in one guarded work-copy call; return compact handles."""
        if not 1 <= len(items) <= 200 or sum(map(len, items)) > 10000:
            raise ValueError("Use 1–200 objects and at most 10000 total vertices per batch.")
        if type(expected_insunits) is not int or not 0 <= expected_insunits <= 24:
            raise ValueError("Supply the observed INSUNITS integer; no automatic unit conversion.")
        coordinates = [self.geometry_coordinates(kind, item, radius, angles_rad, closed)
                       for item in items]
        handles = []
        with self.change(drawing_path) as doc:
            actual = int(doc.GetVariable("INSUNITS"))
            if actual != expected_insunits:
                raise ValueError("INSUNITS changed. Verify drawing units before writing.")
            name = self.layer(doc, layer)
            space = self.space(doc, layout)
            before = int(space.Count)
            try:
                for item in coordinates:
                    entity = self.create_geometry(space, name, kind, item, radius, angles_rad, closed)
                    handles.append(str(entity.Handle))
                delta = int(space.Count) - before
                if delta != len(items):
                    raise RuntimeError("Unexpected entity count change.")
            except Exception as error:
                raise RuntimeError(
                    f"Batch may be partial; {len(handles)} completed handles: {handles}. "
                    "Inspect the working copy or undo the batch before retrying."
                ) from error
        return {"created": len(handles), "handles": handles, "layer": name,
                "kind": kind, "insunits": actual, "entity_count_delta": delta}

    def edit_entity(
        self, drawing_path: str, handle: str, expected_type: str, layout: str | None = None,
        layer: str | None = None, displacement_wcs: list[float] | None = None,
        text: str | None = None, radius: float | None = None, style: str | None = None,
        line_endpoints_wcs: list[list[float]] | None = None,
        leader_arrow_size: float | None = None, leader_landing_gap: float | None = None,
    ) -> dict:
        """Move or update an exact handle on a work copy, preserving unsupplied properties."""
        if expected_type not in EDIT_TYPES:
            raise ValueError("Unsupported exact AutoCAD entity type.")
        displacement = None if displacement_wcs is None else self.backend.xyz(displacement_wcs)
        if all(value is None for value in (layer, displacement, text, radius, style, line_endpoints_wcs,
                                            leader_arrow_size, leader_landing_gap)):
            raise ValueError("Provide at least one edit.")
        for value, label in ((leader_arrow_size, "Multileader arrow size"),
                             (leader_landing_gap, "Multileader landing gap")):
            if value is not None:
                if expected_type != "AcDbMLeader":
                    raise ValueError("Arrow and landing settings require a multileader.")
                positive(value, label)
        endpoints = None
        if line_endpoints_wcs is not None:
            if expected_type != "AcDbLine" or len(line_endpoints_wcs) != 2 or displacement is not None:
                raise ValueError("Absolute endpoints require a LINE and cannot be combined with displacement.")
            endpoints = [self.backend.xyz(point) for point in line_endpoints_wcs]
            separate_points(*endpoints)
        if text is not None and expected_type not in TEXT_TYPES | {"AcDbAlignedDimension"}:
            raise ValueError("The selected entity does not support a text edit.")
        if radius is not None:
            if expected_type not in {"AcDbCircle", "AcDbArc"}:
                raise ValueError("Only arcs and circles support a radius edit.")
            positive(radius, "Radius")
        with self.change(drawing_path) as doc:
            entity = self.entity(doc, handle, layout, {expected_type})
            target_layer = self.layer(doc, layer) if layer is not None else None
            if text is not None and expected_type == "AcDbMLeader" and entity.ContentType != MTEXT_CONTENT:
                raise ValueError("The multileader does not contain text.")
            style_name = None
            if style is not None:
                style_name = self.annotation_style(doc, expected_type, style)
            before = self.record(doc, entity)
            if endpoints is not None:
                # Choose the assignment order so a shared endpoint cannot create a transient zero length.
                if math.dist(endpoints[0], entity.EndPoint) < 1e-10:
                    if math.dist(endpoints[1], entity.StartPoint) < 1e-10:
                        raise ValueError("Swapping endpoints in place requires a distinct intermediate edit.")
                    entity.EndPoint = array(endpoints[1])
                    entity.StartPoint = array(endpoints[0])
                else:
                    entity.StartPoint = array(endpoints[0])
                    entity.EndPoint = array(endpoints[1])
            if displacement is not None:
                entity.Move(array([0.0, 0.0, 0.0]), array(displacement))
            if target_layer is not None:
                entity.Layer = target_layer
            if text is not None:
                setattr(entity, "TextOverride" if expected_type == "AcDbAlignedDimension" else "TextString", text)
            if radius is not None:
                entity.Radius = radius
            if style_name is not None:
                entity.StyleName = style_name
            if leader_arrow_size is not None:
                entity.ArrowheadType = 0
                entity.ArrowheadSize = leader_arrow_size
            if leader_landing_gap is not None:
                entity.DogLegged = True
                entity.LandingGap = leader_landing_gap
            entity.Update()
            return {"before": before, "after": self.record(doc, entity)}

    def annotation_style(self, doc, object_type, name):
        if object_type == "AcDbAlignedDimension":
            return existing_asset(doc.DimStyles, name).Name
        if object_type in {"AcDbText", "AcDbMText"}:
            return existing_asset(doc.TextStyles, name).Name
        if object_type == "AcDbMLeader":
            return existing_asset(doc.Dictionaries.Item("ACAD_MLEADERSTYLE"), name).Name
        raise ValueError("The selected entity does not support an annotation style.")

    def add_annotation(
        self, drawing_path: str, layer: str, kind: Literal["text", "mtext", "aligned_dimension", "multileader"],
        points: list[list[float]], layout: str | None = None, text: str = "",
        height: float = 2.5, width: float = 40, style: str | None = None,
    ) -> dict:
        """Create TEXT, MTEXT, an aligned dimension, or an MTEXT multileader."""
        pts = [self.backend.xyz(point) for point in points]
        if kind in {"text", "mtext"} and len(pts) != 1:
            raise ValueError("Expected one insertion point.")
        if kind == "aligned_dimension" and len(pts) != 3:
            raise ValueError("Expected two measured endpoints and a dimension position.")
        if kind == "multileader" and len(pts) < 2:
            raise ValueError("Expected an arrow point and at least one leader vertex.")
        if len(pts) > 1:
            for a, b in zip(pts, pts[1:]):
                separate_points(a, b)
        if kind != "aligned_dimension":
            if not text.strip():
                raise ValueError("Annotation text is required.")
            positive(height, "Height")
        if kind == "mtext":
            positive(width, "MTEXT width")
        types = {"text": "AcDbText", "mtext": "AcDbMText", "aligned_dimension": "AcDbAlignedDimension",
                 "multileader": "AcDbMLeader"}
        with self.change(drawing_path) as doc:
            name = self.layer(doc, layer)
            space = self.space(doc, layout)
            style_name = None if style is None else self.annotation_style(doc, types[kind], style)
            args = [array(point) for point in pts]
            if kind == "text":
                entity = space.AddText(text, args[0], height)
            elif kind == "mtext":
                entity = space.AddMText(args[0], width, text)
                entity.Height = height
            elif kind == "aligned_dimension":
                entity = space.AddDimAligned(*args)
                if text:
                    entity.TextOverride = text
            else:
                entity = add_mleader(space, array(v for point in pts for v in point))
                entity.ContentType = MTEXT_CONTENT
                entity.TextString = text
                entity.TextHeight = height
            if style_name is not None:
                entity.StyleName = style_name
            entity.Layer = name
            entity.Update()
            return self.record(doc, entity)

    def define_marker_block(
        self, drawing_path: str, name: str, radius: float,
        attribute_tag: str | None = None, default_value: str = "", text_height: float = 2.5,
    ) -> dict:
        """Define a reusable survey marker block on layer 0, optionally with a variable attribute."""
        cad_name(name)
        positive(radius, "Marker radius")
        if attribute_tag is not None:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", attribute_tag):
                raise ValueError("Expected an attribute tag with letters, digits, underscore or hyphen.")
            positive(text_height, "Attribute text height")
        with self.change(drawing_path) as doc:
            if lookup(doc.Blocks, name) is not None:
                raise ValueError("The block already exists.")
            block = doc.Blocks.Add(array([0.0, 0.0, 0.0]), name)
            block.AddCircle(array([0.0, 0.0, 0.0]), radius).Layer = "0"
            block.AddLine(array([-radius, 0.0, 0.0]), array([radius, 0.0, 0.0])).Layer = "0"
            block.AddLine(array([0.0, -radius, 0.0]), array([0.0, radius, 0.0])).Layer = "0"
            if attribute_tag is not None:
                attr = block.AddAttribute(text_height, 0, attribute_tag, array([radius * 1.5, 0.0, 0.0]),
                                          attribute_tag.upper(), default_value)
                attr.Layer = "0"
            return {"name": block.Name, "objects": block.Count}

    def insert_block(
        self, drawing_path: str, name: str, insertion_wcs: list[float], layer: str,
        layout: str | None = None, scale: float = 1, rotation_rad: float = 0,
    ) -> dict:
        """Insert an existing local block definition. External drawings and Xrefs are excluded."""
        insertion = self.backend.xyz(insertion_wcs)
        cad_name(name)
        positive(scale, "Block scale")
        if not math.isfinite(rotation_rad):
            raise ValueError("Rotation must be finite.")
        with self.change(drawing_path) as doc:
            name_layer = self.layer(doc, layer)
            definition = existing_asset(doc.Blocks, name)
            if definition.IsXRef or definition.IsLayout:
                raise ValueError("An Xref or layout block cannot be inserted.")
            insert = self.space(doc, layout).InsertBlock(array(insertion), definition.Name, scale, scale, scale, rotation_rad)
            insert.Layer = name_layer
            insert.Update()
            return self.record(doc, insert)

    def define_block_from_entities(
        self, drawing_path: str, name: str, base_wcs: list[float], handles: list[str],
        source_layout: str | None = None,
    ) -> dict:
        """Copy selected geometry or text into a new local block definition."""
        cad_name(name)
        base = self.backend.xyz(base_wcs)
        if not handles or len(set(handle.casefold() for handle in handles)) != len(handles):
            raise ValueError("Select one or more distinct source handles.")
        with self.change(drawing_path) as doc:
            if lookup(doc.Blocks, name) is not None:
                raise ValueError("The block already exists.")
            members = [self.entity(doc, handle, source_layout, GEOMETRY_TYPES | {"AcDbText", "AcDbMText"})
                       for handle in handles]
            block = doc.Blocks.Add(array([0.0, 0.0, 0.0]), name)
            ids = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, members)
            copied = doc.CopyObjects(ids, block)
            if copied and isinstance(copied[0], (list, tuple)):
                copied = copied[0]
            if not copied or len(copied) != len(members):
                raise RuntimeError("Unexpected copied entity count. Inspect the block before a retry.")
            for member in copied:
                win32com.client.Dispatch(member).Move(array(base), array([0.0, 0.0, 0.0]))
            return {"name": block.Name, "objects": block.Count, "source_handles": handles}

    def inspect_block_attributes(self, drawing_path: str, handle: str) -> dict:
        """Read editable and constant attribute values of one block reference."""
        doc = self.document(drawing_path)
        if not re.fullmatch(r"[0-9a-fA-F]+", handle):
            raise ValueError("Expected an AutoCAD hexadecimal handle.")
        insert = doc.HandleToObject(handle)
        if insert.ObjectName != "AcDbBlockReference":
            raise ValueError("Expected a block reference.")
        return {"handle": handle, "name": insert.EffectiveName, "attributes": [
            {"tag": item.TagString, "value": item.TextString, "constant": constant}
            for constant, items in ((False, insert.GetAttributes()), (True, insert.GetConstantAttributes()))
            for item in (items or ())
        ]}

    def set_block_attribute(
        self, drawing_path: str, handle: str, tag: str, expected_value: str, value: str,
        layout: str | None = None,
    ) -> dict:
        """Update one attribute only if its existing value still matches."""
        with self.change(drawing_path) as doc:
            insert = self.entity(doc, handle, layout, {"AcDbBlockReference"})
            definition = existing_asset(doc.Blocks, insert.EffectiveName)
            if definition.IsXRef:
                raise ValueError("External reference attributes cannot be edited.")
            attrs = [attribute for attribute in (insert.GetAttributes() or ())
                     if attribute.TagString.casefold() == tag.casefold()]
            if len(attrs) != 1 or attrs[0].TextString != expected_value:
                raise ValueError("Attribute tag or prior value did not match uniquely.")
            attribute = attrs[0]
            attribute.TextString = value
            attribute.Update()
            return {"handle": handle, "tag": attribute.TagString, "value": attribute.TextString}

    def configure_sheet(
        self, drawing_path: str, name: str, device: str,
        media: str | None = None, stylesheet: str | None = None, landscape: bool = False,
    ) -> dict:
        """Create or configure a named paper sheet for a PDF device, at 1:1 paper scale."""
        cad_name(name)
        with self.change(drawing_path) as doc:
            old = doc.ActiveLayout
            old.RefreshPlotDeviceInfo()
            chosen = pdf_device(old, device)
            layout = lookup(doc.Layouts, name)
            if layout is not None and layout.ModelType:
                raise ValueError("The model layout cannot be used as a paper sheet.")
            created = layout is None
            if created:
                layout = doc.Layouts.Add(name)
            layout.ConfigName = chosen
            layout.RefreshPlotDeviceInfo()
            available = list(layout.GetCanonicalMediaNames())
            if media is not None:
                if media not in available:
                    raise ValueError("Invalid canonical media name for the chosen device.")
                layout.CanonicalMediaName = media
            if stylesheet is not None:
                if stylesheet not in list(layout.GetPlotStyleTableNames()):
                    raise ValueError("Plot style table is unavailable.")
                layout.StyleSheet = stylesheet
                layout.PlotWithPlotStyles = True
            layout.PaperUnits = MILLIMETERS
            layout.PlotType = PLOT_LAYOUT
            layout.UseStandardScale = False
            layout.SetCustomScale(1, 1)
            layout.PlotRotation = 0
            width, height = layout.GetPaperSize()
            # Canonical device media can already be landscape.
            if (width > height) != landscape and width != height:
                layout.PlotRotation = 1
            if created:
                create_viewport = doc.GetVariable("LAYOUTCREATEVIEWPORT")
                try:
                    set_short_variable(doc, "LAYOUTCREATEVIEWPORT", 0)
                    doc.ActiveLayout = layout
                finally:
                    try:
                        doc.ActiveLayout = old
                    finally:
                        set_short_variable(doc, "LAYOUTCREATEVIEWPORT", create_viewport)
            return {"name": layout.Name, "created": created, "device": layout.ConfigName,
                    "media": layout.CanonicalMediaName, "available_media": available,
                    "native_paper_mm": [width, height], "rotation": layout.PlotRotation}

    def set_plan_viewport(
        self, drawing_path: str, layout: str, center_paper: list[float], width: float, height: float,
        target_wcs: list[float], scale: float, handle: str | None = None, layer: str = "0",
    ) -> dict:
        """Create or reconfigure a locked plan viewport. Scale is paper units per model unit."""
        center = self.backend.xyz(center_paper)
        target = self.backend.xyz(target_wcs)
        if center[2] != 0:
            raise ValueError("Paper-space viewport center must have Z=0.")
        positive(width, "Viewport width")
        positive(height, "Viewport height")
        positive(scale, "Viewport scale")
        with self.change(drawing_path) as doc:
            sheet = self.sheet(doc, layout)
            name_layer = self.layer(doc, layer)
            viewport = None if handle is None else self.entity(doc, handle, layout, {"AcDbViewport"})
            previous = doc.ActiveLayout
            try:
                doc.ActiveLayout = sheet
                if viewport is None:
                    viewport = doc.PaperSpace.AddPViewport(array(center), width, height)
                else:
                    viewport.DisplayLocked = False
                    viewport.Center = array(center)
                    viewport.Width = width
                    viewport.Height = height
                viewport.Layer = name_layer
                viewport.Display(True)
                viewport.Direction = array([0.0, 0.0, 1.0])
                viewport.Target = array(target)
                doc.MSpace = True
                try:
                    doc.ActivePViewport = viewport
                    doc.Application.ZoomCenter(array(target), 1.0)
                    viewport.StandardScale = VIEWPORT_CUSTOM_SCALE
                    viewport.CustomScale = scale
                finally:
                    doc.MSpace = False
                viewport.DisplayLocked = True
                viewport.Update()
                return self.record(doc, viewport)
            finally:
                doc.ActiveLayout = previous

    def export_sheet_pdf(self, drawing_path: str, layout: str, output_path: str) -> dict:
        """Export one already configured paper layout to a new PDF using foreground plotting."""
        target = self.working_path(output_path, ".pdf", new=True)
        doc = self.document(drawing_path, True)
        sheet = self.sheet(doc, layout)
        device = pdf_device(sheet, sheet.ConfigName)
        if sheet.PlotType != PLOT_LAYOUT:
            raise ValueError("The sheet must be configured to plot the layout.")
        previous = doc.ActiveLayout
        previous_plot = doc.GetVariable("BACKGROUNDPLOT")
        previous_quiet = doc.Plot.QuietErrorMode
        with tempfile.TemporaryDirectory(prefix=".cad-pdf-", dir=target.parent) as directory:
            temporary = Path(directory) / "sheet.pdf"
            try:
                doc.ActiveLayout = sheet
                set_short_variable(doc, "BACKGROUNDPLOT", 0)
                doc.Plot.QuietErrorMode = True
                if not doc.Plot.PlotToFile(str(temporary), device) or not temporary.is_file():
                    raise RuntimeError("AutoCAD did not report a successful PDF export.")
                with temporary.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        raise RuntimeError("AutoCAD output does not have a PDF header.")
                # Windows rename does not replace an existing destination.
                temporary.rename(target)
            finally:
                try:
                    doc.Plot.QuietErrorMode = previous_quiet
                finally:
                    try:
                        set_short_variable(doc, "BACKGROUNDPLOT", previous_plot)
                    finally:
                        doc.ActiveLayout = previous
        return {"path": str(target), "layout": layout, "bytes": target.stat().st_size}
