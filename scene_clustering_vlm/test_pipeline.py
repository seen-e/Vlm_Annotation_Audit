from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import yaml

from .api import credentials,request
from .run import roles,run_task,validate_config
from .schema import validate_round1,validate_round2


def first(ids,reps=3):
    return {'groups':[dict(local_group_id='g1',status='coherent' if len(ids)>1 else 'singleton',
        member_episode_ids=ids,representative_episode_ids=ids[:reps],common_features='桌面配置相同',distinguishing_features='主要对象一致')]}


def second(ids,status='coherent'):
    return {'groups':[dict(final_group_id='c1',source_group_ids=ids,status=status,common_features='配置一致',reason='代表图片支持合并')]}


class Tests(unittest.TestCase):
    def test_credentials_file_layouts(self):
        auth=dict(base_url='https://example.invalid/v1',api_key='TEST-SECRET',model='mock')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'credentials.yaml'
            for content in [auth,{'model':auth}]:
                path.write_text(yaml.safe_dump(content),encoding='utf-8')
                self.assertEqual(credentials({'credentials_file':str(path)}),auth)

    def config(self):
        cfg=yaml.safe_load(Path(__file__).with_name('config.yaml').read_text());cfg['api']['retry_delay_sec']=0;return cfg

    def test_first_validation(self):
        validate_round1(first(['a','b']),['a','b'],3)
        validate_round1(first(['a']),['a'],3)
        for bad in [first(['a']),first(['a','a']),first(['a','c'])]:
            with self.assertRaises(ValueError):validate_round1(bad,['a','b'],3)
        bad=first(['a','b']);bad['groups'][0]['representative_episode_ids']=['c']
        with self.assertRaises(ValueError):validate_round1(bad,['a','b'],3)

    def test_second_validation(self):
        candidates={'g1':dict(member_episode_ids=['a']),'g2':dict(member_episode_ids=['b'])}
        validate_round2(second(['g1','g2']),candidates)
        for bad in [second(['g1']),second(['g1','g1']),second(['g1','g3']),second(['g1','g2'],'uncertain')]:
            with self.assertRaises(ValueError):validate_round2(bad,candidates)

    def test_modes_and_config(self):
        self.assertEqual(roles('first_last'),['first','last'])
        for mode in ['first','last','first_last']:
            cfg=self.config();cfg['frame_mode']=mode;validate_config(cfg)
        cfg=self.config();cfg['max_images_per_request']=99
        with self.assertRaises(ValueError):validate_config(cfg)

    def test_exactly_two_rounds_and_full_expansion(self):
        for mode in ['first','last','first_last']:
            cfg=self.config();cfg.update(frame_mode=mode,episodes_per_batch=2,representatives_per_cluster=1)
            items=[dict(episode_id=str(i),task='task',images=[dict(episode_id=str(i),frame_role=role) for role in roles(mode)]) for i in range(4)]
            calls=[]
            def fake(folder,prompt,payload,images,cfg,auth,validator):
                calls.append((payload,images))
                obj=first([r['episode_id'] for r in payload['episodes']],1) if payload['round']==1 else second([g['group_id'] for g in payload['candidate_groups']])
                validator(obj);return obj
            with tempfile.TemporaryDirectory() as d:out=run_task(0,items,cfg,d,{},fake)
            self.assertEqual([p['round'] for p,_ in calls],[1,1,2])
            self.assertEqual(len(calls[-1][1]),2*len(roles(mode)))
            self.assertEqual({a['episode_id'] for a in out['assignments']},{'0','1','2','3'})
            self.assertEqual(out['final_group_count'],1)

    def test_round_two_capacity_never_splits(self):
        cfg=self.config();cfg.update(episodes_per_batch=2,max_images_per_request=4)
        items=[dict(episode_id=str(i),task='task',images=[dict(episode_id=str(i),frame_role=f) for f in ['first','last']]) for i in range(4)]
        calls=[]
        def fake(folder,prompt,payload,images,cfg,auth,validator):
            calls.append(payload['round']);obj=first([r['episode_id'] for r in payload['episodes']]);validator(obj);return obj
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Round two requires'):run_task(0,items,cfg,d,{},fake)
            self.assertTrue((Path(d)/'task_000/round1_candidates.json').exists())
        self.assertEqual(calls,[1,1])

    def test_transport_retry_cache_and_image_binding(self):
        cfg=self.config();auth=dict(api_key='TEST-SECRET',base_url='https://example.invalid/v1',model='mock')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'frame.png';Image.new('RGB',(640,480),'red').save(path)
            images=[dict(episode_id='a',frame_role='last',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())]
            calls=[]
            def fake(messages,auth,config):
                calls.append(messages)
                self.assertEqual(messages[1]['content'][1]['type'],'text')
                self.assertEqual(json.loads(messages[1]['content'][1]['text'])['frame_role'],'last')
                self.assertEqual(messages[1]['content'][2]['type'],'image_url')
                return ('{}','stop') if len(calls)==1 else (json.dumps(first(['a'])),'stop')
            validator=lambda obj:validate_round1(obj,['a'],3)
            folder=Path(d)/'request'
            request(folder,'prompt',{'episode_ids':['a']},images,cfg,auth,validator,transport=fake)
            request(folder,'prompt',{'episode_ids':['a']},images,cfg,auth,validator,transport=fake)
            self.assertEqual(len(calls),2)
            self.assertNotIn(auth['api_key'],(folder/'input.json').read_text())
            (folder/'output.json').write_text('{}',encoding='utf-8')
            request(folder,'prompt',{'episode_ids':['a']},images,cfg,auth,validator,transport=fake)
            self.assertEqual(len(calls),3)
            with self.assertRaisesRegex(ValueError,'inputs changed'):
                request(folder,'changed prompt',{'episode_ids':['a']},images,cfg,auth,validator,transport=fake)
            self.assertEqual(len(calls),3)


if __name__=='__main__':unittest.main()
