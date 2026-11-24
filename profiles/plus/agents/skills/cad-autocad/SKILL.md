---
name: cad-autocad
description: Edit DWG working copies and check sheet output using the local standalone AutoCAD MCP. Use for geometry, layers, block attributes, annotations, layouts, viewports and AutoCAD PDF plotting.
---

# Standalone AutoCAD drawing work

Use AutoCAD 2021 (R24.0) by default. AutoCAD 2018 (R22.0) is an explicitly selected secondary mode. Civil 3D is forbidden in every version.

1. Call autocad diagnose_autocad. Require one process in the selected COM release family, a responding standalone AutoCAD window, a verified module inventory and standalone product/profile registration. If the preflight refuses, stop before COM. After it passes, require active_drawing to report the same AutoCAD PID and HWND and a registered standalone profile. The profile name, a shared acad.exe path and a Civil 3D “as AutoCAD” shortcut cannot establish the product.
2. Inspect the source without modifying it. Use open_working_copy to copy the on-disk DWG into the configured run directory. Tell the user if unsaved source edits affect the request. For testing use new_working_dwg. Use exact DWG paths, documented units and WCS XYZ. Inspect the relevant entity before changing it. Preserve OCS plane, bulges and topology for existing planar polylines.
3. Use add_geometry, edit_entity, upsert_layer and the block and annotation tools. Verify the handle, type, layer and space returned after each edit. Use set_block_attribute with the observed old value. Inspect drawing_inventory after a partial failure before retrying a write. One undo mark per edit supports reversal of a partial call.
4. For sheets, retain any approved page setup and viewports. Use list_layouts and drawing_inventory before setting a viewport. Express scale as paper units per model unit. For model metres and paper millimetres, 1:100 is 10. For millimetres in both spaces, 1:100 is 0.01. Inspect for the default viewport after first activating a layout. Reuse the reviewed viewport or check overlaps after creating a new one. Lock the result.
5. Save the work copy, close and reopen by exact path. Compare the saved inventory with the expected objects and original. Export one sheet with an installed AutoCAD PDF PC3 configuration. Render and inspect the PDF at whole-sheet and detail scales. Check format, geometry, positional text, arrow endpoints, dimensions, margins, scale and legibility. Keep the DWG and PDF evidence together.

See [upstream mapping](references/upstream.md) for adapted CAD workflows and limits. Use geo-standards for the applicable Russian standards and approved project requirements.
