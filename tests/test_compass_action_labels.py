#!/usr/bin/env python3
"""Unit tests for compass action labels and level validation."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts/compass_action_labels.py"


def load_module():
    spec = importlib.util.spec_from_file_location("compass_action_labels", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompassLabelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_module()

    def test_status_normalization(self):
        self.assertEqual(self.m.normalize_status("准备种花"), "候场")
        self.assertEqual(self.m.normalize_status("种花"), "伏击")
        self.assertEqual(self.m.normalize_status("准备摘花"), "止盈观察")
        self.assertEqual(self.m.normalize_status("摘花"), "兑现")
        self.assertEqual(self.m.normalize_status("候场"), "候场")

    def test_stage_normalization(self):
        self.assertEqual(self.m.normalize_stage("07:30早盘版"), "08:30盘前版")
        self.assertEqual(self.m.normalize_stage("08:30盘前版"), "08:30盘前版")
        self.assertEqual(self.m.normalize_stage("11:30上午收盘修正"), "11:30上午收盘修正")

    def test_levels_valid_rejects_dirty(self):
        ok, reason = self.m.levels_valid(1.0, 1.0, 2.0, -0.1)
        self.assertFalse(ok)
        ok, reason = self.m.levels_valid(1.0, 1.0, 2.0, 0.9)
        self.assertFalse(ok)
        ok, reason = self.m.levels_valid(1.29, 1.2, 2.34, 1.1)
        self.assertFalse(ok)
        ok, reason = self.m.levels_valid(0.78, 0.76, 0.80, 0.74)
        self.assertTrue(ok)

    def test_rewrite_garden_terms(self):
        text = self.m.rewrite_garden_terms("07:30准备信号：准备种花后分批摘花")
        self.assertIn("08:30准备信号", text)
        self.assertIn("候场", text)
        self.assertIn("兑现", text)
        self.assertNotIn("种花", text)
        self.assertNotIn("摘花", text)


if __name__ == "__main__":
    unittest.main()
