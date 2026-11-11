"""algorithm.py 回归：与 V1.1 之前由 index.html 内 JS 求解器产出的基准逐方案比对。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # E:\EMB-01\S2标定计算器
sys.path.insert(0, str(ROOT / "标定软件V1.1" / "src"))

import algorithm as alg  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text(encoding="utf-8"))
DATA = ROOT / "测试数据"


class AlgorithmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = [alg.read_csv(str(DATA / n)) for n in GOLDEN["names"]]
        cls.out = alg.fit_all(cls.files, GOLDEN["ci"], GOLDEN["clum"])

    def test_trailing_comma_rows(self):
        self.assertGreater(len(self.files[0]), 700)

    def test_scheme_keys(self):
        self.assertEqual(list(self.out), alg.SCHEME_ORDER)

    def test_matches_js_golden(self):
        for name, want in GOLDEN["schemes"].items():
            got = self.out[name]
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, delta=abs(w) * 1e-9 + 1e-12,
                                       msg=f"{name} 偏差过大")

    def test_k_one(self):
        self.assertAlmostEqual(alg.k_one_rows(self.files[0].tolist(), 1),
                               GOLDEN["schemes"]["S1"][0], places=9)
        self.assertGreater(alg.k_one_rows(self.files[0].tolist(), 5), 0.0)


if __name__ == "__main__":
    unittest.main()
