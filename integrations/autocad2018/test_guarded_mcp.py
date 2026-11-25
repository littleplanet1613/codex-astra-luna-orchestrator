"""Regression tests for interpreting product evidence without contacting CAD."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import guarded_mcp as guard

EXE = r"C:\Program Files\Autodesk\AutoCAD 2021\acad.exe"
PLAIN = dict(pid=101, session=1, hwnd=1001, path=EXE, version="R24.0.107.0.0",
             command=f'"{EXE}" /product ACAD /language "ru-RU" /p "<<Unnamed Profile>>"',
             title="Autodesk AutoCAD 2021 - [test.dwg]", responding=True, civil=False,
             modules_verified=True)
NEWER = dict(pid=202, path=r"C:\Program Files\Autodesk\AutoCAD 2021\acad.exe", version="R25.0")
R24 = guard.RELEASES["2021"]
R24 = guard.RELEASES["2021"]


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"AUTOCAD_RELEASE": "2021"}))

    def check(self, *rows):
        return guard.verified_process(rows, EXE, R24)

    def test_2021_is_default_and_exact_24_0_is_required(self):
        with patch.dict(os.environ, clear=True):
            self.assertEqual(guard.configured_release(), R24)
        executable = NEWER["path"]
        row = {**PLAIN, **NEWER, "title": "Autodesk AutoCAD 2021 - [test.dwg]", "version": "R24.0",
               "command": f'"{executable}"'}
        self.assertEqual(guard.verified_process([row], executable, R24)[0]["pid"], NEWER["pid"])
        with self.assertRaisesRegex(RuntimeError, "R24.0"):
            guard.verified_process([{**row, "version": "R24.1.0"}], executable, R24)
        with self.assertRaisesRegex(RuntimeError, "Civil 3D"):
            guard.verified_process([{**row, "civil": True}], executable, R24)
        with self.assertRaisesRegex(RuntimeError, "exactly one R24"):
            guard.verified_process([row, {**row, "pid": 999, "civil": True}], executable, R24)

    def test_explicit_and_default_shortcuts(self):
        for tail in ('/product ACAD /language "ru-RU" /p "Plan profile"',
                     '/p "Plan profile" /nologo /language ru-RU /product "ACAD"',
                     '/nologo /language ru-RU', '', '"D:\\some folder\\test.dwg"'):
            with self.subTest(tail=tail):
                row, flags = self.check({**PLAIN, "command": f'"{EXE}" {tail}'}, NEWER)
                self.assertEqual(row["hwnd"], 1001)
                self.assertNotEqual(flags.get("product"), "C3D")

    def test_rejects_two_r22_even_if_first_appears_standalone(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one R24"):
            self.check(PLAIN, {**PLAIN, "pid": 102, "civil": True})

    def test_rejects_civil_and_absent_module_inventory(self):
        for update in ({"civil": True}, {"modules_verified": False}):
            with self.assertRaisesRegex(RuntimeError, "module inventory"):
                self.check({**PLAIN, **update})

    def test_rejects_wrong_title_window_and_unresponsive(self):
        for update in ({"title": "AutoCAD Civil 3D 2021"}, {"title": ""}, {"hwnd": 0},
                       {"responding": False}):
            with self.assertRaisesRegex(RuntimeError, "window or readiness"):
                self.check({**PLAIN, **update})

    def test_rejects_mismatched_or_ambiguous_switches(self):
        tails = ('/product C3D /p AutoCAD', '/p Civil3D', '/p C3D_Imperial',
                 '/p "C:\\temp\\profile.arg"', '/product ACAD /product ACAD',
                 '/product /nologo', '/ld C3D.dll')
        for tail in tails:
            with self.subTest(tail=tail), self.assertRaises(RuntimeError):
                self.check({**PLAIN, "command": f'"{EXE}" {tail}'})
        with self.assertRaisesRegex(RuntimeError, "differs"):
            self.check({**PLAIN, "command": '"D:\\acad.exe" /product ACAD'})
        with self.assertRaisesRegex(RuntimeError, "could not be read"):
            self.check({**PLAIN, "command": None})

    def test_rejects_wrong_or_unknown_release(self):
        with self.assertRaisesRegex(RuntimeError, "No running AutoCAD 2021 .*Observed releases: R25.0"):
            self.check(NEWER)
        with self.assertRaisesRegex(RuntimeError, "unverifiable"):
            self.check(PLAIN, {**NEWER, "version": "unknown"})

    def test_preflight_refuses_missing_standalone_registered_profiles_before_com(self):
        with (patch.object(guard, "registry_value", side_effect=[r"C:\Program Files\Autodesk\AutoCAD 2021", "2021"]),
              patch.object(guard, "process_inventory", return_value=[PLAIN]),
              patch.object(guard.win32process, "GetWindowThreadProcessId", return_value=(1, PLAIN["pid"])),
              patch.object(guard, "standalone_profiles", return_value=([], set())),
              patch.object(guard.win32com.client, "GetActiveObject") as com):
            with self.assertRaisesRegex(RuntimeError, "registration or its user profiles are absent"):
                guard.verified_application()
            com.assert_not_called()

    def test_com_is_disabled_even_with_a_valid_preflight(self):
        with (patch.object(guard, "preflight", return_value=(PLAIN, None, {"plain"}, {})),
              patch.dict(os.environ, {"AUTOCAD_ATTACH": ""}),
              patch.object(guard.win32com.client, "GetActiveObject") as com):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                guard.verified_application()
            com.assert_not_called()

    def test_com_owner_and_profile_must_match(self):
        document = SimpleNamespace(GetVariable=lambda _: document.profile, profile="Plain")
        app = SimpleNamespace(HWND=PLAIN["hwnd"], Version="24.0s", FullName=EXE, ActiveDocument=document,
                              Documents=SimpleNamespace(Count=1),
                              GetAcadState=lambda: SimpleNamespace(IsQuiescent=True))
        with (patch.object(guard, "preflight", return_value=(PLAIN, None, {"plain"}, {})),
              patch.dict(os.environ, {"AUTOCAD_ATTACH": "verified-exclusive"}),
              patch.object(guard.win32com.client, "GetActiveObject", return_value=app) as com,
              patch.object(guard.win32gui, "GetWindowText", return_value=PLAIN["title"]),
              patch.object(guard.win32process, "GetWindowThreadProcessId", return_value=(1, 900)) as owner):
            with self.assertRaisesRegex(RuntimeError, "COM window"):
                guard.verified_application()
            owner.return_value = (1, PLAIN["pid"])
            document.profile = "C3D"
            with self.assertRaisesRegex(RuntimeError, "profile"):
                guard.verified_application()
            document.profile = "Plain"
            self.assertIs(guard.verified_application(), app)
            com.assert_called_with(R24.progid)

    def test_progid_registration_path_is_checked(self):
        with patch.object(guard, "registry_value", side_effect=["{example}", f"{EXE} /Automation"]):
            self.assertEqual(guard.com_registration(EXE, R24)["local_server"], EXE)
        with patch.object(guard, "registry_value", side_effect=["{example}", 'D:\\acad.exe /Automation']):
            with self.assertRaisesRegex(RuntimeError, "LocalServer32"):
                guard.com_registration(EXE, R24)


if __name__ == "__main__":
    unittest.main()
