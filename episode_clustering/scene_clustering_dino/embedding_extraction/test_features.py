import tempfile
from pathlib import Path
import unittest
from PIL import Image
import numpy as np
import torch
from .preprocess import letterbox, normalize, pool_features


class FeatureTests(unittest.TestCase):
    def test_letterbox(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.png';Image.new('RGB',(640,480),'red').save(p)
            for size in [224,448]:
                image,w,meta=letterbox(p,size,16,[.485,.456,.406])
                self.assertEqual(image.size,(size,size))
                self.assertEqual(tuple(w.shape),(5,size//16,size//16))
                self.assertTrue(torch.allclose(w[1:].sum(0),w[0]))
                self.assertEqual(meta['padding'][0],0)
                self.assertAlmostEqual(w[0].sum().item()*256,size*size*.75)

    def test_registers_and_pooling(self):
        t=torch.ones(1+4+4,8);t[1:5]=1000
        result=pool_features(t,torch.ones(5,2,2),4,2,True)
        self.assertEqual(result['spatial_2x2'].shape,(32,))
        np.testing.assert_allclose(result['patch_mean'],np.ones(8)/np.sqrt(8),rtol=1e-6)
        for name in ['cls','patch_mean','spatial_2x2']:
            self.assertAlmostEqual(float(np.linalg.norm(result[name])),1,places=6)
        with self.assertRaises(ValueError):pool_features(t,torch.ones(5,2,2),3,2)

    def test_invalid_vectors(self):
        for t in [torch.zeros(8),torch.full((8,),float('nan')),torch.full((8,),float('inf'))]:
            with self.assertRaises(ValueError):normalize(t)


if __name__=='__main__':unittest.main()
