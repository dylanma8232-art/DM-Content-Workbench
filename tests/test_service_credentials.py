"""Synthetic credentials and temporary roots only; never read real configuration."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import processing_settings
import service_credentials as credentials


class ServiceCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.client = self.new_client()

    def new_client(self):
        app = FastAPI()
        credentials.mount(app, self.root)
        return self.enterContext(TestClient(app))

    def post(self, kind='notion', **body):
        return self.client.post('/api/service-credentials/' + kind, json=body)

    def assert_metadata(self, result, notion=False, aliases=None):
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {'notion_saved': notion, 'api_aliases': aliases or []})
        self.assertNotIn('synthetic-secret', result.text)

    def test_empty_and_environment_metadata_only(self):
        self.assert_metadata(self.client.get('/api/service-credentials'))
        os.environ.update({
            'DM_NOTION_TOKEN': 'synthetic-secret-notion',
            'DM_API_TOKEN_Z': 'synthetic-secret-z',
            'DM_API_TOKEN_A': 'synthetic-secret-a',
            'DM_API_TOKEN_EMPTY': '',
            'DM_API_TOKEN_lower': 'synthetic-secret-invalid',
            'DM_API_TOKEN_': 'synthetic-secret-invalid',
            'DM_MODEL_NAME': 'private-model-name',
            'DM_MODEL_KEY': 'synthetic-secret-model',
        })
        result = self.client.get('/api/service-credentials')
        self.assert_metadata(result, True, ['DM_API_TOKEN_A', 'DM_API_TOKEN_Z'])
        self.assertNotIn('private-model-name', result.text)
        self.assertFalse((self.root / '.dm.env').exists())

    def test_persist_environment_permissions_and_restart(self):
        self.assert_metadata(self.post(token='synthetic-secret-notion'), True)
        self.assert_metadata(self.post('api', alias='DM_API_TOKEN_MAIN', token='synthetic-secret-api'),
                             True, ['DM_API_TOKEN_MAIN'])
        saved = dotenv_values(self.root / '.dm.env')
        self.assertEqual(saved, {'DM_NOTION_TOKEN': 'synthetic-secret-notion',
                                 'DM_API_TOKEN_MAIN': 'synthetic-secret-api'})
        for name, value in saved.items():
            self.assertEqual(os.environ[name], value)
        self.assertEqual((self.root / '.dm.env').stat().st_mode & 0o777, 0o600)
        os.environ.clear()
        self.assert_metadata(self.new_client().get('/api/service-credentials'), True, ['DM_API_TOKEN_MAIN'])

    def test_blank_and_omitted_tokens_preserve_without_writing(self):
        self.post(token='synthetic-secret-notion').raise_for_status()
        self.post('api', alias='DM_API_TOKEN_MAIN', token='synthetic-secret-api').raise_for_status()
        before = (self.root / '.dm.env').read_bytes()
        with patch.object(processing_settings, '_persist') as persist:
            for blank in ({}, {'token': ''}, {'token': '   '}):
                self.assert_metadata(self.post(**blank), True, ['DM_API_TOKEN_MAIN'])
                self.assert_metadata(self.post('api', alias='DM_API_TOKEN_MAIN', **blank),
                                     True, ['DM_API_TOKEN_MAIN'])
            self.assert_metadata(self.post('api', alias='DM_API_TOKEN_NEW', token=''), True, ['DM_API_TOKEN_MAIN'])
            persist.assert_not_called()
        self.assertEqual((self.root / '.dm.env').read_bytes(), before)
        self.assertEqual(os.environ['DM_NOTION_TOKEN'], 'synthetic-secret-notion')
        self.assertEqual(os.environ['DM_API_TOKEN_MAIN'], 'synthetic-secret-api')

    def test_replace_preserves_other_settings_and_aliases(self):
        (self.root / '.dm.env').write_text("# keep this\nDM_MODEL_NAME='private-model'\n", encoding='utf-8')
        self.post(token='synthetic-secret-old').raise_for_status()
        self.post('api', alias='DM_API_TOKEN_ONE', token='synthetic-secret-one').raise_for_status()
        self.post('api', alias='DM_API_TOKEN_TWO', token='synthetic-secret-two').raise_for_status()
        self.assert_metadata(self.post(token='synthetic-secret-new'), True,
                             ['DM_API_TOKEN_ONE', 'DM_API_TOKEN_TWO'])
        saved = dotenv_values(self.root / '.dm.env')
        self.assertEqual(saved['DM_NOTION_TOKEN'], 'synthetic-secret-new')
        self.assertEqual(saved['DM_MODEL_NAME'], 'private-model')
        self.assertEqual(saved['DM_API_TOKEN_ONE'], 'synthetic-secret-one')
        self.assertIn('# keep this', (self.root / '.dm.env').read_text())

    def test_invalid_fields_never_echo_or_mutate(self):
        self.post(token='synthetic-secret-existing').raise_for_status()
        before = (self.root / '.dm.env').read_bytes()
        environment = dict(os.environ)
        cases = [
            ('notion', {'token': None}), ('notion', {'token': 12}),
            ('notion', {'token': {'key': 'synthetic-secret'}}),
            ('notion', {'token': 'synthetic-secret\nINJECT=value'}),
            ('notion', {'token': 'synthetic-secret\x00'}),
            ('notion', {'token': 'synthetic-secret\x7f'}),
            ('notion', {'token': 'synthetic-secret' + 'x' * credentials._MAX_TOKEN}),
            ('notion', {'token': 'synthetic-secret', 'extra': 'synthetic-secret'}),
        ]
        for alias in (None, 7, 'DM_NOTION_TOKEN', 'DM_API_TOKEN_', 'DM_API_TOKEN_lower',
                      'DM_API_TOKEN_A\n', 'DM_API_TOKEN_' + 'A' * 100, 'synthetic-secret'):
            cases.append(('api', {'alias': alias, 'token': 'synthetic-secret'}))
        for kind, body in cases:
            with self.subTest(kind=kind, body=body):
                result = self.post(kind, **body)
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn('synthetic-secret', result.text)
                self.assertEqual(result.json(), {'detail': credentials._INVALID})
                self.assertEqual((self.root / '.dm.env').read_bytes(), before)
                self.assertEqual(dict(os.environ), environment)

    def test_malformed_json_and_non_objects_are_sanitized(self):
        for body in (b'{"token":"synthetic-secret",', b'\xffsynthetic-secret',
                     b'["synthetic-secret"]', b'"synthetic-secret"', b'null',
                     b'[' * 1100 + b'"synthetic-secret"' + b']' * 1100):
            result = self.client.post('/api/service-credentials/notion', content=body,
                                      headers={'Content-Type': 'application/json'})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn('synthetic-secret', result.text)
        self.assertFalse((self.root / '.dm.env').exists())

    def test_bounded_body_even_without_content_length(self):
        chunks = iter([b' ' * 8192, b' ' * 8192, b'synthetic-secret'])

        async def receive():
            return {'type': 'http.request', 'body': next(chunks), 'more_body': True}

        request = Request({'type': 'http', 'headers': []}, receive)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(credentials._form(request, api=False))
        self.assertEqual(raised.exception.status_code, 413)
        self.assertNotIn('synthetic-secret', raised.exception.detail)
        result = self.client.post('/api/service-credentials/api', content=b'x' * (credentials._MAX_BODY + 1))
        self.assertEqual(result.status_code, 413)
        self.assertFalse((self.root / '.dm.env').exists())

    def test_persist_return_is_discarded_and_error_is_sanitized(self):
        with patch.object(processing_settings, '_persist', return_value={'model_key': 'synthetic-secret-model'}):
            self.assert_metadata(self.post(token='synthetic-secret-notion'))
        with patch.object(processing_settings, '_persist', side_effect=HTTPException(500, 'synthetic-secret')):
            result = self.post(token='synthetic-secret-notion')
        self.assertEqual(result.status_code, 500)
        self.assertNotIn('synthetic-secret', result.text)

    def test_atomic_failure_preserves_file_and_environment(self):
        self.post(token='synthetic-secret-original').raise_for_status()
        before = (self.root / '.dm.env').read_bytes()
        environment = dict(os.environ)
        with patch.object(processing_settings.os, 'replace', side_effect=OSError('synthetic-secret')):
            result = self.post('api', alias='DM_API_TOKEN_MAIN', token='synthetic-secret-new')
        self.assertEqual(result.status_code, 500)
        self.assertNotIn('synthetic-secret', result.text)
        self.assertEqual((self.root / '.dm.env').read_bytes(), before)
        self.assertEqual(dict(os.environ), environment)
        self.assertFalse(list(self.root.glob('.dm.env-*')))


if __name__ == '__main__':
    unittest.main()
