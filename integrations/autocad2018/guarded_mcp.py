"""Attach the existing MCP only to a verified, already running standalone AutoCAD."""
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
import time
from typing import NamedTuple
import winreg

import pythoncom
import pywintypes
import win32com.client
import win32gui
import win32process

from drawing_tools import DrawingTools, active_profile

VERSION = re.compile(r"^R?(\d+)\.(\d+)(?:[.\w -]*)$", re.IGNORECASE)
CIVIL_CAPTION = re.compile(r"^(?:Autodesk )?(?:AutoCAD )?Civil 3D\b", re.IGNORECASE)


class Release(NamedTuple):
    year: str
    major: str
    code: str

    @property
    def progid(self):
        return f"AutoCAD.Application.{self.major}"

    @property
    def root(self):
        return rf"SOFTWARE\Autodesk\AutoCAD\R{self.major}.0"

    def caption_matches(self, caption):
        return bool(re.fullmatch(
            rf"(?:Autodesk )?AutoCAD {self.year}(?:\s*[-–]\s*\[.*\])?", caption, re.IGNORECASE,
        ))


RELEASES = {"2021": Release("2021", "24", "ACAD-4101")}


def configured_release():
    year = os.environ.get("AUTOCAD_RELEASE", "2021")
    if year not in RELEASES:
        raise ValueError("AUTOCAD_RELEASE must select standalone AutoCAD 2021 (R24.0).")
    return RELEASES[year]


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
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
$session = (Get-Process -Id $PID).SessionId
$rows = @(Get-CimInstance Win32_Process -Filter "Name='acad.exe'" |
  Where-Object SessionId -eq $session | ForEach-Object {
    $p = Get-Process -Id $_.ProcessId
    $version = if ($_.ExecutablePath) {
      (Get-Item -LiteralPath $_.ExecutablePath).VersionInfo.ProductVersion
    } else { '' }
    $modules = @()
    if ($version -match '^R?(22|24)\.') { $modules = @($p.Modules) }
    $civil = @($modules | Where-Object {
      if ($_.ModuleName -notmatch '^(Aecc|C3D)' -and $_.FileName -notmatch '[\\/]C3D[\\/]') { return $false }
      # Plain R24.0 includes this Autodesk object-enabler module in its own installation.
      if ($version -match '^R?24\.0\.' -and $_.ModuleName -ieq 'aeccore.crx' -and
          $_.FileName -ieq (Join-Path (Split-Path $p.Path) 'aeccore.crx')) {
        $signature = Get-AuthenticodeSignature -LiteralPath $_.FileName
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $hash = [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($_.FileName))).Replace('-', '') }
        finally { $sha.Dispose() }
        return $signature.Status -ne 'Valid' -or
            $signature.SignerCertificate.GetNameInfo('SimpleName', $false) -ne 'Autodesk, Inc.' -or
            $hash -ne '1E72A38524FA2F7B7DE6694B735367492D83A9C78A8B45626C88C35BB04E72A7'
      }
      return $true
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
                row.setdefault("product_windows", []).append({"hwnd": hwnd, "product": "Civil 3D"})
            elif win32gui.IsWindowVisible(hwnd):
                for release in RELEASES.values():
                    if release.caption_matches(caption):
                        row.setdefault("product_windows", []).append({"hwnd": hwnd, "product": f"AutoCAD {release.year}"})
        return True

    win32gui.EnumWindows(collect, None)
    for row in rows:
        candidates = row.get("product_windows", [])
        if len(candidates) == 1 and candidates[0]["product"].startswith("AutoCAD "):
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


def verified_process(rows, expected_executable, policy):
    versions = [VERSION.fullmatch(row["version"] or "") for row in rows]
    if any(not row["path"] or not version for row, version in zip(rows, versions)):
        raise RuntimeError("An AutoCAD process has unverifiable identity. COM connection refused.")
    release = [row for row, version in zip(rows, versions) if version.group(1) == policy.major]
    if not release:
        observed = ", ".join(sorted({row["version"] for row in rows})) or "none"
        raise RuntimeError(f"No running AutoCAD {policy.year} (R{policy.major}) in this Windows session. Observed releases: {observed}.")
    if len(release) > 1:
        raise RuntimeError(
            f"Expected exactly one R{policy.major} process in this Windows session, found {len(release)}. "
            "Shared COM registration cannot uniquely identify standalone AutoCAD."
        )
    row = release[0]
    if not row["modules_verified"] or row["civil"]:
        raise RuntimeError("Target module inventory is unavailable or contains Civil 3D modules.")
    if VERSION.fullmatch(row["version"]).group(2) != "0":
        raise RuntimeError(f"Expected exactly release R{policy.major}.0 for AutoCAD {policy.year}.")
    if (not same_path(row["path"], expected_executable)
            or not policy.caption_matches(row["title"] or "") or not row["responding"]
            or not row["hwnd"]):
        raise RuntimeError("The standalone executable, application window or readiness could not be verified.")
    argv = windows_args(row["command"])
    if not same_path(argv[0], row["path"]):
        raise RuntimeError("Process command line executable differs from its actual image.")
    return row, startup_switches(argv)


def standalone_profiles(expected, policy, language=None):
    root = policy.root
    profiles = set()
    products = []
    for name in registry_children(winreg.HKEY_LOCAL_MACHINE, root):
        if not re.fullmatch(rf"{policy.code}:[0-9a-f]{{3,4}}", name, re.IGNORECASE):
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
            if (not re.fullmatch(rf"(?:Autodesk )?AutoCAD {policy.year}(?:\s*[—–-]\s*.+)?", str(title), re.IGNORECASE)
                    or "civil" in str(title).casefold()
                    or not same_path(ntpath.join(path, "acad.exe"), expected)):
                continue
            products.append(name)
            profiles.update(registry_children(winreg.HKEY_CURRENT_USER, f"{localized}\\Profiles"))
        except FileNotFoundError:
            continue
    return products, profiles


def com_registration(expected, policy):
    clsid = registry_value(winreg.HKEY_CLASSES_ROOT, f"{policy.progid}\\CLSID", "")
    command = registry_value(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{clsid}\\LocalServer32", "")
    # Autodesk can register an unquoted executable path containing spaces.
    match = re.fullmatch(r'\s*"?(.+?\.exe)"?\s+/Automation\s*', command, re.IGNORECASE)
    if not match or not same_path(match.group(1), expected):
        raise RuntimeError("Versioned COM LocalServer32 does not point to the verified executable.")
    return {"progid": policy.progid, "clsid": clsid, "local_server": match.group(1)}


def installed_executable(policy):
    key = f"{policy.root}\\{policy.code}"
    install = registry_value(winreg.HKEY_LOCAL_MACHINE, key, "GlobUPILocation")
    release = str(registry_value(winreg.HKEY_LOCAL_MACHINE, key, "UPIRELEASE"))
    if release != policy.year:
        raise RuntimeError("Standalone AutoCAD registry entry was not verified.")
    return str(Path(install) / "acad.exe")


def preflight(policy, rows=None):
    expected = installed_executable(policy)
    row, flags = verified_process(process_inventory() if rows is None else rows, expected, policy)
    if win32process.GetWindowThreadProcessId(row["hwnd"])[1] != row["pid"]:
        raise RuntimeError("The standalone window no longer belongs to the observed process.")
    products, profiles = standalone_profiles(expected, policy, flags.get("language"))
    if not products or not profiles:
        raise RuntimeError("Localized standalone product registration or its user profiles are absent.")
    registered = {profile.casefold() for profile in profiles}
    if flags.get("p") and flags["p"].casefold() not in registered:
        raise RuntimeError("The startup profile is absent from standalone AutoCAD registration.")
    return row, flags.get("p"), registered, com_registration(expected, policy)


def diagnose_autocad() -> dict:
    """Report process evidence and the preflight verdict without making a COM call."""
    policy = configured_release()
    rows = process_inventory()
    result = {
        "windows": [
            {**{key: row[key] for key in ("pid", "hwnd", "session", "version", "civil", "modules_verified")},
             "product_windows": row.get("product_windows", [])}
            for row in rows
        ],
        "progid": policy.progid,
        "target_release": policy.year,
        "attach_enabled": os.environ.get("AUTOCAD_ATTACH") == "verified-exclusive",
    }
    try:
        result["registry_only"] = com_registration(installed_executable(policy), policy)
    except (RuntimeError, OSError) as error:
        result["registration_error"] = str(error)
    try:
        row, profile, _, registration = preflight(policy, rows)
        result.update(preflight="passed", target_pid=row["pid"], target_hwnd=row["hwnd"],
                      startup_profile=profile, registration=registration)
    except (RuntimeError, OSError) as error:
        result.update(preflight="refused", reason=str(error))
    return result


def verified_application():
    # Reacquire and revalidate only read-only identity. Never replay a drawing mutation.
    for attempt in range(3):
        try:
            return verified_application_once()
        except (AttributeError, pywintypes.com_error) as error:
            if isinstance(error, pywintypes.com_error) and error.hresult not in {-2147418111, -2147417846}:
                raise
            if attempt == 2:
                raise RuntimeError("Could not read verified AutoCAD COM identity after 3 attempts.") from error
            pythoncom.PumpWaitingMessages()
            time.sleep(0.25)


def verified_application_once():
    policy = configured_release()
    row, launch_profile, profiles, _ = preflight(policy)
    if os.environ.get("AUTOCAD_ATTACH") != "verified-exclusive":
        raise RuntimeError("Standalone AutoCAD COM attachment is disabled in this deployment.")
    try:
        app = win32com.client.GetActiveObject(policy.progid)
    except pywintypes.com_error as error:
        raise RuntimeError("Standalone AutoCAD did not register an active COM object.") from error
    hwnd = app.HWND
    if (hwnd != row["hwnd"] or win32process.GetWindowThreadProcessId(hwnd)[1] != row["pid"]
            or not policy.caption_matches(win32gui.GetWindowText(hwnd))):
        raise RuntimeError("COM window does not match the verified standalone process.")
    if not re.match(rf"^{policy.major}\.0(?:[.s\s]|$)", str(app.Version)) or not same_path(app.FullName, row["path"]):
        raise RuntimeError("COM executable or release does not match standalone registration.")
    if not app.GetAcadState().IsQuiescent:
        raise RuntimeError("AutoCAD is waiting for an active command or dialog.")
    profile = active_profile(app)
    if (profile.casefold() not in profiles
            or (launch_profile and profile.casefold() != launch_profile.casefold())):
        raise RuntimeError("COM active profile does not match standalone registration.")
    return app


def load_backend():
    backend_path = Path(os.environ["AUTOCAD_BACKEND"]).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("local_autocad_backend", backend_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the existing local AutoCAD MCP backend.")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    backend.app = verified_application
    backend.server.add_tool(diagnose_autocad)
    DrawingTools(backend, f"Autodesk AutoCAD {configured_release().year}").register()
    return backend


if __name__ == "__main__":
    load_backend().server.run(transport="stdio")
