import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import content_media as media

PNG = b'\x89PNG\r\n\x1a\nmock-raster'


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.deadline = time.monotonic() + 30
        self.env = patch.dict(os.environ, {'DM_MODEL_BASE_URL': '', 'DM_MODEL_NAME': ''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def response(self, chunks, content_type='image/png', status=200, length=None):
        response = Mock(status_code=status)
        response.headers = {'content-type': content_type}
        if length is not None:
            response.headers['content-length'] = str(length)
        response.iter_bytes.return_value = iter(chunks)
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return context

    def test_bounded_fetch_validates_public_endpoint_and_rejects_redirects(self):
        with patch('content_media.connections.public_endpoint') as public, patch(
                'content_media.httpx.stream', return_value=self.response([PNG])) as stream:
            self.assertEqual(media.download_image('https://images.example/1.png', self.deadline), (PNG, 'image/png'))
        public.assert_called_once_with('https://images.example/1.png')
        self.assertFalse(stream.call_args.kwargs['follow_redirects'])
        with patch('content_media.connections.public_endpoint'), patch(
                'content_media.httpx.stream', return_value=self.response([], status=302)):
            with self.assertRaises(ValueError):
                media.bounded_fetch('https://images.example/1', 100, self.deadline)

    def test_stream_and_announced_size_limits(self):
        for response in (self.response([b'123', b'456']), self.response([], length=6)):
            with self.subTest(response=response), patch('content_media.connections.public_endpoint'), patch(
                    'content_media.httpx.stream', return_value=response):
                with self.assertRaises(ValueError):
                    media.bounded_fetch('https://images.example/1', 5, self.deadline)

    def test_private_endpoint_rejected_before_network(self):
        with patch('content_media.connections.public_endpoint', side_effect=ValueError('private')), patch(
                'content_media.httpx.stream') as stream:
            with self.assertRaises(ValueError):
                media.download_image('https://127.0.0.1/image', self.deadline)
        stream.assert_not_called()
        with self.assertRaises(ValueError):
            media.public_url('http://images.example/1')

    def test_svg_and_disguised_images_are_rejected(self):
        with patch('content_media.bounded_fetch', return_value=(b'<svg/>', 'image/png')):
            with self.assertRaises(ValueError):
                media.download_image('https://images.example/file.png', self.deadline)
        with patch('content_media.bounded_fetch', return_value=(PNG, 'image/svg+xml')):
            with self.assertRaises(ValueError):
                media.download_image('https://images.example/file', self.deadline)
        with patch('content_media.bounded_fetch') as fetch:
            with self.assertRaises(ValueError):
                media.download_image('https://images.example/file.svg', self.deadline)
        fetch.assert_not_called()

    def test_ocr_uses_temporary_image_and_configured_language(self):
        paths = []

        def run(command, **kwargs):
            paths.append(Path(command[1]))
            self.assertEqual(paths[0].read_bytes(), PNG)
            self.assertEqual(command[-2:], ['-l', 'chi_sim'])
            self.assertLessEqual(kwargs['timeout'], 40)
            return Mock(stdout=' recognized text\n')

        with patch('content_media.shutil.which', return_value='/bin/tesseract'), patch(
                'content_media.subprocess.run', side_effect=run), patch.dict(os.environ, {'DM_OCR_LANG': 'chi_sim'}):
            self.assertEqual(media.ocr_image(PNG, self.deadline), 'recognized text')
        self.assertFalse(paths[0].exists())

    def test_image_order_failure_isolation_and_original_text(self):
        item = {'body': 'original caption', 'image_urls': ['https://img.example/a', 'https://img.example/b', 'https://img.example/c']}
        with patch('content_media.download_image', side_effect=[(PNG, 'image/png'), ValueError('network'), (PNG, 'image/png')]), patch(
                'content_media.ocr_image', side_effect=['first text', 'third text']):
            result = media.enrich_media('xiaohongshu', item, self.deadline)
        self.assertEqual([row['index'] for row in result['image_texts']], [1, 2, 3])
        self.assertEqual([row['text'] for row in result['image_texts']], ['first text', '', 'third text'])
        self.assertEqual(result['body'], 'original caption')
        self.assertNotIn('image_texts', item)
        self.assertEqual(result['media_status'], 'partial')
        self.assertIn('image_understanding_unavailable', result['missing'])
        json.dumps(result)  # Evidence contains no binary payloads or temporary files.

    def test_vision_uses_configured_multimodal_endpoint(self):
        payload = {'choices': [{'message': {'content': 'A visible chart.'}}]}
        with patch.dict(os.environ, {'DM_MODEL_BASE_URL': 'https://model.example/v1', 'DM_MODEL_NAME': 'vision', 'DM_MODEL_KEY': 'secret'}), patch(
                'content_media.bounded_fetch', return_value=(json.dumps(payload).encode(), 'application/json')) as fetch:
            self.assertEqual(media.describe_image(PNG, self.deadline), 'A visible chart.')
        self.assertEqual(fetch.call_args.args[0], 'https://model.example/v1/chat/completions')
        options = fetch.call_args.kwargs
        self.assertEqual(options['method'], 'POST')
        self.assertEqual(options['headers']['Authorization'], 'Bearer secret')
        parts = options['json']['messages'][1]['content']
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_ffmpeg_extracts_four_timestamps_with_local_protocols(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[0].endswith('ffprobe'):
                return Mock(stdout='{"format":{"duration":"100"}}')
            Path(command[-1]).write_bytes(PNG)
            return Mock()

        with tempfile.TemporaryDirectory() as folder, patch('content_media.shutil.which', side_effect=lambda name: '/bin/' + name), patch(
                'content_media.subprocess.run', side_effect=run):
            rows = media.extract_frames(Path(folder) / 'video.mp4', folder, self.deadline)
            self.assertEqual([row[0] for row in rows], [10, 35, 60, 85])
            self.assertTrue(all(path.read_bytes() == PNG for _, path in rows))
        self.assertTrue(all('file,pipe' in command for command in calls))

    def test_video_resolves_with_ytdlp_and_downloads_with_hard_cap(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        context = Mock()
        context.__enter__ = Mock(return_value=process)
        context.__exit__ = Mock(return_value=False)

        def popen(command, **kwargs):
            self.assertIn('--ignore-config', command)
            self.assertIn('--no-playlist', command)
            kwargs['stdout'].write(json.dumps({'protocol': 'https', 'ext': 'mp4', 'url': 'https://cdn.example/video.mp4'}).encode())
            kwargs['stdout'].flush()
            return context

        with tempfile.TemporaryDirectory() as folder, patch('content_media.public_url'), patch(
                'content_media.shutil.which', return_value='/bin/yt-dlp'), patch(
                'content_media.subprocess.Popen', side_effect=popen), patch(
                'content_media.bounded_fetch', return_value=(b'video', 'video/mp4')) as fetch:
            path = media.download_video('https://www.bilibili.com/video/BV1', folder, self.deadline)
            self.assertEqual(path.read_bytes(), b'video')
        self.assertEqual(fetch.call_args.args[1], 24_000_000)

    def test_expired_ytdlp_process_is_killed(self):
        process = Mock(returncode=None)
        process.poll.side_effect = [None, None]
        context = Mock()
        context.__enter__ = Mock(return_value=process)
        context.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder, patch('content_media.public_url'), patch(
                'content_media.shutil.which', return_value='/bin/yt-dlp'), patch(
                'content_media.subprocess.Popen', return_value=context):
            with self.assertRaises(TimeoutError):
                media.download_video('https://videos.example/1', folder, time.monotonic() - 1)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with()

    def test_video_collects_speech_first_and_keeps_timed_ocr_without_vision(self):
        events = []
        item = {'url': 'https://www.bilibili.com/video/BV1', 'body': 'original'}

        def speech(item, deadline):
            events.append('speech')
            return dict(item, body='spoken words', content_level='transcript', segments=[])

        def download(url, folder, deadline):
            events.append('video')
            return Path(folder) / 'video.mp4'

        def frames(path, folder, deadline):
            target = Path(folder) / 'frame.png'
            target.write_bytes(PNG)
            return [(1.5, target), (4.5, target), (7.5, target)]

        with patch('content_media.shutil.which', return_value='/mock/ffmpeg'), patch('content_media.public_url'), patch('content_media.collection_depth.media_text', side_effect=speech), patch(
                'content_media.download_video', side_effect=download), patch('content_media.extract_frames', side_effect=frames), patch(
                'content_media.ocr_image', return_value='visible slide text'), patch('content_media.describe_image') as vision:
            result = media.enrich_media('bilibili', item, self.deadline)
        self.assertEqual(events, ['speech', 'video'])
        self.assertEqual(result['description_text'], 'original')
        self.assertEqual(result['body'], 'spoken words')
        self.assertEqual(result['transcript_text'], 'spoken words')
        self.assertEqual(result['visual_frames'][0], {'time': 1.5, 'text': 'visible slide text', 'status': 'partial'})
        vision.assert_not_called()
        self.assertNotIn('base64', json.dumps(result))

    def test_cached_keyframe_can_be_reanalyzed_without_redownloading_video(self):
        cache=Mock()
        cache.get.return_value=(PNG,'image/png','a'*64)
        cache.put.return_value='a'*64
        item={'url':'https://www.bilibili.com/video/BV1','body':'原话','content_level':'subtitle',
              'visual_frames':[{'time':2.5,'text':'已有 OCR','status':'partial'}],
              'frame_assets':[{'time':2.5,'id':'a'*64}]}
        with patch('content_media.public_url'),patch('content_media.shutil.which',return_value='/mock/ffmpeg'),patch('content_media.download_video') as download,patch('content_media.extract_image',return_value=('画面解析已补齐','ok','')):
            result=media.enrich_media('bilibili',item,self.deadline,cache=cache)
        download.assert_not_called()
        self.assertEqual(result['visual_frames'][0]['text'],'画面解析已补齐')
        self.assertEqual(result['frame_assets'][0]['id'],'a'*64)

    def test_retry_keeps_successful_frames_when_new_download_fails(self):
        frame={'time':1.5,'text':'已有画面识别结果','status':'partial'}
        item={'url':'https://www.bilibili.com/video/BV1','body':'原话','content_level':'subtitle','visual_frames':[frame]}
        with patch('content_media.public_url'),patch('content_media.shutil.which',return_value='/mock/ffmpeg'),patch('content_media.download_video',side_effect=RuntimeError('network')):
            result=media.enrich_media('bilibili',item,self.deadline)
        self.assertEqual(result['visual_frames'],[frame])
        self.assertIn('video_frames_unavailable',result['missing'])

    def test_deadline_preserves_original_and_does_not_run_ocr(self):
        with patch('content_media.httpx.stream') as stream, patch('content_media.connections.public_endpoint'), patch('content_media.subprocess.run') as run:
            result = media.enrich_media('x', {'body': 'keep', 'image_urls': ['https://images.example/a']}, time.monotonic() - 1)
        self.assertEqual(result['body'], 'keep')
        self.assertEqual(result['media_status'], 'unavailable')
        stream.assert_not_called()
        run.assert_not_called()

    def test_empty_and_bad_image_inputs(self):
        self.assertEqual(media.enrich_media('x', {'body': 'text'}, self.deadline)['media_status'], 'not_applicable')
        self.assertIn('invalid_image_urls', media.enrich_media('x', {'image_urls': 'bad'}, self.deadline)['missing'])

    def test_fingerprint_ignores_errors_and_tracks_evidence(self):
        first = {'body': 'caption', 'image_texts': [{'index': 1, 'text': 'text', 'status': 'ok', 'error': ''}]}
        second = {'body': 'caption', 'missing': ['network'], 'media_status': 'partial',
                  'image_texts': [{'index': 1, 'text': 'text', 'status': 'partial', 'error': 'timeout'},
                                  {'index': 2, 'text': '', 'status': 'missing', 'error': 'network'}]}
        self.assertEqual(media.media_fingerprint(first), media.media_fingerprint(second))
        second['image_texts'][0]['text'] = 'new evidence'
        self.assertNotEqual(media.media_fingerprint(first), media.media_fingerprint(second))

    def test_text_and_audio_do_not_request_video_frames(self):
        from content_pipeline import media_report
        item = {'url':'https://www.bilibili.com/video/av1','body':'简介','content_level':'detail_text'}
        with patch.object(media,'public_url'), patch.object(media.collection_depth,'media_text',return_value={'body':'  ','content_level':'transcript'}) as speech, patch.object(media,'download_video') as video:
            plain = media.enrich_media('bilibili', dict(item,media_kind='text'), self.deadline)
            speech.assert_not_called()
            self.assertEqual(media_report(plain)['parts'], {'正文': True})
            audio = media.enrich_media('bilibili', dict(item,media_kind='audio'), self.deadline)
            speech.assert_called_once()
            video.assert_not_called()
            self.assertIn('speech_unavailable',audio['missing'])
            self.assertEqual(media_report(audio)['parts'], {'正文': True,'字幕或转写':False})


if __name__ == '__main__':
    unittest.main()
