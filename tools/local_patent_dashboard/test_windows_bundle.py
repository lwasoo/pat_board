"""Portable bundle filesystem/validation regressions; native Windows is not emulated."""
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import build_windows as bundle
import dashboard_server as server


class WindowsBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def fixture_bundle(self, name='bundle'):
        root=self.root/name
        root.mkdir()
        for filename in bundle.MANAGED:
            if filename=='ocr':continue
            (root/filename).write_text('new '+filename,encoding='utf-8')
        (root/'ocr/tesseract').mkdir(parents=True)
        (root/'ocr/tesseract/tesseract.exe').write_bytes(b'engine')
        (root/'ocr/tesseract/lept.dll').write_bytes(b'dll')
        (root/'ocr/tessdata').mkdir()
        for language in ['chi_sim','eng']:
            (root/'ocr/tessdata'/f'{language}.traineddata').write_bytes(b'model')
        return root

    def test_copy_engine_dlls_and_licenses_without_private_files(self):
        source=self.root/'installed';source.mkdir()
        for name in ['tesseract.exe','lept.dll','LICENSE.txt','uninstall.exe','personal.db','private-notes.txt']:
            (source/name).write_bytes(name.encode())
        (source/'plugins').mkdir();(source/'plugins/codec.dll').write_bytes(b'codec')
        (source/'tessdata').mkdir();(source/'tessdata/unneeded.traineddata').write_bytes(b'old model')
        target=self.root/'portable'
        bundle.copy_runtime(source/'tesseract.exe',target)
        self.assertEqual((target/'plugins/codec.dll').read_bytes(),b'codec')
        self.assertTrue((target/'LICENSE.txt').is_file())
        self.assertFalse((target/'uninstall.exe').exists())
        self.assertFalse((target/'personal.db').exists())
        self.assertFalse((target/'private-notes.txt').exists())
        self.assertFalse((target/'tessdata').exists())

    def test_missing_model_is_rejected(self):
        source=self.root/'models';source.mkdir()
        (source/'eng.traineddata').write_bytes(b'eng')
        with self.assertRaisesRegex(ValueError,'chi_sim'):
            bundle.copy_models(source,self.root/'target')

    def test_zip_whitelist_and_publish_preserve_existing_data(self):
        stage=self.fixture_bundle()
        (stage/'data').mkdir();(stage/'data/patents.db').write_bytes(b'NEVER DISTRIBUTE')
        bundle.write_launcher(stage)
        archive=self.root/'new.zip';bundle.make_archive(stage,archive)
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip())
            names=z.namelist()
            self.assertTrue(any(n.endswith('/start.bat') for n in names))
            self.assertTrue(any(n.endswith('/ocr/tesseract/lept.dll') for n in names))
            self.assertFalse(any('/data/' in n for n in names))
        target=self.root/'output';(target/'data').mkdir(parents=True)
        (target/'data/patents.db').write_bytes(b'USER DATA')
        bundle.publish_bundle(stage,target,archive,self.root/'published.zip')
        self.assertEqual((target/'data/patents.db').read_bytes(),b'USER DATA')
        launcher=(target/'start.bat').read_bytes()
        self.assertIn(b'PATENT_TESSERACT_CMD=%~dp0ocr\\tesseract\\tesseract.exe',launcher)
        self.assertIn(b'PATENT_TESSDATA_DIR=%~dp0ocr\\tessdata',launcher)
        self.assertIn(b'\r\n',launcher)

    def test_locked_output_rolls_back_without_touching_data(self):
        stage=self.fixture_bundle()
        target=self.fixture_bundle('old')
        for name in bundle.MANAGED:
            if name!='ocr':(target/name).write_text('OLD',encoding='utf-8')
        (target/'data').mkdir();(target/'data/patents.db').write_bytes(b'USER DATA')
        archive=self.root/'new.zip';bundle.make_archive(stage,archive)
        real_replace=Path.replace
        def replace(path,destination):
            if path==stage/'ocr':raise PermissionError('simulated live OCR lock')
            return real_replace(path,destination)
        with patch.object(Path,'replace',new=replace):
            with self.assertRaisesRegex(ValueError,'已有 data 未修改'):
                bundle.publish_bundle(stage,target,archive,self.root/'published.zip')
        self.assertEqual((target/'data/patents.db').read_bytes(),b'USER DATA')
        for name in bundle.MANAGED:
            if name!='ocr':self.assertEqual((target/name).read_text(encoding='utf-8'),'OLD')
        self.assertTrue((target/'ocr/tesseract/tesseract.exe').exists())

    def test_relocated_validation_runs_both_models_and_packaged_app(self):
        stage=self.fixture_bundle('移动后的 portable bundle')
        calls=[]
        def run(args,**kwargs):
            calls.append((args,kwargs))
            self.assertEqual(kwargs['cwd'],stage)
            if '--check-runtime' not in args:
                self.assertIn(str(stage/'ocr/tesseract'),kwargs['env']['PATH'])
            if '--version' in args:return subprocess.CompletedProcess(args,0,b'tesseract v5.5.0\n',b'')
            if '--list-langs' in args:return subprocess.CompletedProcess(args,0,b'List of available languages (2):\nchi_sim\neng\n',b'')
            if '--check-runtime' in args:
                self.assertNotIn('PATENT_TESSERACT_CMD',kwargs['env'])
                self.assertNotIn('PATENT_TESSDATA_DIR',kwargs['env'])
                return subprocess.CompletedProcess(args,0,b'',b'')
            self.assertTrue(kwargs['input'].startswith(b'\x89PNG'))
            return subprocess.CompletedProcess(args,0,b'PORTABLE OCR 12345\n',b'')
        with patch.object(bundle.subprocess,'run',side_effect=run):
            self.assertIn('5.5.0',bundle.validate_runtime(stage))
        self.assertEqual(len(calls),5)
        self.assertEqual({c[0][c[0].index('-l')+1] for c in calls if '-l' in c[0]},{'chi_sim','eng'})

    def test_nonworking_engine_is_not_accepted(self):
        stage=self.fixture_bundle()
        with patch.object(bundle.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'DLL missing')):
            with self.assertRaisesRegex(ValueError,'完整依赖 DLL'):
                bundle.validate_runtime(stage)

    def test_runtime_probe_does_not_start_service_or_create_database(self):
        for ready,code in [({'pdf':True,'ocr':True},0),({'pdf':True,'ocr':False},1)]:
            with self.subTest(code=code), patch.object(server.sys,'argv',['app','--check-runtime']), patch.object(server,'capabilities',return_value=ready), patch.object(server,'initialize_database') as database, patch.object(server,'ThreadingHTTPServer') as http:
                with self.assertRaises(SystemExit) as result:server.main()
                self.assertEqual(result.exception.code,code)
                database.assert_not_called();http.assert_not_called()


if __name__=='__main__':unittest.main()
