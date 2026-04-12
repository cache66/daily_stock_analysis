# -*- coding: utf-8 -*-
"""Tests for board theme-core snapshot helpers."""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.collect_board_theme_core_snapshots import (
    build_snapshot_dir,
    write_snapshot_manifest,
)


class BoardThemeCoreSnapshotsTestCase(unittest.TestCase):
    def test_build_snapshot_dir_namespaces_by_date_and_board(self):
        path = build_snapshot_dir(
            root=Path("data/board_theme_core_snapshots"),
            snapshot_date=date(2026, 4, 11),
            board_name="CPO",
        )
        self.assertIn("2026-04-11", str(path))
        self.assertTrue(str(path).endswith(os.path.join("2026-04-11", "CPO")))

    def test_write_snapshot_manifest_persists_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_snapshot_manifest(
                board_name="CPO",
                board_type="concept",
                commodity_hint="optical_fiber",
                snapshot_date=date(2026, 4, 11),
                universe_size=12,
                selected_count=3,
                output_dir=Path(tmpdir),
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn('"board_name": "CPO"', content)
            self.assertIn('"selected_count": 3', content)


if __name__ == "__main__":
    unittest.main()
