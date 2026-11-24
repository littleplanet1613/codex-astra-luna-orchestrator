# AutoCAD 2018 ActiveX guard

Use the existing local FastMCP backend and its Python environment with pywin32. The wrapper
replaces the backend's attach function before the stdio server starts.

- Set `AUTOCAD2018_BACKEND` to the reviewed local backend's absolute path.
- Use `guarded_mcp.py` as the MCP server entry point in Codex.
- Disable the Civil 3D MCP entry in the Codex configuration for this workflow.
- Launch standalone AutoCAD 2018 only through a verified Autodesk installation, explicit
  `/product ACAD`, installed language and named standalone profile. Check the title after initialization.

The guard defaults to preflight only. Enable COM with `AUTOCAD2018_ATTACH=verified-exclusive`
only after confirming an exclusive standalone R22 setup. It checks the standalone product and
executable registration, startup arguments, title, responsiveness, loaded modules and separate
standalone profile before attempting `GetActiveObject`. AutoCAD 2018 and Civil 3D 2018 may
share the executable and versioned COM registration. Multiple R22 processes prevent a
unique match. The wrapper verifies the COM window's process, version, executable and
profile before returning the object to the backend.

The guard has no CAD launcher. It does not open drawings or use the Civil 3D API. Existing
backend actions keep their documented scope. Before any write, use a test or approved copy
and verify its complete DWG path.

Run the offline guard tests with the existing Windows Python environment:

```powershell
python -m unittest discover -s integrations/autocad2018
```
