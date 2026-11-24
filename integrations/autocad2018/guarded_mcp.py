"""Attach the existing MCP only to a verified, already running AutoCAD 2018."""
import ctypes
from ctypes import wintypes
import importlib.util
import json
import locale
import ntpath
import os
from pathlib import Path
import re
import subprocess
import winreg

import pywintypes
import win32com.client
import win32gui
import win32process

PROGID = "AutoCAD.Application.22"
PRODUCT_KEY = r"SOFTWARE\Autodesk\AutoCAD\R22.0\ACAD-1001"
CAPTION = re.compile(r"^(?:Autodesk )?AutoCAD 2018(?:\s*[-–]\s*\[.*\])?$", re.IGNORECASE)
VERSION = re.compile(r"^R?(\d+)\.\d+(?:[.\w -]*)$", re.IGNORECASE)
PLAIN_PRODUCT = re.compile(r"^(?:Autodesk )?AutoCAD 2018(?:\s*[—–-]\s*.+)?$", re.IGNORECASE)
CIVIL_CAPTION = re.compile(r"^(?:Autodesk )?(?:AutoCAD )?Civil 3D 2018\b", re.IGNORECASE)
LOCALE_KEY = re.compile(r"^ACAD-1001:[0-9a-f]{3,4}$", re.IGNORECASE)


def registry_value(hive, key, value):
    with winreg.OpenKey(hive, key) as registry_key:
        return winreg.QueryValueEx(registry_key, value)[0]


def registry_children(hive, key):
    with winreg.OpenKey(hive, key) as handle:
        count = winreg.QueryInfoKey(handle)[0]
        return [winreg.EnumKey(handle, index) for index in range(count)]


def process_inventory():
    command = r"""
$ErrorActionPreference = 'Stop'
$session = (Get-Process -Id $PID).SessionId
$rows = @(Get-CimInstance Win32_Process -Filter "Name='acad.exe'" |
  Where-Object SessionId -eq $session | ForEach-Object {
    $p = Get-Process -Id $_.ProcessId
    $version = if ($_.ExecutablePath) {
      (Get-Item -LiteralPath $_.ExecutablePath).VersionInfo.ProductVersion
    } else { '' }
    $modules = @()
    if ($version -match '^R?22\.') { $modules = @($p.Modules) }
    $civil = @($modules | Where-Object {
      $_.ModuleName -match '^(Aecc|C3DBrand)' -or $_.FileName -match '[\\/]C3D[\\/]'
    }).Count -gt 0
    [pscustomobject]@{
      pid = $_.ProcessId
      session = $_.SessionId
      hwnd = $p.MainWindowHandle.ToInt64()
      path = $_.ExecutablePath
      command = $_.CommandLine
      version = $version
      title = $p.MainWindowTitle
      responding = $p.Responding
      modules_verified = $modules.Count -gt 0
      civil = $civil
    }
  })
ConvertTo-Json -InputObject $rows -Compress
"""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n" + command],
        capture_output=True, check=True, timeout=12, encoding="utf-8-sig",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    rows = json.loads(result.stdout)
    by_pid = {row["pid"]: row for row in rows}

    def collect(hwnd, _):
        row = by_pid.get(win32process.GetWindowThreadProcessId(hwnd)[1])
        if row is not None:
            caption = win32gui.GetWindowText(hwnd)
            if CIVIL_CAPTION.match(caption):
                row["civil"] = True
                row.setdefault("product_windows", []).append({"hwnd": hwnd, "product": "Civil 3D 2018"})
            elif CAPTION.fullmatch(caption) and win32gui.IsWindowVisible(hwnd):
                row.setdefault("product_windows", []).append({"hwnd": hwnd, "product": "AutoCAD 2018"})
        return True

    win32gui.EnumWindows(collect, None)
    for row in rows:
        candidates = row.get("product_windows", [])
        if len(candidates) == 1 and candidates[0]["product"] == "AutoCAD 2018":
            row["hwnd"] = candidates[0]["hwnd"]
            row["title"] = win32gui.GetWindowText(row["hwnd"])
    return rows


