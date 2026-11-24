"""Offline safety tests. No Autodesk COM connection is made."""
import unittest

from guarded_mcp import verified_process

EXE = r"C:\Program Files\Autodesk\AutoCAD 2018\acad.exe"
PLAIN = dict(pid=101, path=EXE, version="R22.0.107.0.0",
             command=f'"{EXE}" /product ACAD /language "ru-RU" /p "<<Unnamed Profile>>"',
             title="Autodesk AutoCAD 2018 - [test.dwg]", responding=True, civil=False)


class GuardTests(unittest.TestCase):
    def check(self, *rows, profile=True):
        return verified_process(rows, EXE, lambda lang, name: profile)

    def test_accepts_explicit_plain_launch(self):
        self.assertEqual(self.check(PLAIN), (101, "<<Unnamed Profile>>"))

    def test_rejects_two_r22_processes(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one R22"):
            self.check(PLAIN, {**PLAIN, "pid": 102, "title": "AutoCAD Civil 3D 2018"})

    def test_rejects_missing_profile_and_unresponsive_process(self):
        with self.assertRaisesRegex(RuntimeError, "profile is absent"):
            self.check(PLAIN, profile=False)
        with self.assertRaisesRegex(RuntimeError, "readiness"):
            self.check({**PLAIN, "responding": False})

    def test_rejects_civil_even_with_autocad_like_profile(self):
        with self.assertRaisesRegex(RuntimeError, "could not be verified"):
            self.check({**PLAIN, "command": f'"{EXE}" /product C3D /language ru-RU /p "AutoCAD"'})

    def test_rejects_missing_command_or_title(self):
        for update in ({"command": f'"{EXE}"'}, {"title": ""}):
            with self.subTest(update=update):
                with self.assertRaisesRegex(RuntimeError, "could not be verified"):
                    self.check({**PLAIN, **update})

    def test_rejects_2021_only_and_ignores_known_other_release(self):
        newer = dict(pid=202, path=r"C:\Program Files\Autodesk\AutoCAD 2021\acad.exe", version="R24.0")
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            self.check(newer)
        self.assertEqual(self.check(PLAIN, newer)[0], 101)

    def test_rejects_unknown_release_and_civil_modules(self):
        with self.assertRaisesRegex(RuntimeError, "unverifiable"):
            self.check(PLAIN, {**PLAIN, "pid": 102, "version": "unknown"})
        with self.assertRaisesRegex(RuntimeError, "could not be verified"):
            self.check({**PLAIN, "civil": True})


if __name__ == "__main__":
    unittest.main()
