import os
from unittest.mock import patch

import httpx

from test_workbench import WorkbenchFixture


class WorkflowSettingsTests(WorkbenchFixture):
    def test_project_rules_roundtrip_and_execution(self):
        settings={'dedup_mode':'off','dedup_threshold':.9,'exclude_terms':'无关广告',
                  'remove_links':True,'analysis_engine':'evidence','evidence_count':2,
                  'analysis_max_tokens':1600,'topic_limit':3,'min_topic_sources':1,
                  'target_words':600,'writing_requirements':'保留引用','outline':'背景\n发现',
                  'generation_max_tokens':1600}
        other=self.client.post('/api/projects',json={'name':'独立项目'}).json()['id']
        def other_settings():
            return next(project['settings'] for project in self.client.get('/api/projects').json() if project['id']==other)
        original=other_settings()
        saved=self.client.put('/api/projects/1',json=settings)
        self.assertEqual(other_settings(),original)
        self.assertEqual(saved.status_code,200,saved.text)
        for key,value in settings.items():self.assertEqual(saved.json()[key],value)
        run=self.create()
        cleaned=self.client.post(f'/api/runs/{run}/advance',json={'stage':1}).json()
        self.assertEqual([row['reason'] for row in cleaned['payload']['cleaned']],['保留','保留'])
        research=self.client.post(f'/api/runs/{run}/advance',json={'stage':2})
        self.assertEqual(research.status_code,200,research.text)
        self.assertEqual(research.json()['payload']['research']['engine'],'evidence')
        self.assertLessEqual(len(research.json()['payload']['recommendations']),3)

    def test_manual_cleaning_preserves_original_and_checks_revision(self):
        run=self.create()
        path=f'/api/runs/{run}'
        before=self.client.get(path).json()['payload']['materials']
        self.client.post(path+'/advance',json={'stage':1}).raise_for_status()
        result=self.client.put(path+'/cleaning',json={'revision':0,'decisions':{'2':'keep'}})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['payload']['cleaned'][1]['reason'],'保留')
        self.assertEqual(result.json()['payload']['materials'],before)
        self.assertEqual(self.client.put(path+'/cleaning',json={'revision':0,'decisions':{'1':'exclude'}}).status_code,409)
        self.assertEqual(self.client.put(path+'/cleaning',json={'revision':1,'decisions':{'999':'keep'}}).status_code,422)
        result=self.client.put(path+'/cleaning',json={'revision':1,'decisions':{'2':'auto'}}).json()
        self.assertEqual(result['payload']['cleaned'][1]['reason'],'正文重复')
        self.client.post(path+'/advance',json={'stage':2}).raise_for_status()
        self.assertEqual(self.client.put(path+'/cleaning',json={'revision':2,'decisions':{'2':'keep'}}).status_code,409)

    def test_model_failure_keeps_progress_and_credentials_out_of_payload(self):
        self.client.put('/api/projects/1',json={'analysis_engine':'model'}).raise_for_status()
        run=self.create();path=f'/api/runs/{run}'
        self.client.post(path+'/advance',json={'stage':1}).raise_for_status()
        before=self.client.get(path).json()
        response=httpx.Response(200,request=httpx.Request('POST','https://example.org/v1/chat/completions'),
            json={'choices':[{'message':{'content':'{"summary":[{"text":"无效","refs":["不存在"]}],"material_findings":[],"gaps":[],"conflicts":[]}'}}]})
        with patch.dict(os.environ,{'DM_MODEL_BASE_URL':'https://example.org/v1','DM_MODEL_NAME':'fixture','DM_MODEL_KEY':'private-fixture-key'}),patch('workflow_engine.httpx.post',return_value=response):
            failed=self.client.post(path+'/advance',json={'stage':2})
        self.assertEqual(failed.status_code,502,failed.text)
        state=self.client.get(path)
        self.assertEqual(state.json()['stage'],2)
        self.assertEqual(state.json()['payload'],before['payload'])
        self.assertNotIn('private-fixture-key',state.text)
        self.assertNotIn('model_base',state.json()['payload']['config'])

    def test_topic_threshold_cannot_be_bypassed_by_old_candidate(self):
        self.client.put('/api/projects/1',json={'min_topic_sources':2}).raise_for_status()
        run=self.create();path=f'/api/runs/{run}'
        self.client.post(path+'/advance',json={'stage':1}).raise_for_status()
        data=self.client.post(path+'/advance',json={'stage':2}).json()['payload']
        self.assertEqual(data['recommendations'],[])
        self.assertEqual(self.client.post(path+'/advance',json={'stage':3,'candidate_key':'1'}).status_code,422)

    def test_per_draft_options_are_saved_with_output(self):
        run=self.create();path=f'/api/runs/{run}'
        for stage in (1,2):self.client.post(path+'/advance',json={'stage':stage}).raise_for_status()
        self.client.post(path+'/advance',json={'stage':3,'candidate_key':'1'}).raise_for_status()
        result=self.client.post(path+'/advance',json={'stage':4,'engine':'local','target_words':500,
            'writing_requirements':'不要补造数字','outline':'材料发现\n仍需核实'})
        self.assertEqual(result.status_code,200,result.text)
        payload=result.json()['payload']
        self.assertEqual(payload['config']['target_words'],500)
        self.assertIn('不要补造数字',payload['drafts'][0])
        self.assertIn('材料发现',payload['drafts'][0])
        self.assertIn('来源：',payload['drafts'][0])

    def test_source_configuration_edit_and_removal_keep_materials(self):
        run=self.create()
        form={'project_id':1,'name':'资料源','kind':'api','address':'https://example.org/feed',
              'title_field':'title','body_field':'body','minutes':0,'records_path':'items'}
        source=self.client.post('/api/sources',json=form).json()['id']
        changed={**form,'name':'已修改资料源','records_path':'data.items','minutes':60}
        response=self.client.put(f'/api/sources/{source}',json=changed)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/sources?project_id=1').json()[0]['records_path'],'data.items')
        other=self.client.post('/api/projects',json={'name':'另一个项目'}).json()['id']
        self.assertEqual(self.client.put(f'/api/sources/{source}',json={**changed,'project_id':other}).status_code,404)
        self.assertEqual(self.client.delete(f'/api/sources/{source}?project_id={other}').status_code,404)
        removed=self.client.delete(f'/api/sources/{source}?project_id=1')
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertTrue(removed.json()['materials_retained'])
        self.assertEqual(self.client.get(f'/api/runs/{run}').status_code,200)
