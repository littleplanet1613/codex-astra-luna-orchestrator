"""Regression tests for interpreting product evidence without contacting CAD."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import guarded_mcp as guard

EXE = r"C:\Program Files\Autodesk\AutoCAD 2018\acad.exe"
PLAIN = dict(pid=101, session=1, hwnd=1001, path=EXE, version="R22.0.107.0.0",
             command=f'"{EXE}" /product ACAD /language "ru-RU" /p "<<Unnamed Profile>>"',
             title="Autodesk AutoCAD 2018 - [test.dwg]", responding=True, civil=False,
             modules_verified=True)
NEWER = dict(pid=202, path=r"C:\Program Files\Autodesk\AutoCAD 2021\acad.exe", version="R24.0")


class GuardTests(unittest.TestCase):
    def check(self, *rows):
        return guard.verified_process(rows, EXE)

    def test_explicit_and_default_shortcuts(self):
        for tail in ('/product ACAD /language "ru-RU" /p "Plan profile"',
                     '/p "Plan profile" /nologo /language ru-RU /product "ACAD"',
                     '/nologo /language ru-RU', '', '"D:\\some folder\\test.dwg"'):
            with self.subTest(tail=tail):
                row, flags = self.check({**PLAIN, "command": f'"{EXE}" {tail}'}, NEWER)
                self.assertEqual(row["hwnd"], 1001)
                self.assertNotEqual(flags.get("product"), "C3D")

    def test_rejects_two_r22_even_if_first_appears_standalone(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one R22"):
            self.check(PLAIN, {**PLAIN, "pid": 102, "civil": True})

    def test_rejects_civil_and_absent_module_inventory(self):
        for update in ({"civil": True}, {"modules_verified": False}):
            with self.assertRaisesRegex(RuntimeError, "module inventory"):
                self.check({**PLAIN, **update})

    def test_rejects_wrong_title_window_and_unresponsive(self):
        for update in ({"title": "AutoCAD Civil 3D 2018"}, {"title": ""}, {"hwnd": 0},
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
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            self.check(NEWER)
        with self.assertRaisesRegex(RuntimeError, "unverifiable"):
            self.check(PLAIN, {**NEWER, "version": "unknown"})

    def test_preflight_refuses_missing_standalone_registered_profiles_before_com(self):
        with (patch.object(guard, "registry_value", side_effect=[r"C:\Program Files\Autodesk\AutoCAD 2018", "2018"]),
              patch.object(guard, "process_inventory", return_value=[PLAIN]),
              patch.object(guard.win32process, "GetWindowThreadProcessId", return_value=(1, PLAIN["pid"])),
              patch.object(guard, "standalone_profiles", return_value=([], set())),
              patch.object(guard.win32com.client, "GetActiveObject") as com):
            with self.assertRaisesRegex(RuntimeError, "registration or its user profiles are absent"):
                guard.verified_application()
            com.assert_not_called()

    def test_com_is_disabled_even_with_a_valid_preflight(self):
        with (patch.object(guard, "preflight", return_value=(PLAIN, None, {"plain"}, {})),
              patch.dict(os.environ, {"AUTOCAD2018_ATTACH": ""}),
              patch.object(guard.win32com.client, "GetActiveObject") as com):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                guard.verified_application()
            com.assert_not_called()

    def test_com_owner_and_profile_must_match(self):
        app = SimpleNamespace(HWND=PLAIN["hwnd"], Version="22.0s", FullName=EXE,
                              Preferences=SimpleNamespace(Profiles=SimpleNamespace(ActiveProfile="Plain")))
        with (patch.object(guard, "preflight", return_value=(PLAIN, None, {"plain"}, {})),
              patch.dict(os.environ, {"AUTOCAD2018_ATTACH": "verified-exclusive"}),
              patch.object(guard.win32com.client, "GetActiveObject", return_value=app) as com,
              patch.object(guard.win32gui, "GetWindowText", return_value=PLAIN["title"]),
              patch.object(guard.win32process, "GetWindowThreadProcessId", return_value=(1, 900)) as owner):
            with self.assertRaisesRegex(RuntimeError, "COM window"):
                guard.verified_application()
            owner.return_value = (1, PLAIN["pid"])
            app.Preferences.Profiles.ActiveProfile = "C3D"
            with self.assertRaisesRegex(RuntimeError, "profile"):
                guard.verified_application()
            app.Preferences.Profiles.ActiveProfile = "Plain"
            self.assertIs(guard.verified_application(), app)
            com.assert_called_with(guard.PROGID)

    def test_progid_registration_path_is_checked(self):
        with patch.object(guard, "registry_value", side_effect=["{example}", f"{EXE} /Automation"]):
            self.assertEqual(guard.com_registration(EXE)["local_server"], EXE)
        with patch.object(guard, "registry_value", side_effect=["{example}", 'D:\\acad.exe /Automation']):
            with self.assertRaisesRegex(RuntimeError, "LocalServer32"):
                guard.com_registration(EXE)


if __name__ == "__main__":
    unittest.main()
