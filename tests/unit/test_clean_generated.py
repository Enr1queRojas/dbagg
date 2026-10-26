import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.clean_generated import clean


class CleanGeneratedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git = patch("scripts.clean_generated.tracked_files", return_value=set()).start()
        self.addCleanup(patch.stopall)

    def put(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
        return path

    def run_clean(self, apply=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return clean(self.root, apply=apply)

    def test_preview_then_apply_removes_only_generated_files(self):
        cached = self.put("dbagg/agent/__pycache__/service.cpython-312.pyc")
        active_cache = self.put("src/dbagg/__pycache__/config.cpython-312.pyc")
        self.put(".ruff_cache/cache")
        (self.root / "core").mkdir()
        preserved = [
            self.put(name)
            for name in (
                "src/dbagg/config.py",
                ".env",
                "config/prompts/backup/agent.md",
                "data/feedback.sqlite3",
                "reportes/report.html",
                "score_riesgo.html",
                ".venv/__pycache__/local.pyc",
                "dist/release.whl",
                "src/dbagg.egg-info/PKG-INFO",
            )
        ]
        preview = self.run_clean()
        self.assertIn(Path("dbagg"), preview)
        self.assertTrue(cached.exists())
        self.assertEqual(preview, self.run_clean(apply=True))
        self.assertFalse((self.root / "dbagg").exists())
        self.assertFalse(active_cache.exists())
        self.assertTrue(all(p.exists() for p in preserved))
        self.assertEqual(self.run_clean(apply=True), [])

    def test_legacy_folder_with_source_or_private_notes_is_preserved_whole(self):
        for name in ("dbagg/old.py", "core/notes.txt", "tests/__pycache__/notes.txt"):
            self.put(name)
        cached = self.put("dbagg/__pycache__/old.pyc")
        self.assertEqual(self.run_clean(apply=True), [])
        self.assertTrue(cached.exists())

    def test_tracked_generated_file_prevents_removing_parent(self):
        cached = self.put("dbagg/__pycache__/old.pyc")
        self.git.return_value = {cached.relative_to(self.root)}
        self.assertEqual(self.run_clean(apply=True), [])
        self.assertTrue(cached.exists())

    def test_symlinks_are_preserved_and_not_followed(self):
        target = self.put("data/__pycache__/keep.pyc").parent
        link = self.root / "dbagg"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable for this account")
        self.assertEqual(self.run_clean(apply=True), [])
        self.assertTrue(link.is_symlink())
        self.assertTrue((target / "keep.pyc").exists())

    def test_git_failure_does_not_delete_anything(self):
        cached = self.put("dbagg/__pycache__/old.pyc")
        self.git.side_effect = RuntimeError("Git unavailable")
        with self.assertRaises(RuntimeError):
            self.run_clean(apply=True)
        self.assertTrue(cached.exists())
