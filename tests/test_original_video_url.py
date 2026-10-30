import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vendor/TikTokDownloader'))
from src.extract.extractor import Extractor


class OriginalVideoURLTests(unittest.IsolatedAsyncioTestCase):
    def extractor(self,probe):
        value=Extractor.__new__(Extractor)
        value.download=value.original_quality=True
        rate=SimpleNamespace(FPS=30,bit_rate=2000,play_addr=SimpleNamespace(data_size=10,height=1280,width=720,url_list=['https://media.example/bitrate']*3))
        value.safe_extract=lambda data,key,default=None: {'video.bit_rate':[rate],'video.play_addr.uri':'original-uri'}.get(key,default)
        value._Extractor__request_video_size=AsyncMock(return_value=probe)
        return value

    async def test_original_uses_stable_endpoint_instead_of_expiring_redirect(self):
        extractor=self.extractor((20,'https://temporary.example/video?expires=1'))
        height,width,url=await extractor._Extractor__extract_video_download(SimpleNamespace(),use_original_quality=True)
        self.assertEqual((height,width),(1280,720))
        self.assertEqual(url,'https://www.douyin.com/aweme/v1/play/?video_id=original-uri&ratio=default')
        self.assertNotIn('temporary',url)

    async def test_rejected_original_preserves_best_bitrate_and_quality_setting(self):
        extractor=self.extractor((-1,''))
        _,_,url=await extractor._Extractor__extract_video_download(SimpleNamespace(),use_original_quality=True)
        self.assertEqual(url,'https://media.example/bitrate')
        extractor.original_quality=False
        extractor._Extractor__request_video_size.reset_mock()
        await extractor._Extractor__extract_video_download(SimpleNamespace(),use_original_quality=True)
        extractor._Extractor__request_video_size.assert_not_awaited()
