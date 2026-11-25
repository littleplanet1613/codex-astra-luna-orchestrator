"""Measure equivalent point writes through guarded MCP in independent copies."""
import argparse
import asyncio
import json
from pathlib import Path
import tomllib
from uuid import uuid4

from acceptance import ClientSession, StdioServerParameters, call, digest, stdio_client


def summarize(metrics):
    return {"calls": len(metrics), **{
        key: round(sum(row[key] for row in metrics), 2) for key in metrics[0]
    }}


async def benchmark(config, workspace, count, source):
    settings = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["autocad"]
    env = {**settings["env"], "AUTOCAD_WORKSPACE": str(workspace)}
    parameters = StdioServerParameters(command=settings["command"], args=settings["args"], env=env)
    tag = uuid4().hex[:8]
    source = source.resolve(strict=True)
    points = [[i * 2.5, 20.0 + i % 7, 100.0 + i / 4] for i in range(count)]
    results = {}
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            await call(session, "autocad_identity")
            initial_hash = digest(source)
            for mode in ("single", "batch"):
                target = workspace / f"benchmark-{tag}-{mode}.dwg"
                path = str(target)
                await call(session, "open_working_copy", source_path=str(source), output_path=path)
                units = (await call(session, "drawing_units", drawing_path=path))["variables"]
                await call(session, "upsert_layer", drawing_path=path, name="BENCH_POINTS", aci=7)
                shared = dict(drawing_path=path, layer="BENCH_POINTS", kind="point")
                metrics = []
                if mode == "single":
                    created = []
                    for i, point in enumerate(points, 1):
                        result = await call(session, "add_geometry", metrics, points=[point], **shared)
                        assert result["Coordinates"] == point
                        created.append(result["handle"])
                        if i % 25 == 0:
                            print(f"{i}/{count} single writes", flush=True)
                else:
                    result = await call(session, "add_geometry_batch", metrics,
                                        items=[[point] for point in points],
                                        expected_insunits=units["INSUNITS"], **shared)
                    assert result["created"] == result["entity_count_delta"] == count
                    created = result["handles"]
                    for handle, expected in zip(created, points, strict=True):
                        result = await call(session, "inspect_entity", handle=handle)
                        assert result["entity"]["Coordinates"] == expected
                assert len(set(created)) == count
                assert (await call(session, "active_drawing"))["model_entities"] == count
                await call(session, "save_drawing", drawing_path=path)
                await call(session, "close_working_dwg", drawing_path=path)
                results[mode] = summarize(metrics)
                print(json.dumps({mode: results[mode], "path": path, "insunits": units["INSUNITS"]}),
                      flush=True)
            assert digest(source) == initial_hash
            print(json.dumps({"verified_points_per_mode": count, "source_sha256": initial_hash}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--count", type=int, choices=range(100, 201), default=100, metavar="100..200")
    parser.add_argument("--source", required=True, type=Path)
    options = parser.parse_args()
    asyncio.run(benchmark(options.config, options.workspace.resolve(strict=True), options.count, options.source))
