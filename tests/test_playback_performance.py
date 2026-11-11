import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.playback import PlaybackCache


INFO = {'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'pix_fmt': 'yuv420p'}], 'format': {'duration': '1'}}


class PlaybackPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = PlaybackCache(self.root / 'cache')

    async def asyncTearDown(self):
        await self.cache.close()
        self.tmp.cleanup()

    def source(self, name):
        path = self.root / f'{name}.mp4'
        path.write_bytes(name.encode())
        return path

    async def test_ready_cache_does_not_wait_for_another_probe(self):
        slow, cached = self.source('slow'), self.source('cached')
        self.cache.target(cached).write_bytes(b'cached')
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe(path):
            entered.set()
            await release.wait()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            first = asyncio.create_task(self.cache.prepare(slow))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                result = await asyncio.wait_for(self.cache.prepare(cached), .2)
                self.assertEqual(result['mode'], 'compatible')
            finally:
                release.set()
                await first

    async def test_same_video_probe_is_shared(self):
        source = self.source('same')
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def probe(path):
            calls.append(path)
            entered.set()
            await release.wait()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            first = asyncio.create_task(self.cache.prepare(source))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                second = asyncio.create_task(self.cache.prepare(source))
                await asyncio.sleep(.05)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                await asyncio.gather(first, second)
            self.assertEqual(len(calls), 1)

    async def test_two_probes_can_run_but_total_is_bounded(self):
        entered, release = asyncio.Event(), asyncio.Event()
        active = 0
        peak = 0
        async def probe(path):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return INFO
            finally:
                active -= 1
        with patch.object(self.cache, 'probe', side_effect=probe):
            tasks = [asyncio.create_task(self.cache.prepare(self.source(str(i)))) for i in range(5)]
            try:
                await asyncio.wait_for(entered.wait(), .3)
                await asyncio.sleep(.05)
                self.assertEqual(peak, 2)
            finally:
                release.set()
                await asyncio.gather(*tasks)

    async def test_foreground_can_probe_while_warm_probe_is_slow(self):
        warm, foreground = self.source('warm'), self.source('foreground')
        warm_started, foreground_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def probe(path):
            if path == warm:
                warm_started.set()
                await release.wait()
            else:
                foreground_started.set()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            background = asyncio.create_task(self.cache.prepare(warm, warm=True))
            try:
                await asyncio.wait_for(warm_started.wait(), 1)
                current = asyncio.create_task(self.cache.prepare(foreground))
                await asyncio.wait_for(foreground_started.wait(), .3)
                self.assertEqual((await current)['mode'], 'original')
            finally:
                release.set()
                await background

    async def test_queued_foreground_starts_before_queued_warm(self):
        paths = {name: self.source(name) for name in ('warm1', 'warm2', 'current1', 'current2')}
        started = {name: asyncio.Event() for name in paths}
        release = {name: asyncio.Event() for name in paths}
        order = []
        async def probe(path):
            name = path.stem
            order.append(name)
            started[name].set()
            await release[name].wait()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            tasks = []
            try:
                tasks.append(asyncio.create_task(self.cache.prepare(paths['warm1'], warm=True)))
                tasks.append(asyncio.create_task(self.cache.prepare(paths['current1'])))
                await asyncio.wait_for(asyncio.gather(started['warm1'].wait(), started['current1'].wait()), 1)
                tasks.append(asyncio.create_task(self.cache.prepare(paths['warm2'], warm=True)))
                await asyncio.sleep(.01)
                tasks.append(asyncio.create_task(self.cache.prepare(paths['current2'])))
                await asyncio.sleep(.01)
                release['current1'].set()
                await asyncio.wait_for(started['current2'].wait(), .5)
                self.assertFalse(started['warm2'].is_set())
            finally:
                for event in release.values():event.set()
                await asyncio.gather(*tasks)
            self.assertLess(order.index('current2'), order.index('warm2'))

    async def test_cancelling_one_waiter_keeps_shared_probe(self):
        source = self.source('shared')
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def probe(path):
            calls.append(path)
            entered.set()
            await release.wait()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            first = asyncio.create_task(self.cache.prepare(source))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                second = asyncio.create_task(self.cache.prepare(source))
                await asyncio.sleep(.02)
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                self.assertTrue(first.cancelled())
            finally:
                release.set()
            self.assertEqual((await second)['mode'], 'original')
            self.assertEqual(len(calls), 1)

    async def test_probe_queue_is_bounded_and_leaves_space_for_foreground(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe(path):
            entered.set()
            await release.wait()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            tasks = []
            try:
                tasks = [asyncio.create_task(self.cache.prepare(self.source(f'warm{i}'), warm=True)) for i in range(12)]
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.sleep(.05)
                self.assertEqual(len(self.cache.probes), 8)
                self.assertEqual(sum(task.done() for task in tasks), 4)
                foreground = asyncio.create_task(self.cache.prepare(self.source('current')))
                tasks.append(foreground)
                await asyncio.sleep(.05)
                self.assertEqual(self.cache.active_probes, 2)
                self.assertEqual(len(self.cache.probes), 9)
            finally:
                release.set()
                await asyncio.gather(*tasks)

    async def test_last_cancelled_waiter_stops_unneeded_probe(self):
        source = self.source('abandoned')
        entered, stopped = asyncio.Event(), asyncio.Event()
        async def probe(path):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        with patch.object(self.cache, 'probe', side_effect=probe):
            task = asyncio.create_task(self.cache.prepare(source))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(stopped.wait(), 1)
            self.assertFalse(self.cache.probes)

    async def test_new_request_waits_for_cancelled_probe_cleanup_then_retries(self):
        source = self.source('rapid-return')
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0
        async def probe(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                try:await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
            return INFO
        with patch.object(self.cache, 'probe', side_effect=probe):
            first = asyncio.create_task(self.cache.prepare(source))
            await asyncio.wait_for(entered.wait(), 1)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await asyncio.wait_for(cleaning.wait(), 1)
            second = asyncio.create_task(self.cache.prepare(source))
            try:
                await asyncio.sleep(.05)
                self.assertEqual(calls, 1)
                self.assertEqual(self.cache.active_probes, 1)
            finally:
                release.set()
            result = await asyncio.wait_for(second, 1)
            self.assertEqual(result['mode'], 'original')
            self.assertEqual(calls, 2)
            self.assertEqual(self.cache.active_probes, 0)

    async def test_close_clears_probes_even_when_task_has_not_started(self):
        source = self.source('closing')
        task = asyncio.create_task(self.cache.prepare(source))
        await asyncio.sleep(0)
        self.assertTrue(self.cache.probes)
        await self.cache.close()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.cache.probes)
        self.assertEqual(self.cache.active_probes, 0)

    async def test_close_cancels_running_and_waiting_probes(self):
        entered = asyncio.Event()
        active = 0
        async def probe(path):
            nonlocal active
            active += 1
            if active == 2:entered.set()
            try:await asyncio.Event().wait()
            finally:active -= 1
        with patch.object(self.cache, 'probe', side_effect=probe):
            tasks = [asyncio.create_task(self.cache.prepare(self.source(f'close{i}'))) for i in range(3)]
            await asyncio.wait_for(entered.wait(), 1)
            await self.cache.close()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.assertEqual(active, 0)
            self.assertEqual(self.cache.active_probes, 0)
            self.assertFalse(self.cache.probes)

    async def test_close_waits_for_already_cancelled_probe_cleanup(self):
        source = self.source('closing-cancelled')
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def probe(path):
            entered.set()
            try:await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        with patch.object(self.cache, 'probe', side_effect=probe):
            request = asyncio.create_task(self.cache.prepare(source))
            await asyncio.wait_for(entered.wait(), 1)
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            await asyncio.wait_for(cleaning.wait(), 1)
            closing = asyncio.create_task(self.cache.close())
            try:
                await asyncio.sleep(.05)
                self.assertFalse(closing.done())
            finally:
                release.set()
                await closing
            self.assertFalse(self.cache.probes)

    async def test_disk_flush_does_not_block_loop_and_cancel_waits_for_flush(self):
        source = self.source('flush')
        target = self.cache.target(source)
        started, release = threading.Event(), threading.Event()
        async def encode(path, temporary, record):
            temporary.write_bytes(b'copy')
        def flush(fd):
            started.set()
            release.wait(2)
        with patch.object(self.cache, 'encode', side_effect=encode), patch('app.playback.os.fsync', side_effect=flush):
            task = asyncio.create_task(self.cache.convert(source, target, {}))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.5))
                task.cancel()
                await asyncio.sleep(.05)
                self.assertFalse(task.done())
                self.assertTrue(target.with_suffix('.part.mp4').exists())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(task.cancelled())
            self.assertFalse(target.with_suffix('.part.mp4').exists())


if __name__ == '__main__':
    unittest.main()