def windows_args(command):
    if not command or not command.strip():
        raise RuntimeError("Process command line could not be read.")
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.windll.kernel32.LocalFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = split(command, ctypes.byref(count))
    if not pointer:
        raise ctypes.WinError()
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        free(pointer)


def startup_switches(argv):
    """Treat startup flags as consistency evidence; Windows shortcuts can omit them."""
    flags = {}
    args = iter(argv[1:])
    for item in args:
        flag = item.casefold()
        if flag in {"/ld", "-ld"}:
            raise RuntimeError("Explicit module loading requires separate product verification.")
        if flag not in {"/product", "/language", "/p", "-product", "-language", "-p"}:
            continue
        key = flag[1:]
        value = next(args, "")
        if key in flags or not value.strip() or value.startswith(("/", "-")):
            raise RuntimeError("Duplicate or invalid AutoCAD identity switch.")
        flags[key] = value
    if "product" in flags and flags["product"].casefold() != "acad":
        raise RuntimeError("Explicit launch product is not standalone AutoCAD.")
    if "p" in flags and re.search(r"civil|c3d|[\\/]", flags["p"], re.IGNORECASE):
        raise RuntimeError("The startup profile explicitly identifies a vertical or external profile.")
    return flags


def same_path(left, right):
    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right))


def verified_process(rows, expected_executable):
    versions = [VERSION.fullmatch(row["version"] or "") for row in rows]
    if any(not row["path"] or not version for row, version in zip(rows, versions)):
        raise RuntimeError("An AutoCAD process has unverifiable identity. COM connection refused.")
    release = [row for row, version in zip(rows, versions) if version.group(1) == "22"]
    if not release:
        observed = ", ".join(sorted({row["version"] for row in rows})) or "none"
        raise RuntimeError(f"No running AutoCAD 2018 (R22) in this Windows session. Observed releases: {observed}.")
    if len(release) > 1:
        raise RuntimeError(
            f"Expected exactly one R22 process in this Windows session, found {len(release)}. "
            "Shared COM registration cannot uniquely identify standalone AutoCAD 2018."
        )
    row = release[0]
    if not row["modules_verified"] or row["civil"]:
        raise RuntimeError("R22 module inventory is unavailable or contains Civil 3D modules.")
    if (not same_path(row["path"], expected_executable)
            or not CAPTION.fullmatch(row["title"] or "") or not row["responding"]
            or not row["hwnd"]):
        raise RuntimeError("The standalone executable, application window or readiness could not be verified.")
    argv = windows_args(row["command"])
    if not same_path(argv[0], row["path"]):
        raise RuntimeError("Process command line executable differs from its actual image.")
    return row, startup_switches(argv)


def standalone_profiles(expected, language=None):
    root = ntpath.dirname(PRODUCT_KEY)
    profiles = set()
    products = []
    for name in registry_children(winreg.HKEY_LOCAL_MACHINE, root):
        if not LOCALE_KEY.fullmatch(name):
            continue
        if language is not None:
            lcid = int(name.rsplit(":", 1)[1], 16)
            registered_language = locale.windows_locale.get(lcid, "").replace("_", "-").casefold()
            if registered_language != language.casefold():
                continue
        localized = f"{root}\\{name}"
        try:
            title = registry_value(winreg.HKEY_LOCAL_MACHINE, localized, "ProductName")
            path = registry_value(winreg.HKEY_LOCAL_MACHINE, localized, "AcadLocation")
            if (not PLAIN_PRODUCT.fullmatch(str(title)) or "civil" in str(title).casefold()
                    or not same_path(ntpath.join(path, "acad.exe"), expected)):
                continue
            products.append(name)
            profiles.update(registry_children(winreg.HKEY_CURRENT_USER, f"{localized}\\Profiles"))
        except FileNotFoundError:
            continue
    return products, profiles


