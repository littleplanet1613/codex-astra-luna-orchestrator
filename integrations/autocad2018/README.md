# Standalone AutoCAD 2018 MCP

The wrapper loads the existing local stdio backend and reuses its Python
environment with pywin32. It does not start AutoCAD or change Civil 3D settings.

## Attachment

Use the installed wrapper as the entry point. Set AUTOCAD2018_BACKEND to the
absolute path of the reviewed local Python backend. Set AUTOCAD2018_WORKSPACE
to an existing dedicated drawing-copy directory to enable writes. Keep the
Civil 3D integration disabled for this workflow.

A health check via diagnose_autocad only inspects Windows metadata. It reports
the COM registry entry separately from an actual connection. It requires one
R22 process in the current session and checks the image, loaded modules,
responding standalone window, its PID/HWND, standalone product/profile
registration and versioned COM server path. Startup arguments are tokenized
as Windows arguments. They may be reordered or omitted, and explicit vertical
choices are refused. A shortcut, title, shared executable, or ProgID alone
cannot identify the running product.

The default is preflight only. AUTOCAD2018_ATTACH=verified-exclusive permits
GetActiveObject on a passing preflight. The returned application must match
the verified PID, HWND, path, R22 release, standalone window and registered
profile before DWG access. Autodesk COM attachment is never attempted when
there are multiple R22 processes. An installed class registration does not
prove that a corresponding running object exists.

## Drawing operations

- Use new_working_dwg or open_working_copy to create a scoped DWG. The latter
  copies the existing on-disk file without opening it in AutoCAD. Save, close
  and reopen the working copy by exact path. The server records which files
  it created or copied and rejects unmanaged files, replacements, symlinks,
  and hard links for writes. An external save may require staging a new copy.
- Read with drawing_inventory, list_layouts, inspect_entity and
  inspect_block_attributes. Geometry includes point, line, circle, arc,
  planar WCS and 3D polyline. Use the relevant tool for WCS line endpoints,
  translation, radius and planar/3D polyline vertex changes.
- Use upsert_layer, define_marker_block, define_block_from_entities,
  insert_block and set_block_attribute on working drawings. Existing
  text/dimension/multileader styles can be selected. Annotation covers TEXT,
  MTEXT, aligned dimensions and text multileaders. Unsupported edits remain
  explicit. No raw AutoLISP, arbitrary command, Xref import or arbitrary file
  write endpoint is exposed.
- Use configure_sheet, set_plan_viewport and export_sheet_pdf. Plot to an
  installed AutoCAD PDF PC3 device. The foreground export stages output in the
  working directory and restores layout and plotting variables on failure.
  Inspect and undo a partial COM edit before retrying.

The adapted cad-autocad-2018 skill documents the upstream workflow mapping.
The send/receive acceptance runner creates a synthetic source DWG and works
on a new copy, tests the model and paper-space data after save/close/reopen,
exports a PDF and verifies the source hash. Run it with the same existing
Python environment on a Windows session with verified standalone AutoCAD 2018.

    python -m unittest discover -s integrations/autocad2018
    python integrations/autocad2018/acceptance.py --config <Codex-config> --workspace <existing-workspace> --attach-verified-existing

Exit code 2 means preflight blocked live testing before any drawing operation.
If a different AutoCAD release is running, the diagnostic reports it. The old
local backend defaulted to AutoCAD.Application.24 (AutoCAD 2021); that legacy
default does not qualify as a standalone R22 session.

The acceptance runner checks that every tool advertised by the local MCP
was successfully exercised. Geometry copy, deletion and the DWG round trip
use synthetic working drawings. Saved artifacts include a separately saved DWG
copy and a PDF. The runner compares source and donor hashes after the test.
A successful live run still requires PDF rendering and visual inspection.
