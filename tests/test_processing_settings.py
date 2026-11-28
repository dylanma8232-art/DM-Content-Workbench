"""Settings tests use only a temporary root and synthetic credentials."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient

import processing_settings as settings


class ProcessingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.client = self.new_client()
        self.form = {'model_base': 'https://model.example/v1', 'model_name': 'vision',
                     'model_key': 'synthetic-model-secret', 'asr_key': 'synthetic-asr-secret'}

    def new_client(self):
        app = FastAPI()
        settings.mount(app, self.root)
        return self.enterContext(TestClient(app))

    def custom(self, **changes):
        return self.client.post('/api/processing-settings/custom', json={**self.form, **changes})

    def mock_engine(self, service='dm-local-ai', models=None):
        client = MagicMock()
        def get(url):
            data = {'service': service} if url.endswith('/health') else {
                'data': [{'id': model} for model in (models if models is not None else [settings.VISION_MODEL, settings.ASR_MODEL])]}
            return httpx.Response(200, json=data, request=httpx.Request('GET', url))
        client.get.side_effect = get
        return client

    def test_save_restart_and_secret_preservation(self):
        result = self.custom()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn('synthetic-', result.text)
        self.assertTrue(result.json()['model_key_saved'])
        self.assertEqual(result.json()['mode'], 'custom')
        self.assertEqual((self.root / '.dm.env').stat().st_mode & 0o777, 0o600)
        os.environ.clear()
        restarted = self.new_client()
        result = restarted.get('/api/processing-settings')
        self.assertEqual(result.json()['model_name'], 'vision')
        self.assertTrue(result.json()['model_key_saved'])
        form = {**self.form, 'model_key': None, 'asr_key': None}
        result = restarted.post('/api/processing-settings/custom', json=form)
        self.assertEqual(result.status_code, 200)
        saved = dotenv_values(self.root / '.dm.env')
        self.assertEqual(saved['DM_MODEL_KEY'], self.form['model_key'])
        self.assertEqual(saved['DM_ASR_KEY'], self.form['asr_key'])
        self.assertNotIn('synthetic-', result.text)

    def test_ocr_preferences_are_independent_and_survive_restart(self):
        self.custom().raise_for_status()
        response = self.client.post('/api/processing-settings/ocr', json={'enabled': True, 'language': 'chi_sim+eng'})
        response.raise_for_status()
        self.assertTrue(response.json()['model_key_saved'])
        os.environ.clear()
        restarted = self.new_client()
        status = restarted.get('/api/processing-settings').json()
        self.assertTrue(status['ocr_enabled'])
        self.assertEqual(status['ocr_language'], 'chi_sim+eng')
        self.assertEqual(restarted.post('/api/processing-settings/ocr', json={'enabled': False, 'language': 'a/b'}).status_code, 422)
        self.assertEqual(os.environ['DM_OCR_ENABLED'], '1')

    def test_changing_service_address_does_not_reuse_previous_key(self):
        self.custom(asr_base='https://asr.example/v1', asr_name='whisper').raise_for_status()
        result = self.custom(model_base='https://other.example/v1', model_key=None,
                             asr_base='https://asr.example/v1', asr_name='whisper', asr_key=None)
        result.raise_for_status()
        self.assertFalse(result.json()['model_key_saved'])
        self.assertTrue(result.json()['asr_key_saved'])

    def test_invalid_fields_do_not_mutate_or_echo_secrets(self):
        self.custom().raise_for_status()
        before = (self.root / '.dm.env').read_bytes()
        environment = dict(os.environ)
        cases = [
            {'model_base': 'http://public.example/v1'},
            {'model_base': 'https://user:synthetic-secret@public.example/v1'},
            {'model_base': 'https://public.example/v1?key=synthetic-secret'},
            {'model_base': 'https://public.example/#synthetic-secret'},
            {'model_base': 'https://192.168.1.2/v1'},
            {'model_base': 'http://127.0.0.1:bad/v1'},
            {'model_name': '  '}, {'asr_base': 'https://asr.example/v1'},
            {'asr_name': 'whisper'}, {'model_key': {'key': 'synthetic-secret'}},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.custom(**changes)
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn('synthetic-', result.text)
                self.assertEqual((self.root / '.dm.env').read_bytes(), before)
                self.assertEqual(dict(os.environ), environment)

    def test_loopback_custom_and_disable_asr(self):
        result = self.custom(model_base='http://[::1]:9000/v1/', asr_base='https://asr.example/v1', asr_name='whisper')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['model_base'], 'http://[::1]:9000/v1')
        result = self.custom(asr_base='', asr_name='', model_key='', asr_key='')
        self.assertFalse(result.json()['model_key_saved'])
        self.assertFalse(result.json()['asr_key_saved'])
        self.assertEqual(result.json()['asr_base'], '')

    def test_local_fixed_port_and_clear_keys(self):
        self.custom().raise_for_status()
        engine = self.mock_engine()
        with patch.object(settings.httpx, 'Client') as factory, patch.object(settings.subprocess, 'Popen') as spawn:
            factory.return_value.__enter__.return_value = engine
            result = self.client.post('/api/processing-settings/local')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['mode'], 'local')
        self.assertEqual(result.json()['model_base'], settings.LOCAL_BASE)
        self.assertEqual(result.json()['asr_name'], settings.ASR_MODEL)
        self.assertFalse(result.json()['model_key_saved'])
        self.assertFalse(result.json()['asr_key_saved'])
        self.assertEqual([call.args[0] for call in engine.get.call_args_list],
                         ['http://127.0.0.1:8770/health', settings.LOCAL_BASE + '/models'])
        spawn.assert_not_called()

    def test_wrong_service_or_models_never_persist_or_spawn(self):
        for engine in [self.mock_engine(service='other'), self.mock_engine(models=['unexpected'])]:
            with patch.object(settings.httpx, 'Client') as factory, patch.object(settings.subprocess, 'Popen') as spawn:
                factory.return_value.__enter__.return_value = engine
                result = self.client.post('/api/processing-settings/local')
            self.assertEqual(result.status_code, 503)
            self.assertFalse((self.root / '.dm.env').exists())
            spawn.assert_not_called()

    def test_missing_environment_actionable(self):
        engine = MagicMock()
        engine.get.side_effect = httpx.ConnectError('offline')
        with patch.object(settings.httpx, 'Client') as factory, patch.object(settings.subprocess, 'Popen') as spawn:
            factory.return_value.__enter__.return_value = engine
            result = self.client.post('/api/processing-settings/local')
        self.assertEqual(result.status_code, 503)
        self.assertIn('.venv-local-ai', result.text)
        spawn.assert_not_called()

    def test_launch_and_bounded_failure(self):
        python = self.root / '.venv-local-ai/bin/python'
        python.parent.mkdir(parents=True)
        python.touch(mode=0o700)
        healthy = self.mock_engine()
        for succeeds in (True, False):
            engine = MagicMock()
            calls = 0
            def get(url):
                nonlocal calls
                calls += 1
                if calls == 1 or not succeeds:
                    raise httpx.ConnectError('offline')
                return healthy.get(url)
            engine.get.side_effect = get
            with patch.object(settings.httpx, 'Client') as factory, patch.object(settings.subprocess, 'Popen') as spawn, patch.object(settings, 'time') as clock:
                clock.monotonic.side_effect = [0, 0, 5]
                factory.return_value.__enter__.return_value = engine
                result = self.client.post('/api/processing-settings/local')
            self.assertEqual(result.status_code, 200 if succeeds else 503, result.text)
            self.assertEqual(spawn.call_args.args[0], [str(python), '-m', 'uvicorn', 'local_ai:app', '--host', '127.0.0.1', '--port', '8770'])
            self.assertEqual(spawn.call_args.kwargs['cwd'], self.root)
            self.assertTrue((self.root / '.dm-data/engine-service.log').exists())

    def test_atomic_failure_does_not_change_environment(self):
        self.custom().raise_for_status()
        before = (self.root / '.dm.env').read_bytes()
        environment = dict(os.environ)
        with patch.object(settings.os, 'replace', side_effect=OSError('synthetic-secret')):
            result = self.custom(model_name='changed')
        self.assertEqual(result.status_code, 500)
        self.assertNotIn('synthetic-', result.text)
        self.assertEqual((self.root / '.dm.env').read_bytes(), before)
        self.assertEqual(dict(os.environ), environment)
        self.assertFalse(list(self.root.glob('.dm.env-*')))


if __name__ == '__main__':
    unittest.main()
