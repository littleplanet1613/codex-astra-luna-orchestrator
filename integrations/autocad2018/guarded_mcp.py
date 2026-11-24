"""Attach the existing local AutoCAD MCP only to proven standalone AutoCAD 2018."""
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import winreg

import pywintypes
import win32com.client
import win32process

PROGID = "AutoCAD.Application.22"
PRODUCT_KEY = r"SOFTWARE\Autodesk\AutoCAD\R22.0\ACAD-1001"
STARTUP = re.compile(
    r'^"[^"]+" +/product +"?ACAD"? +/language +"?(ru-RU|en-US)"? +/p +"([^"]+)"\s*$',
    re.IGNORECASE,
)
CAPTION = re.compile(r"^(?:Autodesk )?AutoCAD 2018(?:\s*[-–].*)?$", re.IGNORECASE)
LANGUAGE_IDS = {"ru-ru": "419", "en-us": "409"}
VERSION = re.compile(r"^R?(\d+)\.\d+(?:[.\w -]*)$", re.IGNORECASE)
PLAIN_PRODUCT = re.compile(r"^(?:Autodesk )?AutoCAD 2018(?:\s*[—–-]\s*.+)?$", re.IGNORECASE)


def registry_value(hive, key, value):
    with winreg.OpenKey(hive, key) as registry_key:
        return winreg.QueryValueEx(registry_key, value)[0]


def process_inventory():
    # WMI only, before any Autodesk COM connection. Emit metadata for this session.
    command = r"""
$ErrorActionPreference = 'Stop'
$session = (Get-Process -Id $PID).SessionId
$rows = @(Get-CimInstance Win32_Process -Filter "Name='acad.exe'" |
  Where-Object SessionId -eq $session | ForEach-Object {
    $p = Get-Process -Id $_.ProcessId
    $version = if ($_.ExecutablePath) {
      (Get-Item -LiteralPath $_.ExecutablePath).VersionInfo.ProductVersion
    } else { '' }
    $civil = $false
    if ($version -match '^R?22\.') {
      $civil = @($p.Modules | Where-Object {
        $_.ModuleName -match '^Aecc' -or $_.FileName -match '[\\/]C3D[\\/]'
      }).Count -gt 0
    }
    [pscustomobject]@{
      pid = $_.ProcessId
      path = $_.ExecutablePath
      command = $_.CommandLine
      version = $version
      title = $p.MainWindowTitle
      responding = $p.Responding
      civil = $civil
    }
  })
ConvertTo-Json -InputObject $rows -Compress
"""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n" + command
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, check=True, timeout=12, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout)


def verified_process(rows, expected_executable, has_profile):
    versions = [VERSION.fullmatch(row["version"] or "") for row in rows]
    if any(not row["path"] or not version for row, version in zip(rows, versions)):
        raise RuntimeError("An AutoCAD process has unverifiable identity. COM connection refused.")
    release = [row for row, version in zip(rows, versions) if version.group(1) == "22"]
    if len(release) != 1:
        raise RuntimeError(
            f"Expected exactly one R22 process in this Windows session, found {len(release)}. "
            "Shared COM registration cannot uniquely identify standalone AutoCAD 2018."
        )
    row = release[0]
    launch = STARTUP.fullmatch(row["command"] or "")
    if (ntpath.normcase(row["path"]) != ntpath.normcase(expected_executable)
            or not launch or not CAPTION.fullmatch(row["title"] or "")
            or not row["responding"] or row["civil"]):
        raise RuntimeError("Executable, explicit standalone launch, window, or readiness could not be verified.")
    lang, profile = launch.groups()
    if not has_profile(lang.lower(), profile):
        raise RuntimeError("The startup profile is absent from standalone AutoCAD 2018 registration.")
    return row["pid"], profile


def has_standalone_product_and_profile(lang, profile, expected):
    localized = f"{PRODUCT_KEY}:{LANGUAGE_IDS[lang]}"
    key = f"{localized}\\Profiles\\{profile}"
    try:
        name = registry_value(winreg.HKEY_LOCAL_MACHINE, localized, "ProductName")
        path = registry_value(winreg.HKEY_LOCAL_MACHINE, localized, "AcadLocation")
        if not PLAIN_PRODUCT.fullmatch(str(name)) or "civil" in str(name).casefold():
            return False
        if ntpath.normcase(ntpath.join(path, "acad.exe")) != ntpath.normcase(expected):
            return False
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
            return True
    except FileNotFoundError:
        return False


def preflight():
    install = registry_value(winreg.HKEY_LOCAL_MACHINE, PRODUCT_KEY, "GlobUPILocation")
    release = str(registry_value(winreg.HKEY_LOCAL_MACHINE, PRODUCT_KEY, "UPIRELEASE"))
    if release != "2018":
        raise RuntimeError("Standalone AutoCAD 2018 registry entry was not verified.")
    expected = str(Path(install) / "acad.exe")
    pid, profile = verified_process(
        process_inventory(), expected,
        lambda lang, profile: has_standalone_product_and_profile(lang, profile, expected),
    )
    return pid, profile, expected


def verified_application():
    pid, profile, expected = preflight()
    if os.environ.get("AUTOCAD2018_ATTACH") != "verified-exclusive":
        raise RuntimeError("Standalone AutoCAD 2018 COM attachment is disabled in this deployment.")
    # GetActiveObject never starts a product. Use only the versioned registration.
    try:
        app = win32com.client.GetActiveObject(PROGID)
    except pywintypes.com_error as error:
        raise RuntimeError("Standalone AutoCAD 2018 did not register an active COM object.") from error
    _, owner = win32process.GetWindowThreadProcessId(app.HWND)
    if (owner != pid or not re.match(r"^22(?:\.|$)", str(app.Version))
            or ntpath.normcase(app.FullName) != ntpath.normcase(expected)
            or str(app.Preferences.Profiles.ActiveProfile).casefold() != profile.casefold()):
        raise RuntimeError("COM window, executable, release or active profile does not match the verified process.")
    return app


def run_server():
    backend_path = Path(os.environ["AUTOCAD2018_BACKEND"]).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("local_autocad_backend", backend_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the existing local AutoCAD MCP backend.")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    backend.app = verified_application
    backend.server.run(transport="stdio")


if __name__ == "__main__":
    run_server()
