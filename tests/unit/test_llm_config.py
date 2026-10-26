import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.agent.prompts import build_messages, tool_definitions
from dbagg.config import Settings
from dbagg.llm.__main__ import initialize
from dbagg.llm.config import load_llm_config


class LLMConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def initialize(self):
        with patch.dict(os.environ, {}, clear=True):
            initialize(self.root)
        return self.root / "config" / "llm.toml"

    def test_no_config_preserves_legacy_openai_model(self):
        config = load_llm_config(self.root, {"OPENAI_MODEL": "my-openai-model"})
        self.assertEqual(config.profile.model, "my-openai-model")
        self.assertEqual(config.profile.provider, "openai")
        self.assertIsNone(config.system_prompt)

    def test_file_wins_over_legacy_model_and_explicit_profile_wins_over_default(self):
        path = self.initialize()
        path.write_text(
            path.read_text().replace('active_profile = "openai"', 'active_profile = "glm"')
        )
        config = load_llm_config(self.root, {"OPENAI_MODEL": "ignored"})
        self.assertEqual(config.profile.provider, "glm")
        config = load_llm_config(self.root, {"LLM_PROFILE": "claude"})
        self.assertEqual(config.profile.provider, "anthropic")
        self.assertEqual(config.profile.api_key_env, "ANTHROPIC_API_KEY")
        self.assertEqual(config.system_prompt, self.root / "config/prompts/agent.md")

    def test_init_preserves_legacy_model_and_never_overwrites_files(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "my-model"}, clear=True):
            initialize(self.root)
        self.assertEqual(load_llm_config(self.root, {}).profile.model, "my-model")
        target = self.root / "config/prompts/agent.md"
        target.write_text("Custom instructions")
        with self.assertRaisesRegex(ValueError, "conservaron"):
            initialize(self.root)
        self.assertEqual(target.read_text(), "Custom instructions")

    def test_explicit_missing_config_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, "LLM_CONFIG"):
            load_llm_config(self.root, {"LLM_CONFIG": "missing.toml"})

    def test_relative_custom_config_resolves_prompts_from_its_directory(self):
        path = self.initialize()
        config = load_llm_config(self.root, {"LLM_CONFIG": str(path.relative_to(self.root))})
        self.assertTrue(config.tool_descriptions.is_file())

    def test_invalid_config_and_unknown_keys_fail_without_exposing_values(self):
        path = self.initialize()
        original = path.read_text()
        cases = [
            original + '\napi_key = "private-value"\n',
            original.replace('provider = "openai"', 'provider = "unsupported"'),
            original.replace("max_retries = 1", "max_retries = 99"),
            original.replace("temperature = 0.0", "temperature = nan"),
            original.replace(
                "https://api.openai.com/v1", "https://user:private-value@api.openai.com/v1"
            ),
            original.replace("https://api.openai.com/v1", "http://api.openai.com/v1"),
            original.replace('active_profile = "openai"', 'active_profile = "missing"'),
            original.replace('thinking = { type = "disabled" }', "messages = []"),
            "[ invalid private-value",
        ]
        for value in cases:
            path.write_text(value)
            with self.subTest(case=cases.index(value)), self.assertRaises(ValueError) as exc:
                load_llm_config(self.root, {})
            self.assertNotIn("private-value", str(exc.exception))

    def test_only_selected_provider_key_is_required(self):
        self.initialize()
        variables = {
            "LLM_PROFILE": "claude",
            "ANTHROPIC_API_KEY": "synthetic-secret",
            "META_ACCESS_TOKEN": "test",
            "META_APP_SECRET": "test",
            "META_VERIFY_TOKEN": "test",
            "META_PHONE_NUMBER_ID": "123",
            "META_GRAPH_VERSION": "v25.0",
            "WHATSAPP_ALLOWED_NUMBERS": "5215555555555",
            "SQL_ALLOWED_TABLES": "proadel.gastos_data",
            "DB_CONNECTION_STRING": "DRIVER={test};Encrypt=yes;TrustServerCertificate=no;",
        }
        with (
            patch.dict(os.environ, variables, clear=True),
            patch("dbagg.config.project_root", return_value=self.root),
            patch("dbagg.llm.config.project_root", return_value=self.root),
            patch("dbagg.config.load_dotenv"),
        ):
            settings = Settings.from_env()
            self.assertEqual(settings.api_key, "synthetic-secret")
            self.assertEqual(settings.llm.profile.provider, "anthropic")
            self.assertNotIn("synthetic-secret", repr(settings))
            del os.environ["ANTHROPIC_API_KEY"]
            os.environ["OPENAI_API_KEY"] = "wrong-provider-key"
            with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
                Settings.from_env()

    def test_prompt_edit_changes_hash_and_message_without_template_execution(self):
        self.initialize()
        config = load_llm_config(self.root, {})
        original = load_prompt_bundle(config)
        config.system_prompt.write_text("Sé breve. {schema} {{no_python}}", encoding="utf-8")
        changed = load_prompt_bundle(config)
        self.assertNotEqual(changed.version, original.version)
        prompt = build_messages('["allowed.table"]', "{}", "hola", bundle=changed)[0]["content"]
        self.assertIn("{{no_python}}", prompt)
        self.assertIn("allowed.table", prompt)

    def test_descriptions_edit_does_not_change_tool_permissions_or_schema(self):
        self.initialize()
        config = load_llm_config(self.root, {})
        before = tool_definitions(load_prompt_bundle(config))
        descriptions = json.loads(config.tool_descriptions.read_text())
        descriptions["consultar_sql"] = "Explica esta herramienta con otra redacción."
        config.tool_descriptions.write_text(json.dumps(descriptions))
        after = tool_definitions(load_prompt_bundle(config))
        self.assertEqual(
            [t["function"]["parameters"] for t in before],
            [t["function"]["parameters"] for t in after],
        )
        self.assertNotEqual(
            before[-1]["function"]["description"], after[-1]["function"]["description"]
        )
        descriptions["borrar_tabla"] = "Una herramienta inventada"
        config.tool_descriptions.write_text(json.dumps(descriptions))
        with self.assertRaises(ValueError):
            load_prompt_bundle(config)

    def test_missing_empty_or_oversized_prompt_fails_explicitly(self):
        self.initialize()
        config = load_llm_config(self.root, {})
        for content in ("", "x" * 24001):
            config.system_prompt.write_text(content)
            with self.assertRaises(ValueError):
                load_prompt_bundle(config)
        with self.assertRaises(ValueError):
            load_prompt_bundle(replace(config, system_prompt=self.root / "absent.md"))
