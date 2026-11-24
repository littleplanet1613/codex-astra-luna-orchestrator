"""Live-only acceptance. It refuses before making drawings unless the real guard passes."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def call(session, tool, **arguments):
    result = await session.call_tool(tool, arguments)
    if result.isError:
        messages = [content.text for content in result.content if content.type == "text"]
        raise RuntimeError(f"{tool}: {' '.join(messages)}")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


async def run(config, workspace, attach_verified_existing):
    config = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["autocad"]
    env = {**config["env"], "AUTOCAD2018_WORKSPACE": str(workspace)}
    if attach_verified_existing:
        env["AUTOCAD2018_ATTACH"] = "verified-exclusive"
    parameters = StdioServerParameters(command=config["command"], args=config["args"], env=env)
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            diagnostic = await call(session, "diagnose_autocad")
            print(json.dumps(diagnostic, ensure_ascii=False), flush=True)
            if diagnostic["preflight"] != "passed":
                return 2
            if not diagnostic["attach_enabled"]:
                raise RuntimeError("The deployment's COM permission is off. Use the explicit live-acceptance flag.")
            active = await call(session, "autocad_identity")
            assert active["pid"] == diagnostic["target_pid"] and active["hwnd"] == diagnostic["target_hwnd"]
            print(json.dumps(active, ensure_ascii=False), flush=True)
            with tempfile.TemporaryDirectory(dir=workspace, prefix=".acad-run-") as reservation:
                # Reserve a unique name and keep the finished DWG/PDF alongside it.
                label = Path(reservation).name.removeprefix(".acad-run-")
                seed = workspace / f"{label}-seed.dwg"
                source = workspace / f"{label}-source.dwg"
                target = workspace / f"{label}-acceptance.dwg"
                pdf = workspace / f"{label}-acceptance.pdf"
            await call(session, "new_working_dwg", output_path=str(seed))
            await call(session, "close_working_dwg", drawing_path=str(seed))
            with seed.open("rb") as original, source.open("xb") as baseline:
                shutil.copyfileobj(original, baseline)
            original_hash = digest(source)
            assert source.read_bytes()[:6] == b"AC1032"
            await call(session, "open_working_copy", source_path=str(source), output_path=str(target))

            async def tool(name, **kwargs):
                return await call(session, name, drawing_path=str(target), **kwargs)

            await tool("upsert_layer", name="SURVEY_TEST", aci=7, plottable=True)
            objects = {}
            specimens = {
                "point": {"points": [[65, 40, 0]]},
                "line": {"points": [[10, 15, 0], [100, 15, 0]]},
                "circle": {"points": [[55, 85, 0]], "radius": 7},
                "polyline": {"points": [[15, 50, 0], [45, 50, 0], [45, 65, 0]], "closed": True},
                "polyline3d": {"points": [[70, 63, 1], [90, 68, 2], [98, 70, 3]]},
                "arc": {"points": [[30, 92, 0]], "radius": 9, "angles_rad": [0, 2.2]},
            }
            for kind, data in specimens.items():
                objects[kind] = await tool("add_geometry", kind=kind, layer="SURVEY_TEST", **data)
            await tool("edit_entity", handle=objects["line"]["handle"], expected_type="AcDbLine",
                       line_endpoints_wcs=[[10, 15, 0], [105, 15, 0]])
            await tool("edit_entity", handle=objects["circle"]["handle"], expected_type="AcDbCircle", radius=8)
            for kind, points, text in (
                ("text", [[12, 105, 0]], "AUTO CAD 2018 ACCEPTANCE"),
                ("mtext", [[72, 98, 0]], "Reference point P01"),
                ("aligned_dimension", [[10, 15, 0], [105, 15, 0], [55, 5, 0]], ""),
                ("multileader", [[55, 85, 0], [73, 88, 0]], "P01 / ELEV +0.000"),
            ):
                objects[kind] = await tool("add_annotation", kind=kind, layer="SURVEY_TEST",
                                           points=points, text=text)
            await tool("define_marker_block", name="SURVEY_MARK", radius=2, attribute_tag="ID",
                       default_value="OLD")
            objects["block"] = await tool("insert_block", name="SURVEY_MARK", layer="SURVEY_TEST",
                                           insertion_wcs=[22, 38, 0])
            marker = objects["block"]["handle"]
            await tool("set_block_attribute", handle=marker, tag="ID", expected_value="OLD", value="P01")
            device = "DWG To PDF.pc3"
            configured = await tool("configure_sheet", name="Acceptance", device=device, landscape=True)
            media = next((item for item in configured["available_media"]
                          if re.search(r"\bA4\b|_A4_", item, re.I)
                          and "full" not in item.casefold()), None)
            if media is None:
                raise RuntimeError("No regular A4 media was reported by the AutoCAD PDF device.")
            await tool("configure_sheet", name="Acceptance", device=device, media=media, landscape=True)
            initial = await call(session, "drawing_inventory")
            initial_sheet = next(item for item in initial["layouts"] if item["name"] == "Acceptance")
            initial_handles = {item["handle"] for item in initial_sheet["entities"]}
            viewport = await tool("set_plan_viewport", layout="Acceptance", center_paper=[140, 105, 0],
                                  width=220, height=150, target_wcs=[56, 58, 0], scale=1)
            await tool("add_annotation", kind="text", layout="Acceptance", layer="SURVEY_TEST",
                       points=[[28, 15, 0]], text="SYNTHETIC TEST SHEET / 1:1", height=3.5)
            await tool("save_drawing")
            await tool("close_working_dwg")
            opened = await tool("open_working_dwg")
            assert not opened["already_open"]
            inventory = await call(session, "drawing_inventory")
            layouts = (await tool("list_layouts"))["layouts"]
            model_name = next(item["name"] for item in layouts if item["model"])
            model = next(item for item in inventory["layouts"] if item["name"] == model_name)
            model_objects = {item["handle"]: item for item in model["entities"]}
            for item in objects.values():
                assert model_objects[item["handle"]]["type"] == item["type"]
            assert model_objects[objects["line"]["handle"]]["EndPoint"] == [105, 15, 0]
            assert model_objects[objects["polyline3d"]["handle"]]["vertices_wcs"][-1] == [98, 70, 3]
            assert any(a["value"] == "P01" for a in (await tool("inspect_block_attributes", handle=marker))["attributes"])
            sheet = next(item for item in inventory["layouts"] if item["name"] == "Acceptance")
            saved_viewport = next(item for item in sheet["entities"] if item["handle"] == viewport["handle"])
            assert saved_viewport["DisplayLocked"] and abs(saved_viewport["CustomScale"] - 1) < 1e-9
            new_viewports = {item["handle"] for item in sheet["entities"]
                             if item["type"] == "AcDbViewport"} - initial_handles
            assert new_viewports == {viewport["handle"]}
            await tool("export_sheet_pdf", layout="Acceptance", output_path=str(pdf))
            assert pdf.read_bytes()[:5] == b"%PDF-"
            assert digest(source) == original_hash
            print(json.dumps({"dwg": str(target), "pdf": str(pdf), "source_sha256": original_hash,
                              "model_handles": len(objects), "viewport": saved_viewport["handle"],
                              "visual_qa": "pending"}, ensure_ascii=False), flush=True)
            return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--attach-verified-existing", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.config, args.workspace.resolve(strict=True), args.attach_verified_existing)))
