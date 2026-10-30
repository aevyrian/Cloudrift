import asyncio
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.playback import PlaybackCache, needs_compatibility


def streams(codec='hevc', pixel='yuv420p'):
    return {'streams':[{'codec_type':'video','codec_name':codec,'pix_fmt':pixel,'width':1080,'height':1920},
                       {'codec_type':'audio','codec_name':'aac'}], 'format':{'duration':'9.0'}}


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'original.mp4';self.source.write_bytes(b'original-hevc')
        self.cache=PlaybackCache(self.root/'cache',limit=1024)

    async def asyncTearDown(self):
        await self.cache.close();self.tmp.cleanup()

    def test_codec_and_pixel_format_need_a_browser_copy(self):
        self.assertTrue(needs_compatibility(self.source,streams()))
        self.assertFalse(needs_compatibility(self.source,streams('h264')))
        self.assertTrue(needs_compatibility(self.source,streams('h264','yuv420p10le')))
        self.assertTrue(needs_compatibility(self.root/'movie.mkv',streams('h264')))

    async def test_single_atomic_copy_reused_and_original_untouched(self):
        checksum=hashlib.sha256(self.source.read_bytes()).hexdigest()
        original=self.source.stat();gate=asyncio.Event()
        async def encode(path,temporary,record):
            temporary.write_bytes(b'partial');await gate.wait();temporary.write_bytes(b'complete')
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=encode) as run:
            first=await self.cache.prepare(self.source)
            self.assertEqual(first['status'],'preparing')
            await asyncio.sleep(0)
            await self.cache.prepare(self.source)
            self.assertEqual(run.call_count,1);self.assertIsNone(self.cache.ready(self.source))
            gate.set();await asyncio.gather(*list(self.cache.jobs.values()))
            second=await self.cache.prepare(self.source)
            self.assertEqual(second['status'],'ready');self.assertEqual(second['mode'],'compatible')
            self.assertEqual(self.cache.ready(self.source).read_bytes(),b'complete')
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),checksum)
        self.assertEqual(self.source.stat().st_mtime_ns,original.st_mtime_ns)
        self.assertEqual(self.source.stat().st_mode,original.st_mode)
        if os.name!='nt':self.assertEqual(self.cache.ready(self.source).stat().st_mode&0o777,0o600)

    async def test_failed_copy_not_published_and_can_retry(self):
        async def broken(path,temporary,record):
            temporary.write_bytes(b'broken');raise RuntimeError('decode failed')
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=broken):
            await self.cache.prepare(self.source);await asyncio.gather(*list(self.cache.jobs.values()))
            self.assertEqual((await self.cache.prepare(self.source))['status'],'failed')
            self.assertIsNone(self.cache.ready(self.source));self.assertEqual(list(self.cache.root.glob('*.part.mp4')),[])
            self.assertEqual((await self.cache.prepare(self.source,retry=True))['status'],'preparing')
            await asyncio.gather(*list(self.cache.jobs.values()))

    async def test_native_video_avoids_encoding_and_source_change_invalidates_cache(self):
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams('h264'))),patch.object(self.cache,'encode',AsyncMock()) as run:
            self.assertEqual((await self.cache.prepare(self.source))['mode'],'original');run.assert_not_called()
        output=self.cache.target(self.source);output.write_bytes(b'old-cache')
        self.source.write_bytes(b'changed-original')
        self.assertIsNone(self.cache.ready(self.source))

    def test_pruning_only_removes_generated_copies(self):
        other=self.cache.root/'keep-me.txt';other.write_bytes(b'user-content')
        old=self.cache.root/('a'*64+'.mp4');old.write_bytes(b'0'*900)
        current=self.cache.root/('b'*64+'.mp4');current.write_bytes(b'1'*500)
        self.cache.prune(current)
        self.assertFalse(old.exists());self.assertTrue(current.exists());self.assertTrue(other.exists());self.assertTrue(self.source.exists())

    async def test_only_one_encoder_runs_and_pending_work_is_bounded(self):
        gate=asyncio.Event();active=0;peak=0
        async def encode(path,temporary,record):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            try:await gate.wait();temporary.write_bytes(b'complete')
            finally:active-=1
        second=self.root/'second.mp4';second.write_bytes(b'2')
        third=self.root/'third.mp4';third.write_bytes(b'3')
        fourth=self.root/'fourth.mp4';fourth.write_bytes(b'4')
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=encode):
            await self.cache.prepare(self.source);await self.cache.prepare(second)
            await self.cache.prepare(third)
            self.assertEqual((await self.cache.prepare(fourth))['status'],'waiting')
            await asyncio.sleep(0);self.assertEqual(peak,1)
            gate.set();await asyncio.gather(*list(self.cache.jobs.values()));self.assertEqual(peak,1)

    async def test_foreground_playback_cancels_unwatched_preloads(self):
        gate=asyncio.Event();cancelled=[]
        async def encode(path,temporary,record):
            try:await gate.wait();temporary.write_bytes(b'complete')
            except asyncio.CancelledError:cancelled.append(path);raise
        foreground=self.root/'current.mp4';foreground.write_bytes(b'current')
        queued=self.root/'queued.mp4';queued.write_bytes(b'queued')
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=encode):
            await self.cache.prepare(self.source,warm=True);await asyncio.sleep(0)
            await self.cache.prepare(queued,warm=True)
            self.assertEqual((await self.cache.prepare(foreground))['status'],'preparing')
            self.assertEqual(cancelled,[self.source]);self.assertEqual(len(self.cache.jobs),1)
            gate.set();await asyncio.gather(*list(self.cache.jobs.values()))
            self.assertTrue(self.cache.ready(foreground));self.assertFalse(self.cache.ready(queued))

    async def test_preload_promoted_to_foreground_is_not_cancelled(self):
        gate=asyncio.Event()
        async def encode(path,temporary,record):
            await gate.wait();temporary.write_bytes(b'complete')
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=encode) as run:
            await self.cache.prepare(self.source,warm=True);await asyncio.sleep(0)
            await self.cache.prepare(self.source)
            self.assertFalse(next(iter(self.cache.records.values()))['warm'])
            self.assertEqual(run.call_count,1)
            gate.set();await asyncio.gather(*list(self.cache.jobs.values()))

    def test_default_cache_retains_twenty_gib(self):
        cache=PlaybackCache(self.root/'default-cache')
        self.assertEqual(cache.limit,20*1024**3)

    async def test_hardware_failure_falls_back_only_for_that_file(self):
        self.cache.hardware=True;temporary=self.root/'partial.mp4';temporary.write_bytes(b'failed-gpu')
        record={'progress':50,'clients':0}
        async def run(path,target,current,hardware):
            if hardware:raise RuntimeError('unsupported input')
            self.assertFalse(target.exists());target.write_bytes(b'software-copy')
        with patch.object(self.cache,'_encode',side_effect=run) as encode:
            await self.cache.encode(self.source,temporary,record)
            self.assertEqual([call.args[3] for call in encode.call_args_list],[True,False])
        self.assertTrue(record['software']);self.assertTrue(self.cache.hardware)
        self.assertEqual(temporary.read_bytes(),b'software-copy')

    async def test_hardware_failure_cannot_replace_an_active_stream(self):
        self.cache.hardware=True;temporary=self.root/'playing.mp4';temporary.write_bytes(b'playing')
        record={'clients':1}
        with patch.object(self.cache,'_encode',AsyncMock(side_effect=RuntimeError('GPU failed'))) as encode:
            with self.assertRaisesRegex(RuntimeError,'重试'):await self.cache.encode(self.source,temporary,record)
            self.assertEqual(encode.await_count,1)
        self.assertTrue(record['software']);self.assertEqual(temporary.read_bytes(),b'playing')

    async def test_hardware_timeout_kills_process_and_falls_back_without_clients(self):
        self.cache.hardware=True;temporary=self.root/'timeout.mp4';temporary.write_bytes(b'partial-gpu')
        record={'clients':0,'duration':9,'requested':time.monotonic(),'progress':50}
        hardware=Mock(returncode=None);hardware.stdout.readline=AsyncMock(side_effect=asyncio.TimeoutError)
        hardware.wait=AsyncMock()
        software=Mock(returncode=0);software.stdout.readline=AsyncMock(return_value=b'');software.wait=AsyncMock()
        async def spawn(*args,**kwargs):
            if '-hwaccel' in args:return hardware
            self.assertFalse(temporary.exists());temporary.write_bytes(b'software-copy');return software
        with patch('app.playback.asyncio.create_subprocess_exec',side_effect=spawn),patch.object(self.cache,'probe',AsyncMock(return_value=streams('h264'))):
            try:await self.cache.encode(self.source,temporary,record)
            except asyncio.TimeoutError:self.fail('Hardware timeout must fall back to software')
        hardware.kill.assert_called_once();hardware.wait.assert_awaited_once()
        self.assertTrue(record['software']);self.assertTrue(self.cache.hardware)
        self.assertEqual(temporary.read_bytes(),b'software-copy');self.assertEqual(self.source.read_bytes(),b'original-hevc')

    async def test_hardware_timeout_preserves_active_stream_until_software_retry(self):
        self.cache.hardware=True;temporary=self.root/'playing-timeout.mp4';temporary.write_bytes(b'playing')
        record={'clients':1,'duration':9,'requested':time.monotonic(),'progress':50}
        process=Mock(returncode=None);process.stdout.readline=AsyncMock(side_effect=asyncio.TimeoutError);process.wait=AsyncMock()
        with patch('app.playback.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn:
            try:
                with self.assertRaisesRegex(RuntimeError,'重试'):await self.cache.encode(self.source,temporary,record)
            except asyncio.TimeoutError:self.fail('Active hardware timeout must offer a software retry')
            self.assertEqual(spawn.await_count,1)
        process.kill.assert_called_once();process.wait.assert_awaited_once()
        self.assertTrue(record['software']);self.assertEqual(temporary.read_bytes(),b'playing')
        record['clients']=0
        process=Mock(returncode=0);process.stdout.readline=AsyncMock(return_value=b'');process.wait=AsyncMock()
        with patch('app.playback.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn,patch.object(self.cache,'probe',AsyncMock(return_value=streams('h264'))):
            await self.cache.encode(self.source,temporary,record)
        self.assertNotIn('-hwaccel',spawn.await_args.args)

    async def test_probe_timeout_terminates_process_and_has_actionable_message(self):
        process=Mock(returncode=None);process.communicate=AsyncMock(side_effect=asyncio.TimeoutError)
        process.wait=AsyncMock()
        with patch('app.playback.asyncio.create_subprocess_exec',AsyncMock(return_value=process)):
            with self.assertRaisesRegex(RuntimeError,'超时'):await self.cache.probe(self.source)
        process.kill.assert_called_once();process.wait.assert_awaited_once()

    async def test_abandoned_preparation_releases_the_encoder(self):
        self.cache.hardware=True
        process=Mock(returncode=None);process.stdout.readline=AsyncMock(return_value=b'progress=continue\n');process.wait=AsyncMock()
        record={'duration':9,'requested':time.monotonic()-20,'clients':0}
        with patch('app.playback.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn:
            with self.assertRaises(asyncio.CancelledError):
                await self.cache.encode(self.source,self.root/'part.mp4',record)
            self.assertEqual(spawn.await_count,1)
        self.assertFalse(record.get('software'))
        process.kill.assert_called_once();process.wait.assert_awaited_once()

    @unittest.skipIf(os.name=='nt','Requires POSIX rename of an open stream; run on Linux NAS')
    async def test_progressive_playback_before_atomic_cache_completion(self):
        gate=asyncio.Event()
        async def encode(path,temporary,record):
            temporary.write_bytes(b'a'*8192);await gate.wait()
            with temporary.open('ab') as f:f.write(b'b'*1024)
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=encode):
            await self.cache.prepare(self.source);await asyncio.sleep(0)
            status=await self.cache.prepare(self.source)
            self.assertEqual(status['status'],'ready');self.assertTrue(status['streaming']);self.assertIsNone(self.cache.ready(self.source))
            iterator=self.cache.stream(self.source);first=await anext(iterator);self.assertEqual(first,b'a'*8192)
            self.assertEqual(next(iter(self.cache.records.values()))['clients'],1)
            gate.set();await asyncio.gather(*list(self.cache.jobs.values()))
            rest=b''.join([chunk async for chunk in iterator]);self.assertEqual(rest,b'b'*1024)
            self.assertEqual(self.cache.ready(self.source).read_bytes(),first+rest)
            self.assertEqual(next(iter(self.cache.records.values()))['clients'],0)

    @unittest.skipIf(os.name=='nt','Requires POSIX unlink of an open stream; run on Linux NAS')
    async def test_stream_failure_does_not_look_like_a_complete_copy(self):
        gate=asyncio.Event()
        async def encode(path,temporary,record):
            temporary.write_bytes(b'a'*8192);await gate.wait();raise RuntimeError('decode failed')
        with patch.object(self.cache,'probe',AsyncMock(return_value=streams())),patch.object(self.cache,'encode',side_effect=encode):
            await self.cache.prepare(self.source);await asyncio.sleep(0)
            iterator=self.cache.stream(self.source);await anext(iterator)
            gate.set();await asyncio.gather(*list(self.cache.jobs.values()))
            with self.assertRaisesRegex(RuntimeError,'未完成'):await anext(iterator)
            self.assertIsNone(self.cache.ready(self.source))
