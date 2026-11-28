import base64
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import local_ai


class LocalAITests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(local_ai.app)

    def test_only_configured_models_and_local_origins(self):
        response=self.client.get('/v1/models')
        self.assertEqual(len(response.json()['data']),2)
        self.assertEqual(self.client.post('/v1/chat/completions',json={'model':'other','messages':[{'role':'user','content':'hello'}]}).status_code,404)
        self.assertEqual(self.client.post('/v1/chat/completions',headers={'Origin':'https://example.org'},json={'model':local_ai.VISION_MODEL,'messages':[{'role':'user','content':'hello'}]}).status_code,403)

    def test_chat_returns_real_adapter_output_and_reports_failure(self):
        form={'model':local_ai.VISION_MODEL,'messages':[{'role':'user','content':'hello'}]}
        with patch('local_ai.complete',return_value='真实引擎输出') as complete:
            response=self.client.post('/v1/chat/completions',json=form)
        self.assertEqual(response.json()['choices'][0]['message']['content'],'真实引擎输出')
        complete.assert_called_once()
        with patch('local_ai.complete',side_effect=RuntimeError('failure')):
            self.assertEqual(self.client.post('/v1/chat/completions',json=form).status_code,503)

    def test_schema_constrains_claim_refs_to_input_evidence(self):
        schema=local_ai.evidence_schema([{'role':'user','content':'{"evidence":{"body":"原文","frame:1":"画面"}}'}])
        refs=schema['properties']['claims']['items']['properties']['evidence_ids']['items']['enum']
        self.assertEqual(refs,['body','frame:1'])
        self.assertFalse(schema['additionalProperties'])

    def test_fake_ip_proxy_exception_is_limited_to_approved_hosts_and_range(self):
        import socket
        import connections
        from fastapi import HTTPException
        def addresses(ip):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
        with patch.dict('os.environ',{'DM_PUBLIC_PROXY_SUFFIXES':'bilibili.com','DM_RSS_TRUSTED_HOSTS':''}),patch('connections.socket.getaddrinfo',return_value=addresses('198.18.3.148')):
            connections.public_endpoint('https://www.bilibili.com/video/test')
            for address in ('https://bilibili.com.evil.test/','https://evil.test/','https://www.bilibili.com:8765/'):
                with self.assertRaises(HTTPException):connections.public_endpoint(address)
        for private in ('127.0.0.1','10.0.0.1','192.168.0.1','169.254.169.254'):
            with patch.dict('os.environ',{'DM_PUBLIC_PROXY_SUFFIXES':'bilibili.com','DM_RSS_TRUSTED_HOSTS':''}),patch('connections.socket.getaddrinfo',return_value=addresses(private)):
                with self.assertRaises(HTTPException):connections.public_endpoint('https://www.bilibili.com/')

    def test_upload_requires_configured_asr(self):
        response=self.client.post('/v1/audio/transcriptions',files={'file':('a.wav',b'audio','audio/wav')},data={'model':'other'})
        self.assertEqual(response.status_code,404)


if __name__=='__main__':
    unittest.main()
