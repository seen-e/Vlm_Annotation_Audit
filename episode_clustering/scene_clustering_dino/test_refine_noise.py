from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest

from .refine_noise import combine_task, publish, read_dino, select_noise


def fixture():
    return dict(task_id=0, task='Task', episode_count=4, cluster_count=1, noise_count=2,
                noise_ratio=.5, group_status='uncertain', cross_pairs=[],
                clusters=[dict(cluster_id=0, size=2, episode_ids=['a', 'b'], medoid_episode_id='a')],
                episodes=[dict(episode_id=e, cluster_id=0 if e in ('a', 'b') else -1,
                               image=f'images/{e}.jpg', center=e == 'a', boundary=False,
                               medoid=e == 'a', cross=False, medoid_similarity=1 if e == 'a' else None,
                               membership_strength=.8) for e in 'abcd'])


def prediction(groups=None):
    return dict(task_id=0, task='Task', groups=groups or [dict(cluster_id='t000_c000', status='coherent',
                member_episode_ids=['c', 'd'], common_features='Visible objects', reason='Representative evidence',
                source_group_ids=['t000_b000_g000'])])


class NoiseTests(unittest.TestCase):
    def test_only_noise_selected(self):
        rows = [dict(episode_id=e, task='Task', images=[e]) for e in 'abcd']
        got = select_noise([fixture()], {0:rows})
        self.assertEqual([r['episode_id'] for r in got[0]], ['c', 'd'])
        with self.assertRaises(ValueError):
            select_noise([fixture()], {0:rows[:-1]})
        rows[-1]['task'] = 'Wrong task'
        with self.assertRaises(ValueError):
            select_noise([fixture()], {0:rows})

    def test_existing_clusters_preserved(self):
        source = fixture()
        before = deepcopy(source)
        result = combine_task(source, prediction(), [dict(representative_episode_ids=['c'])])
        self.assertEqual(source, before)
        self.assertEqual(result['clusters'][0]['episode_ids'], ['a', 'b'])
        for old, new in zip(source['episodes'][:2], result['episodes'][:2]):
            self.assertEqual(new['dino_cluster_id'], old['cluster_id'])
            self.assertEqual(new['cluster_id'], 'dino:t000:c0')
            for key in old.keys() - {'cluster_id'}:
                self.assertEqual(new[key], old[key])
        self.assertEqual(result['cluster_count'], 2)
        self.assertEqual(result['noise_count'], 0)
        self.assertTrue(result['episodes'][2]['center'])
        self.assertFalse(result['episodes'][3]['center'])
        self.assertEqual(result['clusters'][1]['source'], 'vlm')

    def test_vlm_cannot_touch_dino_or_lose_members(self):
        for ids in (['a', 'c'], ['c'], ['c', 'c'], ['c', 'd', 'x']):
            p = prediction()
            p['groups'][0]['member_episode_ids'] = ids
            with self.assertRaises(ValueError):
                combine_task(fixture(), p)

    def test_failed_task_preserves_noise(self):
        result = combine_task(fixture())
        self.assertEqual(result['noise_count'], 2)
        self.assertEqual(result['vlm_cluster_count'], 0)
        self.assertTrue(all(e['cluster_id'] == -1 for e in result['episodes'][2:]))

    def test_uncertain_not_hidden(self):
        p = prediction()
        p['groups'][0]['status'] = 'uncertain'
        result = combine_task(fixture(), p)
        self.assertEqual(result['vlm_uncertain_count'], 2)
        self.assertEqual(result['clusters'][1]['status'], 'uncertain')

    def test_no_noise_skips_selection(self):
        source = fixture()
        source['episodes'] = source['episodes'][:2]
        self.assertEqual(select_noise([source], {}), {})
        result = combine_task(source)
        self.assertEqual(result['vlm_cluster_count'], 0)
        self.assertEqual(result['noise_count'], 0)

    def test_publish_side_by_side_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'source', Path(tmp)/'output'
            (source/'task_000').mkdir(parents=True)
            (source/'viewer/images').mkdir(parents=True)
            original = json.dumps(fixture())
            (source/'task_000/clusters.json').write_text(original)
            (source/'run_config.json').write_text(json.dumps(dict(feature_mode='dual_cls')))
            for e in 'abcd':
                (source/f'viewer/images/{e}.jpg').write_bytes(b'placeholder')
            tasks = read_dino(source)
            (output/'vlm/task_000').mkdir(parents=True)
            (output/'vlm/task_000/round1_candidates.json').write_text('[]')
            summary = publish(output, source, tasks, {0:prediction()}, [], dict(frame_mode='last'), time.time())
            self.assertEqual(summary['dino_clusters'], 1)
            self.assertEqual(summary['vlm_clusters'], 1)
            self.assertEqual(summary['episodes'], 4)
            self.assertEqual(summary['noise'], 0)
            self.assertEqual(summary['vlm_frame_mode'], 'last')
            self.assertEqual((source/'task_000/clusters.json').read_text(), original)
            self.assertTrue((output/'viewer/index.html').exists())
            self.assertEqual(len(list((output/'viewer/images').iterdir())), 4)


if __name__ == '__main__':
    unittest.main()
