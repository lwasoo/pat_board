"""Installer supply-chain/cache behavior, with no native installer execution."""
import hashlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import ocr_installer as installer


class OcrInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.target=Path(self.temporary.name)/'cache/setup.exe'
        self.payload=b'MZ verified fixture installer'
        self.digest=patch.object(installer,'SHA256',hashlib.sha256(self.payload).hexdigest())
        self.digest.start();self.addCleanup(self.digest.stop)

    def test_local_verified_installer_needs_no_download(self):
        self.target.parent.mkdir()
        self.target.write_bytes(self.payload)
        with patch.object(installer.urllib.request,'urlopen') as fetch:
            self.assertEqual(installer.ensure_installer(self.target),self.target.resolve())
            fetch.assert_not_called()

    def test_wrong_local_file_is_rejected(self):
        self.target.parent.mkdir();self.target.write_bytes(b'changed installer')
        with self.assertRaisesRegex(ValueError,'SHA-256 不匹配'):
            installer.verify_installer(self.target)

    def test_transport_failure_uses_next_official_host(self):
        with patch.object(installer.urllib.request,'urlopen',side_effect=[urllib.error.URLError('network unavailable'),io.BytesIO(self.payload)]) as fetch:
            self.assertEqual(installer.ensure_installer(self.target),self.target.resolve())
            self.assertEqual(fetch.call_count,2)
            self.assertEqual([call.args[0] for call in fetch.call_args_list],list(installer.URLS))
            self.assertEqual(self.target.read_bytes(),self.payload)
            self.assertFalse(list(self.target.parent.glob('*.part')))

    def test_bad_download_never_replaces_existing_cache(self):
        self.target.parent.mkdir();self.target.write_bytes(b'old invalid cache')
        with patch.object(installer.urllib.request,'urlopen',side_effect=lambda *args,**kwargs:io.BytesIO(b'wrong download')):
            with self.assertRaisesRegex(ValueError,'官方安装包下载失败'):
                installer.ensure_installer(self.target)
        self.assertEqual(self.target.read_bytes(),b'old invalid cache')
        self.assertFalse(list(self.target.parent.glob('*.part')))

    def test_success_requires_matching_checksum(self):
        with patch.object(installer.urllib.request,'urlopen',return_value=io.BytesIO(self.payload)):
            installer.ensure_installer(self.target)
        self.assertEqual(installer.verify_installer(self.target),self.target.resolve())


if __name__=='__main__':unittest.main()
