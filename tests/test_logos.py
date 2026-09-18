from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import httpx
from PIL import Image
from highiv import logos

class LogoTests(TestCase):
    def test_webp_resize_and_cache_avoid_repeat_downloads(self):
        image = BytesIO()
        Image.new('RGBA', (200, 100), '#3344aa').save(image, format='PNG')
        response = httpx.Response(200, content=image.getvalue(), request=httpx.Request('GET','https://example.com'))
        with TemporaryDirectory() as directory, patch.object(logos.config, 'DATA_DIR', Path(directory)), patch.object(logos.httpx, 'stream') as stream:
            stream.return_value.__enter__.return_value = response
            content = logos.fetch('SHOP.TO')
            with Image.open(BytesIO(content)) as saved:
                self.assertEqual(saved.format, 'WEBP')
                self.assertEqual(saved.size, (48,24))
            self.assertLess(len(content), logos.MAX_WEBP_BYTES)
            self.assertEqual(logos.fetch('SHOP.TO'), content)
            self.assertEqual(stream.call_count,1)
            self.assertIn('SHOP.TO.png', stream.call_args.args[1])

    def test_missing_image_is_negative_cached_and_invalid_symbols_not_requested(self):
        with TemporaryDirectory() as directory, patch.object(logos.config, 'DATA_DIR', Path(directory)), patch.object(logos.httpx,'stream',side_effect=httpx.ConnectError('offline')) as stream:
            self.assertIsNone(logos.fetch('MISSING'))
            self.assertIsNone(logos.fetch('MISSING'))
            self.assertIsNone(logos.fetch('../secret'))
            self.assertEqual(stream.call_count,1)

    def test_non_image_response_falls_back(self):
        response=httpx.Response(200,content=b'<html>error</html>',request=httpx.Request('GET','https://example.com'))
        with TemporaryDirectory() as directory, patch.object(logos.config,'DATA_DIR',Path(directory)), patch.object(logos.httpx,'stream') as stream:
            stream.return_value.__enter__.return_value=response
            self.assertIsNone(logos.fetch('FAIL'))
