import unittest
import numpy as np
from .run import combine,cluster,norm,embedding

class Tests(unittest.TestCase):
    def test_weights(self):
        rng=np.random.default_rng(42);a=norm(rng.normal(size=(10,8)));b=norm(rng.normal(size=(10,8)))
        x=combine(a,b)
        np.testing.assert_allclose(x@x.T,.5*(a@a.T+b@b.T),atol=1e-6)
    def test_sizes(self):
        rng=np.random.default_rng(1)
        for n in [1,3,10,100]:
            labels,p,state=cluster(norm(rng.normal(size=(n,12))))
            self.assertEqual(len(labels),n);self.assertEqual(len(p),n)
    def test_invalid(self):
        with self.assertRaises(ValueError):norm(np.zeros((2,8)))
    def test_last_frame_only(self):
        last=np.arange(1,9,dtype=np.float32)
        a=embedding(np.zeros(8),last,'last_cls')
        b=embedding(np.full(8,np.nan),last,'last_cls')
        np.testing.assert_array_equal(a,b)
        np.testing.assert_allclose(a,norm(last))
        self.assertEqual(a.shape,(8,))

if __name__=='__main__':unittest.main()
