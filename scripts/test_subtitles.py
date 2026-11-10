"""Subtitle preservation, including a real FFmpeg round trip when installed."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tvplay import formats
from tvplay.jobs import JobManager


def media(subtitles=(), audio='dts'):
    return {'format': {'format_name': 'matroska,webm'}, 'streams': [
        {'index': 0, 'codec_type': 'video', 'codec_name': 'h264'},
        {'index': 1, 'codec_type': 'audio', 'codec_name': audio},
        *[{'index': index + 2, 'codec_type': 'subtitle', 'codec_name': codec,
           'tags': {'language': 'eng'}} for index, codec in enumerate(subtitles)]]}


class SubtitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_converted_cache_is_rebuilt_but_current_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = JobManager.__new__(JobManager)
            manager.settings = {}; manager.jobs = {}
            manager.cache = Mock()
            manager.cache.path.side_effect = lambda name: Path(folder) / name
            manager._set = Mock()
            entry = dict(id=1, source_name='source.mkv', source_size=100,
                         play_name='source.tv.mp4', mime='video/mp4', duration=1,
                         probe='{"action":"audio"}')
            old_file = Path(folder) / entry['play_name']
            old_file.write_bytes(b'old conversion')
            async def converted(settings, source, destination, plan, duration, progress):
                destination.write_bytes(b'converted')
            with patch.object(formats, 'probe', AsyncMock(return_value=media(['subrip']))), patch.object(
                    formats, 'convert', AsyncMock(side_effect=converted)) as convert:
                await manager._prepare({}, entry)
                convert.assert_awaited_once()
                self.assertEqual(manager.cache.set_playable.call_args.args[1], 'source.tv.mkv')
                self.assertIn('"subtitle_version": 1', manager.cache.set_playable.call_args.args[3])
                self.assertFalse(old_file.exists())
                entry['probe'] = '{"subtitle_version":1}'
                self.assertIs(await manager._prepare({}, entry), entry)
                convert.assert_awaited_once()

    async def test_existing_conversion_survives_missing_probe_or_failed_rebuild(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = JobManager.__new__(JobManager)
            manager.settings = {}; manager.jobs = {}; manager._set = Mock()
            manager.cache = Mock()
            manager.cache.path.side_effect = lambda name: Path(folder) / name
            entry = dict(id=1, source_name='source.mkv', source_size=100,
                         play_name='source.tv.mp4', mime='video/mp4', duration=1, probe='{}')
            old_file = Path(folder) / entry['play_name']
            old_file.write_bytes(b'old conversion')
            with patch.object(formats, 'probe', AsyncMock(return_value=None)):
                self.assertIs(await manager._prepare({}, entry), entry)
                manager.cache.set_playable.assert_not_called()
            with patch.object(formats, 'probe', AsyncMock(return_value=media(['subrip']))), patch.object(
                    formats, 'convert', AsyncMock(side_effect=RuntimeError('failed'))):
                with self.assertRaises(RuntimeError):
                    await manager._prepare({}, entry)
            self.assertEqual(old_file.read_bytes(), b'old conversion')
            manager.cache.set_playable.assert_not_called()

    def test_direct_and_no_subtitle_paths(self):
        self.assertEqual(formats.plan_for(media(['subrip'], 'aac'), '').action, 'direct')
        self.assertEqual(formats.plan_for(media(), '').container, 'mp4')

    def test_conversion_preserves_text_and_bitmap_tracks(self):
        plan = formats.plan_for(media(['subrip', 'hdmv_pgs_subtitle', 'mov_text']), '')
        self.assertEqual(plan.container, 'mkv')
        self.assertEqual(len(plan.subtitles), 3)

    async def test_explicit_stream_mapping_and_mov_text_conversion(self):
        process = Mock(returncode=0)
        process.stdout = asyncio.StreamReader(); process.stdout.feed_eof()
        process.stderr = asyncio.StreamReader(); process.stderr.feed_eof()
        process.wait = AsyncMock()
        with patch.object(formats, 'tool', return_value='ffmpeg'), patch.object(
                formats.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)) as run:
            await formats.convert({}, Path('source.mp4'), Path('out.part'),
                                  formats.plan_for(media(['mov_text', 'hdmv_pgs_subtitle']), ''), 1, Mock())
        args = run.call_args.args
        self.assertNotIn('-sn', args)
        self.assertIn('0:2', args); self.assertIn('0:3', args)
        self.assertEqual(args[args.index('-c:s:0') + 1], 'srt')
        self.assertEqual(args[args.index('-c:s:1') + 1], 'copy')

    async def test_real_ffmpeg_round_trip(self):
        ffmpeg = formats.tool({}, 'ffmpeg')
        if not ffmpeg or not formats.tool({}, 'ffprobe'):
            self.skipTest('FFmpeg/ffprobe not installed')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subtitle = root / 'captions.srt'
            subtitle.write_text('1\n00:00:00,000 --> 00:00:00,800\nHello movie world\n', encoding='utf-8')
            source = root / 'source.mkv'
            process = await asyncio.create_subprocess_exec(
                ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=64x64:rate=5:duration=1',
                '-f', 'lavfi', '-i', 'sine=duration=1', '-i', str(subtitle),
                '-map', '0:v', '-map', '1:a', '-map', '2:s', '-c:v', 'libx264', '-c:a', 'aac',
                '-c:s', 'srt', '-metadata:s:s:0', 'language=eng', str(source),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, error = await process.communicate()
            self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
            data = await formats.probe({}, source)
            # Force the same audio conversion used for a DTS movie.
            next(s for s in data['streams'] if s['codec_type'] == 'audio')['codec_name'] = 'dts'
            output = root / 'output.mkv'
            await formats.convert({}, source, output, formats.plan_for(data, ''), 1, Mock())
            result = await formats.probe({}, output)
            track = next(s for s in result['streams'] if s['codec_type'] == 'subtitle')
            self.assertEqual(track['codec_name'], 'subrip')
            self.assertEqual(track['tags']['language'], 'eng')
            process = await asyncio.create_subprocess_exec(ffmpeg, '-v', 'error', '-i', str(output),
                '-map', '0:s:0', '-f', 'srt', '-', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            text, _ = await process.communicate()
            self.assertIn(b'Hello movie world', text)


if __name__ == '__main__':
    unittest.main()
