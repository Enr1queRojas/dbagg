import tempfile
import unittest
from pathlib import Path

from scripts.package_deploy import check_contents, private_config


class PackageDeployTests(unittest.TestCase):
    def test_private_config_leaves_out_backups_and_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in (
                "config/llm.toml",
                "config/README.md",
                "config/prompts/agent.md",
                "config/prompts/backup/old.md",
                ".env",
                "data/outbox.sqlite3",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
            names = [path.relative_to(root).as_posix() for path in private_config(root)]
        self.assertEqual(names, ["config/llm.toml", "config/prompts/agent.md"])

    def test_secrets_and_stored_conversations_are_refused(self):
        for name in (".env", ".env.local", "config/.env", "data/x", "outbox.sqlite3"):
            with self.subTest(name), self.assertRaises(ValueError):
                check_contents([name])

    def test_the_bundle_contents_are_accepted(self):
        check_contents(
            [
                ".env.example",
                "docker-compose.yml",
                "Caddyfile",
                "DEPLOY.md",
                "images.tar",
                "config/llm.toml",
                "config/prompts/agent-v7.md",
            ]
        )


if __name__ == "__main__":
    unittest.main()
