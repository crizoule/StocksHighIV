"""Exercise update trust boundaries with a disposable signing key, without network."""
import base64
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

spec = importlib.util.spec_from_file_location('windows_updater', Path(__file__).resolve().parents[1]/'packaging/windows/updater.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class WindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = Ed25519PrivateKey.generate()
        public = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        patcher = patch.object(updater, 'PUBLIC_KEY', public)
        patcher.start(); self.addCleanup(patcher.stop)

    def package(self, extra=None, build=6):
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('StocksHighIV/StocksHighIV.exe', b'fixture executable')
            z.writestr('StocksHighIV/release.json', json.dumps({'version':'1.3.0', 'build':str(build)}))
            if extra: z.writestr(extra, b'unexpected')
        content = stream.getvalue()
        manifest = dict(version='1.3.0', build=6,
            url='https://github.com/crizoule/StocksHighIV/releases/download/v1.3.0/StocksHighIV-Windows.zip',
            sha256=hashlib.sha256(content).hexdigest(), signature=base64.b64encode(self.key.sign(content)).decode())
        return content, manifest

    def test_verified_side_by_side_install_preserves_user_data(self):
        (self.root/'data').mkdir(); (self.root/'data/watchlist.json').write_text('["AAPL"]')
        content, manifest = self.package()
        folder = updater.stage(content, manifest, self.root/'versions', 5)
        self.assertEqual((folder/'StocksHighIV.exe').read_bytes(), b'fixture executable')
        self.assertEqual((self.root/'data/watchlist.json').read_text(), '["AAPL"]')
        updater.save_pointer(self.root, 6, previous=5)
        self.assertEqual(json.loads((self.root/'current.json').read_text()), {'build':6,'previous':5})
        self.assertEqual(updater.stage(content,manifest,self.root/'versions',5), folder)

    def test_tampering_rejected_even_if_checksum_is_recomputed(self):
        content, manifest = self.package()
        content += b'changed'
        manifest['sha256'] = hashlib.sha256(content).hexdigest()
        with self.assertRaises(InvalidSignature): updater.stage(content,manifest,self.root/'versions',5)
        self.assertFalse((self.root/'versions/6').exists())

    def test_traversal_and_unexpected_files_rejected_even_when_signed(self):
        for name in ('StocksHighIV/../../escape', '/escape', 'StocksHighIV/evil.dll', 'StocksHighIV\\escape', 'C:/escape'):
            content,manifest = self.package(extra=name)
            with self.assertRaises(ValueError): updater.stage(content,manifest,self.root/'versions',5)
        self.assertFalse((self.root/'versions/6').exists())

    def test_signed_package_cannot_spoof_announced_build(self):
        content,manifest = self.package(build=1)
        with self.assertRaises(ValueError): updater.stage(content,manifest,self.root/'versions',5)

    def test_downgrades_and_external_downloads_rejected(self):
        _,manifest = self.package()
        self.assertIsNone(updater.validate_manifest(manifest, 6))
        manifest['url'] = 'https://example.com/update.zip'
        with self.assertRaises(ValueError): updater.validate_manifest(manifest, 5)

    def test_network_failure_leaves_installed_version_untouched(self):
        updater.save_pointer(self.root,5)
        with patch.object(updater,'read_url',side_effect=OSError('offline')):
            with self.assertRaises(OSError): updater.check(5)
        self.assertEqual(json.loads((self.root/'current.json').read_text())['build'],5)
