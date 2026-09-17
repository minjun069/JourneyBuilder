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
    "AI_MASKING_PSEUDONYM_KEY": "g1-disposable-masking-key-32-bytes!",
}


class SettingsTests(unittest.TestCase):
    def test_valid_disposable_runtime_configuration(self) -> None:
        settings = Settings(BASE_ENV)

        settings.validate_runtime()

        self.assertEqual(settings.rag_top_k, 5)
        self.assertEqual(settings.llm_temperature, 0.0)
        self.assertEqual(settings.prompt_release, "2026-09-17.3")
        self.assertEqual(settings.rag_fetch_k, 10)
        self.assertEqual(settings.rag_min_dense_score, 0.54)
        self.assertEqual(settings.rag_financial_collection, "financial-knowledge")
        self.assertEqual(settings.rag_interface_collection, "interface-guides")
        self.assertEqual(settings.embed_dim, 3072)
        self.assertEqual(settings.context_memory_interval_seconds, 5)
        self.assertEqual(settings.context_memory_top_k, 5)
        self.assertEqual(settings.context_embedding_batch_size, 8)
        self.assertFalse(settings.langfuse_enabled)
        self.assertEqual(settings.langfuse_base_url, "")
        self.assertEqual(settings.langfuse_release, "local")
        self.assertEqual(
            settings.masking_pseudonym_key_bytes,
            b"g1-disposable-masking-key-32-bytes!",
        )

    def test_required_database_and_bearer_values_fail_closed(self) -> None:
        for name in (
            "DATABASE_URL",
            "AI_SERVICE_BEARER_TOKEN",
            "AI_MASKING_PSEUDONYM_KEY",
        ):
            with self.subTest(name=name):
                environ = {**BASE_ENV, name: ""}
                with self.assertRaisesRegex(RuntimeConfigurationError, name):
                    Settings(environ).validate_runtime()

    def test_masking_key_is_long_and_separate_from_service_auth(self) -> None:
        with self.assertRaisesRegex(RuntimeConfigurationError, "at least 32 bytes"):
            Settings({**BASE_ENV, "AI_MASKING_PSEUDONYM_KEY": "too-short"}).validate_runtime()
        shared_key = "same-auth-and-masking-key-at-least-32-bytes"
        with self.assertRaisesRegex(RuntimeConfigurationError, "must differ"):
            Settings({
                **BASE_ENV,
                "AI_SERVICE_BEARER_TOKEN": shared_key,
                "AI_MASKING_PSEUDONYM_KEY": shared_key,
            }).validate_runtime()

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
        with self.assertRaisesRegex(RuntimeConfigurationError, "RAG_FETCH_K"):
            Settings({**BASE_ENV, "RAG_TOP_K": "5", "RAG_FETCH_K": "4"})
        with self.assertRaisesRegex(RuntimeConfigurationError, "RAG_MIN_DENSE_SCORE"):
            Settings({**BASE_ENV, "RAG_MIN_DENSE_SCORE": "1.1"})
        with self.assertRaisesRegex(RuntimeConfigurationError, "LLM_TEMPERATURE"):
            Settings({**BASE_ENV, "LLM_TEMPERATURE": "1.1"})
        with self.assertRaisesRegex(RuntimeConfigurationError, "PROMPT_RELEASE"):
            Settings({**BASE_ENV, "PROMPT_RELEASE": "missing"}).validate_runtime()
        with self.assertRaisesRegex(
            RuntimeConfigurationError,
            "AI_CONTEXT_MEMORY_TOP_K",
        ):
            Settings({**BASE_ENV, "AI_CONTEXT_MEMORY_TOP_K": "0"})

    def test_langfuse_requires_complete_safe_configuration_when_enabled(self) -> None:
        enabled = {
            **BASE_ENV,
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_BASE_URL": "https://langfuse.example.com",
        }
        Settings(enabled).validate_runtime()
        self.assertEqual(
            Settings(
                {**enabled, "LANGFUSE_RELEASE": "candidate-sha-dirty"}
            ).langfuse_release,
            "candidate-sha-dirty",
        )

        for name in (
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "LANGFUSE_BASE_URL",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(RuntimeConfigurationError, name):
                    Settings({**enabled, name: ""}).validate_runtime()

        for url in (
            "langfuse.example.com",
            "ftp://langfuse.example.com",
            "https://user:password@langfuse.example.com",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(RuntimeConfigurationError, "http"):
                    Settings({**enabled, "LANGFUSE_BASE_URL": url}).validate_runtime()


if __name__ == "__main__":
    unittest.main()
