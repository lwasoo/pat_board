"""Runtime regressions for Windows EXEs with models but no Tesseract program."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import dashboard_documents as documents


class OcrRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.models = self.root / 'models'
        self.models.mkdir()
        (self.models / 'chi_sim.traineddata').touch()
        (self.models / 'eng.traineddata').touch()
        self.env = patch.dict(os.environ, {'PATENT_TESSERACT_CMD':'', 'PATENT_TESSDATA_DIR':''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.which = patch.object(documents.shutil, 'which', return_value=None)
        self.which.start()
        self.addCleanup(self.which.stop)
        self.resources = patch.object(documents.settings, 'resource_path', return_value=self.models)
        self.resources.start()
        self.addCleanup(self.resources.stop)
        self.appdir = patch.object(documents.settings, 'app_dir', return_value=self.root)
        self.appdir.start()
        self.addCleanup(self.appdir.stop)

    def test_models_without_engine_do_not_form_a_command(self):
        with patch.object(documents.sys, 'platform', 'linux'), patch.object(documents.subprocess, 'run') as run:
            self.assertEqual(documents.ocr_command(), [])
            with self.assertRaisesRegex(ValueError, 'Tesseract OCR 引擎'):
                documents.ocr_page(Mock())
            run.assert_not_called()

    def test_windows_standard_install_location(self):
        program_files = self.root/'Program Files'
        executable = program_files/'Tesseract-OCR/tesseract.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        with patch.object(documents.sys, 'platform', 'win32'), patch.dict(os.environ, {'ProgramFiles':str(program_files), 'ProgramFiles(x86)':str(self.root/'absent')}):
            command = documents.ocr_command()
            self.assertEqual(command[0], str(executable))
            self.assertEqual(command[1:], ['--tessdata-dir',str(self.models)])

    def test_windows_per_user_install_location(self):
        local=self.root/'AppData/Local'
        executable=local/'Programs/Tesseract-OCR/tesseract.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        with patch.object(documents.sys,'platform','win32'), patch.dict(os.environ,{'LOCALAPPDATA':str(local)}):
            self.assertEqual(documents.ocr_command()[0],str(executable))

    def test_windows_portable_runtime_location(self):
        executable = self.root/'ocr/tesseract/tesseract.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        with patch.object(documents.sys, 'platform', 'win32'), patch.object(documents.shutil, 'which', return_value='C:/old/tesseract.exe'):
            self.assertEqual(documents.ocr_command()[0], str(executable))

    def test_external_models_are_used_by_a_frozen_portable_app(self):
        models=self.root/'ocr/tessdata'
        models.mkdir(parents=True)
        for language in ['eng','chi_sim']:(models/(language+'.traineddata')).touch()
        with patch.object(documents.shutil,'which',return_value='/engine/tesseract'):
            self.assertEqual(documents.ocr_command()[1:],['--tessdata-dir',str(models)])

    def test_windows_ocr_loads_models_from_unicode_directory(self):
        models = self.root / '中文目录 with spaces' / 'tessdata'
        models.mkdir(parents=True)
        executable = self.root / 'tesseract.exe'
        command = [str(executable), '--tessdata-dir', str(models), '--list-langs']
        with patch.object(documents.sys, 'platform', 'win32'), patch.object(documents.subprocess, 'run') as run:
            documents.run_tesseract(command, capture_output=True)
        run.assert_called_once_with(
            [str(executable.resolve()), '--tessdata-dir', '.', '--list-langs'],
            cwd=models.resolve(),
            capture_output=True,
        )

    def test_invalid_override_does_not_execute_model_flags(self):
        with patch.dict(os.environ, {'PATENT_TESSERACT_CMD':str(self.root/'missing.exe')}):
            self.assertEqual(documents.ocr_command(), [])

    def test_spawn_failure_identifies_ocr_not_pdf(self):
        pixmap = Mock()
        pixmap.tobytes.return_value = b'png fixture'
        with patch.object(documents, 'ocr_command', return_value=['missing-tesseract.exe']), patch.object(documents.subprocess,'run',side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(ValueError,'无法启动 Tesseract'):
                documents.ocr_page(pixmap)
        with patch.object(documents, 'ocr_command', return_value=['broken-tesseract.exe']), patch.object(documents.subprocess,'run',side_effect=OSError('DLL unavailable')):
            with self.assertRaisesRegex(ValueError,'依赖 DLL'):
                documents.ocr_page(pixmap)

    def test_legacy_empty_success_is_reported_as_failure(self):
        result={'status':'ready','pages':[{'text':'','ocr':False} for _ in range(22)],
                'claims':[],'description':'','metadata':{},
                'warnings':[f'第 {i} 页：未找到 Tesseract OCR 引擎。' for i in range(1,23)]}
        documents.normalize_completed_status(result)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['errorCode'],'OCR_ENGINE_MISSING')
        self.assertEqual(result['ocrUnavailablePages'],list(range(1,23)))
        self.assertEqual(len(result['warnings']),1)

    def test_manually_reviewed_content_is_preserved(self):
        result={'status':'ready','pages':[{'text':''}], 'claims':[{'text':'人工校对的权利要求'}],
                'warnings':['未找到 Tesseract OCR 引擎。']}
        documents.normalize_completed_status(result)
        self.assertEqual(result['status'],'ready')
        self.assertIn('未完成',result['message'])


if __name__=='__main__': unittest.main()
