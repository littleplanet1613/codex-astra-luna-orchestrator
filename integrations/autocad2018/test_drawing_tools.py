"""Working-copy and plotting regressions without a live Autodesk application."""
from contextlib import nullcontext
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from drawing_tools import DrawingTools, add_mleader, cad_name


class DrawingTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.original = self.root / "original.dwg"
        self.original.write_bytes(b"original placeholder")
        self.dwg = self.work / "acceptance.dwg"
        self.dwg.write_bytes(b"work placeholder")
        self.enterContext(patch.dict(os.environ, {"AUTOCAD2018_WORKSPACE": str(self.work)}))
        self.backend = SimpleNamespace(document=Mock(), undo=Mock(return_value=nullcontext()))
        self.tools = DrawingTools(self.backend, "Autodesk AutoCAD 2021")
        self.tools.record_managed(self.dwg)

    def test_excludes_original_and_aliases_before_com(self):
        for path in (self.original, self.work / ".." / "original.dwg", Path("relative.dwg")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.tools.document(str(path), True)
        self.backend.document.assert_not_called()

    def test_refuses_hardlink_to_original(self):
        alias = self.work / "hardlink.dwg"
        os.link(self.original, alias)
        with self.assertRaisesRegex(ValueError, "hard-linked"):
            self.tools.document(str(alias), True)
        self.backend.document.assert_not_called()

    def test_refuses_unmanaged_or_replaced_dwg_inside_work_directory(self):
        original_in_run = self.work / "unknown.dwg"
        original_in_run.write_bytes(b"unknown prior drawing")
        with self.assertRaisesRegex(ValueError, "not a managed copy"):
            self.tools.document(str(original_in_run), True)
        original_in_run.replace(self.dwg)
        with self.assertRaisesRegex(ValueError, "not a managed copy"):
            self.tools.document(str(self.dwg), True)
        self.backend.document.assert_not_called()

    def test_mleader_out_index_is_not_returned_as_entity(self):
        entity = object()
        ole = Mock()
        ole.InvokeTypes.return_value = (entity, 13)
        with patch("drawing_tools.win32com.client.Dispatch", side_effect=lambda item: item) as dispatch:
            self.assertIs(add_mleader(SimpleNamespace(_oleobj_=ole), [0, 0, 0, 1, 1, 0]), entity)
            dispatch.assert_called_once_with(entity)
        self.assertEqual(ole.InvokeTypes.call_args.args[0], 1605)
        self.assertEqual(len(ole.InvokeTypes.call_args.args), 6)

    def test_path_must_be_absolute_and_new_pdf_must_not_exist(self):
        with self.assertRaises(ValueError):
            self.tools.working_path("relative.pdf", ".pdf", new=True)
        self.assertEqual(self.tools.working_path(str(self.work / "report.pdf"), ".pdf", new=True),
                         self.work / "report.pdf")
        path = self.work / "existing.pdf"
        path.touch()
        with self.assertRaises(FileExistsError):
            self.tools.working_path(str(path), ".pdf", new=True)

    def test_work_copy_preserves_original_and_never_opens_original(self):
        copy = self.work / "copy.dwg"
        opener = Mock(return_value=SimpleNamespace(FullName=str(copy), Saved=True))
        app = SimpleNamespace(Documents=SimpleNamespace(Open=opener))
        with (patch.object(self.tools, "app_for_document_switch", return_value=app),
              patch.object(self.tools, "ready_document", side_effect=lambda _, doc: doc)):
            answer = self.tools.open_working_copy(str(self.original), str(copy))
            self.assertEqual(answer["path"], str(copy))
            self.assertEqual(copy.read_bytes(), b"original placeholder")
            opener.assert_called_once_with(str(copy), False)
            with self.assertRaises(FileExistsError):
                self.tools.open_working_copy(str(self.original), str(copy))
        self.assertEqual(self.original.read_bytes(), b"original placeholder")

    def test_save_as_refuses_unscoped_destination(self):
        forbidden = self.root / "outside.dwg"
        with self.assertRaises(ValueError):
            self.tools.save_copy(str(self.dwg), str(forbidden))
        self.backend.document.assert_not_called()

    def test_activate_returns_state_from_verified_cad_app(self):
        self.backend.norm = os.path.normcase
        doc = SimpleNamespace(Path=str(self.work), FullName=str(self.dwg), Activate=Mock())
        app = SimpleNamespace(Documents=[doc])
        state = {"pid": 101, "hwnd": 1001, "path": str(self.dwg)}
        with (patch.object(self.tools, "app_for_document_switch", return_value=app),
              patch.object(self.tools, "drawing_state", return_value=state) as read_state):
            self.assertEqual(self.tools.activate_drawing(str(self.dwg)), state)
            doc.Activate.assert_called_once_with()
            read_state.assert_called_once_with(app)

    def test_local_names_cannot_be_dwg_paths(self):
        self.assertEqual(cad_name("Survey point 1"), "Survey point 1")
        for name in ("", "*XREF", "D:\\a.dwg", "../file", "a|b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                cad_name(name)

    def plotting_objects(self):
        sheet = SimpleNamespace(Name="Plan", ModelType=False, ConfigName="DWG To PDF.pc3", PlotType=5,
                                GetPlotDeviceNames=lambda: ["DWG To PDF.pc3", "Microsoft Print to PDF"])
        old = SimpleNamespace(Name="Model")
        plot = SimpleNamespace(QuietErrorMode=False)
        variables = {"BACKGROUNDPLOT": 3}
        def set_variable(name, value):
            self.assertEqual(value.varianttype, 2)
            variables[name] = value.value
        doc = SimpleNamespace(ActiveLayout=old, Layouts=[sheet], Plot=plot, GetVariable=variables.__getitem__,
                              SetVariable=set_variable)
        self.backend.document.return_value = doc
        return doc, old, plot

    def test_plot_restores_state_and_rejects_invalid_pdf(self):
        doc, old, plot = self.plotting_objects()
        pdf = self.work / "failed.pdf"

        def fail(name, device):
            self.assertEqual(doc.GetVariable("BACKGROUNDPLOT"), 0)
            Path(name).write_bytes(b"non-PDF")
            return True

        plot.PlotToFile = fail
        with self.assertRaisesRegex(RuntimeError, "PDF header"):
            self.tools.export_sheet_pdf(str(self.dwg), "Plan", str(pdf))
        self.assertIs(doc.ActiveLayout, old)
        self.assertEqual(doc.GetVariable("BACKGROUNDPLOT"), 3)
        self.assertIs(plot.QuietErrorMode, False)
        self.assertFalse(pdf.exists())

    def test_plot_publishes_only_new_pdf(self):
        doc, old, plot = self.plotting_objects()
        pdf = self.work / "valid.pdf"

        def plot_file(name, device):
            Path(name).write_bytes(b"%PDF-1.4\nmock content")
            return True

        plot.PlotToFile = Mock(side_effect=plot_file)
        self.assertEqual(self.tools.export_sheet_pdf(str(self.dwg), "Plan", str(pdf))["layout"], "Plan")
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
        self.assertIs(doc.ActiveLayout, old)
        with self.assertRaises(FileExistsError):
            self.tools.export_sheet_pdf(str(self.dwg), "Plan", str(pdf))
        plot.PlotToFile.assert_called_once()

    def test_sheet_rotation_uses_the_device_native_orientation(self):
        for dimensions, expected in (((297, 210), 0), ((210, 297), 1)):
            with self.subTest(dimensions=dimensions):
                sheet = SimpleNamespace(
                    Name="Plan", ModelType=False, RefreshPlotDeviceInfo=Mock(),
                    GetPlotDeviceNames=lambda: ["DWG To PDF.pc3"],
                    GetCanonicalMediaNames=lambda: ["A4"],
                    GetPaperSize=lambda: dimensions, SetCustomScale=Mock(), ConfigName="",
                    CanonicalMediaName="",
                )
                doc = SimpleNamespace(ActiveLayout=sheet, Layouts=[sheet])
                with patch.object(self.tools, "change", return_value=nullcontext(doc)):
                    answer = self.tools.configure_sheet(str(self.dwg), "Plan", "DWG To PDF.pc3",
                                                        media="A4", landscape=True)
                self.assertEqual(answer["native_paper_mm"], list(dimensions))
                self.assertEqual(sheet.PlotRotation, expected)

    def test_leader_edits_validate_type_and_size_before_com(self):
        with self.assertRaisesRegex(ValueError, "require a multileader"):
            self.tools.edit_entity(str(self.dwg), "AA", "AcDbLine", leader_arrow_size=2.5)
        with self.assertRaisesRegex(ValueError, "positive and finite"):
            self.tools.edit_entity(str(self.dwg), "AA", "AcDbMLeader", leader_landing_gap=0)
        self.backend.document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
