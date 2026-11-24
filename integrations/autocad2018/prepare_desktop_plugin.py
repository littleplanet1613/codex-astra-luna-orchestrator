"""Prepare a private desktop plugin from the existing guarded Windows MCP.
No CAD launch, package installation, config overwrite or marketplace activation.
Python 3.11+. Run locally after inspecting the current Codex config.
"""
import argparse
import json
from pathlib import Path
import shutil
import tomllib

SAFE_ENV = {"AUTOCAD_RELEASE", "AUTOCAD2018_ATTACH", "AUTOCAD2018_BACKEND",
            "AUTOCAD2018_WORKSPACE", "CAD_LAUNCH", "AUTOCAD_MCP_BACKEND"}

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def absolute_existing(value, directory=False):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"Absolute path required: {path}")
    path = path.resolve(strict=True)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"Wrong path type: {path}")
    return str(path)

def prepare(config_path, output, release, server="autocad"):
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    entry = config["mcp_servers"][server]
    if entry.get("enabled") is False:
        raise ValueError("Existing guarded MCP is disabled; review its status first.")
    command = absolute_existing(entry["command"])
    args = entry.get("args", [])
    if Path(command).name.casefold() not in {"python.exe", "pythonw.exe", "python"}:
        raise ValueError("Expected the existing guarded Python MCP; resolve its interpreter explicitly.")
    if len(args) != 1 or Path(args[0]).name != "guarded_mcp.py":
        raise ValueError("Expected exactly the reviewed guarded_mcp.py entry point. Never use raw uvx here.")
    guard = absolute_existing(args[0])
    if entry.get("env_vars") or entry.get("cwd"):
        raise ValueError("Existing environment or working directory needs manual review.")
    env = dict(entry.get("env", {}))
    if set(env) - SAFE_ENV:
        raise ValueError("Unreviewed environment keys: " + ", ".join(sorted(set(env) - SAFE_ENV)))
    if env.get("AUTOCAD_RELEASE", "2021") != release:
        raise ValueError("Selected release differs from existing MCP configuration; review locally first.")
    env["AUTOCAD2018_BACKEND"] = absolute_existing(env["AUTOCAD2018_BACKEND"])
    env["AUTOCAD2018_WORKSPACE"] = absolute_existing(env["AUTOCAD2018_WORKSPACE"], directory=True)
    # Diagnostic only until the local operator has verified the selected application.
    env.update(AUTOCAD_RELEASE=release, AUTOCAD2018_ATTACH="preflight-only",
               CAD_LAUNCH="never", AUTOCAD_MCP_BACKEND="com")
    skills_root = Path(__file__).resolve().parents[2] / "profiles/plus/agents/skills"
    names = ("cad-autocad", "geo-survey", "geo-asbuilt", "geo-deviation",
             "geo-networks", "geo-standards", "geo-visual-qa")
    for name in names:
        if not (skills_root / name / "SKILL.md").is_file():
            raise ValueError(f"Missing reviewed skill: {name}")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plugin = output / "plugins" / "autocad-guarded"
    write_json(plugin / "plugin.json", {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "autocad-guarded", "version": "0.1.0",
        "description": "Private standalone AutoCAD geodesy tools, guarded local MCP.",
        "extensions": {"com.openai": {"requires_local_executor": True,
            "interface": {"displayName": "AutoCAD Geodesy (guarded)"}}}
    })
    write_json(plugin / "mcp.json", {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"autocad_guarded": {"type": "stdio", "command": command,
            "args": [guard], "env": env}}
    })
    for name in names:
        dest = plugin / "skills" / name
        shutil.copytree(skills_root / name, dest)
        skill = dest / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        if name == "cad-autocad":
            text = text.replace("Use AutoCAD 2021 (R24.0) by default. AutoCAD 2018 (R22.0) is an explicitly selected secondary mode.",
                                f"Use only verified standalone AutoCAD {release} for this plugin. No version fallback.")
        skill.write_text(text, encoding="utf-8")
    write_json(output / ".agents/plugins/marketplace.json", {
        "name": "local-cad-guarded", "interface": {"displayName": "Local CAD"},
        "plugins": [{"name": "autocad-guarded",
                     "source": {"source": "local", "path": "./plugins/autocad-guarded"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                     "category": "Productivity"}]
    })
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; not an existing marketplace.")
    parser.add_argument("--release", choices=("2018", "2021"), required=True)
    parser.add_argument("--server", default="autocad")
    args = parser.parse_args()
    print(prepare(args.config, args.output, args.release, args.server))
    print("Prepared only. Do not enable alongside the same direct MCP. Test diagnose_autocad first.")