def com_registration(expected):
    clsid = registry_value(winreg.HKEY_CLASSES_ROOT, f"{PROGID}\\CLSID", "")
    command = registry_value(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{clsid}\\LocalServer32", "")
    # Autodesk can register an unquoted executable path containing spaces.
    match = re.fullmatch(r'\s*"?(.+?\.exe)"?\s+/Automation\s*', command, re.IGNORECASE)
    if not match or not same_path(match.group(1), expected):
        raise RuntimeError("Versioned COM LocalServer32 does not point to the verified R22 executable.")
    return {"progid": PROGID, "clsid": clsid, "local_server": match.group(1)}


def installed_executable():
    install = registry_value(winreg.HKEY_LOCAL_MACHINE, PRODUCT_KEY, "GlobUPILocation")
    release = str(registry_value(winreg.HKEY_LOCAL_MACHINE, PRODUCT_KEY, "UPIRELEASE"))
    if release != "2018":
        raise RuntimeError("Standalone AutoCAD 2018 registry entry was not verified.")
    return str(Path(install) / "acad.exe")


def preflight(rows=None):
    expected = installed_executable()
    row, flags = verified_process(process_inventory() if rows is None else rows, expected)
    if win32process.GetWindowThreadProcessId(row["hwnd"])[1] != row["pid"]:
        raise RuntimeError("The standalone window no longer belongs to the observed process.")
    products, profiles = standalone_profiles(expected, flags.get("language"))
    if not products or not profiles:
        raise RuntimeError("Localized standalone product registration or its user profiles are absent.")
    registered = {profile.casefold() for profile in profiles}
    if flags.get("p") and flags["p"].casefold() not in registered:
        raise RuntimeError("The startup profile is absent from standalone AutoCAD 2018 registration.")
    return row, flags.get("p"), registered, com_registration(expected)


def diagnose_autocad() -> dict:
    """Report actual R22 process evidence and the preflight verdict without making a COM call."""
    rows = process_inventory()
    result = {
        "windows": [
            {**{key: row[key] for key in ("pid", "hwnd", "session", "version", "civil", "modules_verified")},
             "product_windows": row.get("product_windows", [])}
            for row in rows
        ],
        "progid": PROGID,
        "attach_enabled": os.environ.get("AUTOCAD2018_ATTACH") == "verified-exclusive",
    }
    try:
        result["registry_only"] = com_registration(installed_executable())
    except (RuntimeError, OSError) as error:
        result["registration_error"] = str(error)
    try:
        row, profile, _, registration = preflight(rows)
        result.update(preflight="passed", target_pid=row["pid"], target_hwnd=row["hwnd"],
                      startup_profile=profile, registration=registration)
    except (RuntimeError, OSError) as error:
        result.update(preflight="refused", reason=str(error))
    return result


def verified_application():
    row, launch_profile, profiles, _ = preflight()
    if os.environ.get("AUTOCAD2018_ATTACH") != "verified-exclusive":
        raise RuntimeError("Standalone AutoCAD 2018 COM attachment is disabled in this deployment.")
    try:
        app = win32com.client.GetActiveObject(PROGID)
    except pywintypes.com_error as error:
        raise RuntimeError("Standalone AutoCAD 2018 did not register an active COM object.") from error
    hwnd = app.HWND
    if (hwnd != row["hwnd"] or win32process.GetWindowThreadProcessId(hwnd)[1] != row["pid"]
            or not CAPTION.fullmatch(win32gui.GetWindowText(hwnd))):
        raise RuntimeError("COM window does not match the verified standalone process.")
    profile = str(app.Preferences.Profiles.ActiveProfile)
    if (not re.match(r"^22(?:\.|$)", str(app.Version)) or not same_path(app.FullName, row["path"])
            or profile.casefold() not in profiles
            or (launch_profile and profile.casefold() != launch_profile.casefold())):
        raise RuntimeError("COM executable, release or active profile does not match standalone registration.")
    return app


def load_backend():
    from drawing_tools import DrawingTools

    backend_path = Path(os.environ["AUTOCAD2018_BACKEND"]).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("local_autocad_backend", backend_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the existing local AutoCAD MCP backend.")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    backend.app = verified_application
    backend.server.add_tool(diagnose_autocad)
    DrawingTools(backend).register()
    return backend


if __name__ == "__main__":
    load_backend().server.run(transport="stdio")
