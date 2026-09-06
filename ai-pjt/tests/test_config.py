from __future__ import annotations

import sys
import unittest
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_ROOT))

from core.config import RuntimeConfigurationError, Settings  # noqa: E402


BASE_ENV = {
    "LLM_PROVIDER": "gemini",
    "EMBEDDING_PROVIDER": "gemini",
    "GOOGLE_API_KEY": "g1-disposable-provider-key",
    "DATABASE_URL": "postgresql://g1:g1-disposable-db@postgres/g1",
    "AI_SERVICE_BEARER_TOKEN": "g1-disposable-bearer",
}


class SettingsTests(unittest.TestCase):
    def test_valid_disposable_runtime_configuration(self) -> None:
        settings = Settings(BASE_ENV)

        settings.validate_runtime()

        self.assertEqual(settings.rag_top_k, 5)
        self.assertEqual(settings.embed_dim, 3072)
        self.assertFalse(settings.langfuse_enabled)

    def test_required_database_and_bearer_values_fail_closed(self) -> None:
        for name in ("DATABASE_URL", "AI_SERVICE_BEARER_TOKEN"):
            with self.subTest(name=name):
                environ = {**BASE_ENV, name: ""}
                with self.assertRaisesRegex(RuntimeConfigurationError, name):
                    Settings(environ).validate_runtime()

    def test_selected_provider_requires_its_credential(self) -> None:
        with self.assertRaisesRegex(RuntimeConfigurationError, "GOOGLE_API_KEY"):
            Settings({**BASE_ENV, "GOOGLE_API_KEY": ""}).validate_runtime()

        openai_env = {
            **BASE_ENV,
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "g1-disposable-openai-key",
        }
        Settings(openai_env).validate_runtime()

    def test_rejects_invalid_provider_boolean_and_numeric_values(self) -> None:
        with self.assertRaisesRegex(RuntimeConfigurationError, "LLM_PROVIDER"):
            Settings({**BASE_ENV, "LLM_PROVIDER": "unknown"}).validate_runtime()
        with self.assertRaisesRegex(
            RuntimeConfigurationError, "EMBEDDING_PROVIDER"
        ):
            Settings(
                {**BASE_ENV, "EMBEDDING_PROVIDER": "openai"}
            ).validate_runtime()
        with self.assertRaisesRegex(RuntimeConfigurationError, "LANGFUSE_ENABLED"):
            Settings({**BASE_ENV, "LANGFUSE_ENABLED": "yes"})
        with self.assertRaisesRegex(RuntimeConfigurationError, "RAG_TOP_K"):
            Settings({**BASE_ENV, "RAG_TOP_K": "0"})


if __name__ == "__main__":
    unittest.main()
