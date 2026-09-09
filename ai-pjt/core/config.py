"""Runtime configuration for the AI service."""

from __future__ import annotations

import os
from collections.abc import Mapping

try:
    from dotenv import load_dotenv
except ImportError:  # Runtime images install it; contract tests need no dependency.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

_SUPPORTED_LLM_PROVIDERS = {"openai", "gemini", "opensource"}
_SUPPORTED_EMBEDDING_PROVIDERS = {"gemini"}
_EMBED_DIM_DEFAULTS = {
    "openai": 1536,
    "opensource": 1536,
    "gemini": 3072,
}
_INSECURE_SECRET_VALUES = {
    "change-me",
    "changeme",
    "password",
    "postgres",
    "secret",
}


class RuntimeConfigurationError(ValueError):
    """Raised when AI runtime configuration is incomplete or unsafe."""


def _read_positive_int(
    name: str,
    default: int,
    environ: Mapping[str, str],
) -> int:
    raw_value = environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeConfigurationError(f"{name} must be greater than 0")
    return value


def _read_bool(
    name: str,
    default: bool,
    environ: Mapping[str, str],
) -> bool:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeConfigurationError(f"{name} must be true or false")


def _read_unit_float(
    name: str,
    default: float,
    environ: Mapping[str, str],
) -> float:
    raw_value = environ.get(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be a number") from exc
    if value < 0.0 or value > 1.0:
        raise RuntimeConfigurationError(f"{name} must be between 0 and 1")
    return value


class Settings:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        active_environ = os.environ if environ is None else environ

        self.llm_provider = active_environ.get("LLM_PROVIDER", "gemini").strip()
        self.embedding_provider = active_environ.get(
            "EMBEDDING_PROVIDER", "gemini"
        ).strip()
        self.llm_model = active_environ.get(
            "LLM_MODEL", "gemini-2.5-flash-lite"
        ).strip()
        self.embedding_model = active_environ.get(
            "EMBEDDING_MODEL", "gemini-embedding-001"
        ).strip()

        self.openai_api_key = active_environ.get("OPENAI_API_KEY", "").strip()
        self.llm_base_url = active_environ.get(
            "LLM_BASE_URL",
            "https://gms.ssafy.io/gmsapi/api.openai.com/v1",
        ).strip()
        self.gemini_api_key = active_environ.get("GOOGLE_API_KEY", "").strip()
        # Gemini 요청을 프록시로 보낼 때 쓴다(GMS 등). 임베딩은 google-genai SDK 가 이 환경변수를
        # 직접 읽지만, ChatGoogleGenerativeAI 는 읽지 않으므로 llm.py 가 이 값을 넘겨준다.
        # 비어 있으면 Google 로 직접 간다(기존 동작).
        self.gemini_base_url = active_environ.get(
            "GOOGLE_GEMINI_BASE_URL", ""
        ).strip()

        self.db_url = active_environ.get("DATABASE_URL", "").strip()
        self.redis_url = active_environ.get(
            "REDIS_URL", "redis://redis:6379"
        ).strip()

        self.rag_financial_collection = active_environ.get(
            "RAG_FINANCIAL_COLLECTION", "financial-knowledge"
        ).strip()
        self.rag_interface_collection = active_environ.get(
            "RAG_INTERFACE_COLLECTION", "interface-guides"
        ).strip()
        self.rag_collection = self.rag_financial_collection
        self.rag_top_k = _read_positive_int("RAG_TOP_K", 5, active_environ)
        self.rag_fetch_k = _read_positive_int("RAG_FETCH_K", 10, active_environ)
        if self.rag_fetch_k < self.rag_top_k:
            raise RuntimeConfigurationError("RAG_FETCH_K must be at least RAG_TOP_K")
        self.rag_min_dense_score = _read_unit_float(
            "RAG_MIN_DENSE_SCORE", 0.30, active_environ
        )
        self.rag_index_retention_days = _read_positive_int(
            "RAG_INDEX_RETENTION_DAYS", 30, active_environ
        )
        self.embed_dim = _read_positive_int(
            "EMBED_DIM",
            _EMBED_DIM_DEFAULTS.get(self.embedding_provider, 3072),
            active_environ,
        )

        self.backend_url = active_environ.get(
            "BACKEND_URL", "http://localhost:8080"
        ).strip()
        self.ai_service_bearer_token = active_environ.get(
            "AI_SERVICE_BEARER_TOKEN", ""
        ).strip()
        self.ai_callback_secret = active_environ.get(
            "AI_CALLBACK_SECRET", ""
        ).strip()
        self.callback_timeout_seconds = float(
            active_environ.get("AI_CALLBACK_TIMEOUT_SECONDS", "5")
        )

        self.langfuse_enabled = _read_bool(
            "LANGFUSE_ENABLED", False, active_environ
        )
        self.langfuse_public_key = active_environ.get(
            "LANGFUSE_PUBLIC_KEY", ""
        ).strip()
        self.langfuse_secret_key = active_environ.get(
            "LANGFUSE_SECRET_KEY", ""
        ).strip()
        self.langfuse_host = active_environ.get(
            "LANGFUSE_HOST", "http://langfuse:3000"
        ).strip()

    @staticmethod
    def _require_runtime_value(name: str, value: str) -> None:
        if not value:
            raise RuntimeConfigurationError(f"{name} is required")
        if value.lower() in _INSECURE_SECRET_VALUES:
            raise RuntimeConfigurationError(f"{name} uses an insecure placeholder")

    def validate_runtime(self) -> None:
        """Reject an incomplete service configuration before accepting traffic."""

        if self.llm_provider not in _SUPPORTED_LLM_PROVIDERS:
            raise RuntimeConfigurationError("LLM_PROVIDER is unsupported")
        if self.embedding_provider not in _SUPPORTED_EMBEDDING_PROVIDERS:
            raise RuntimeConfigurationError("EMBEDDING_PROVIDER is unsupported")
        if not self.llm_model:
            raise RuntimeConfigurationError("LLM_MODEL is required")
        if not self.embedding_model:
            raise RuntimeConfigurationError("EMBEDDING_MODEL is required")
        if self.rag_financial_collection != "financial-knowledge":
            raise RuntimeConfigurationError(
                "RAG_FINANCIAL_COLLECTION must be financial-knowledge"
            )
        if self.rag_interface_collection != "interface-guides":
            raise RuntimeConfigurationError(
                "RAG_INTERFACE_COLLECTION must be interface-guides"
            )

        self._require_runtime_value("DATABASE_URL", self.db_url)
        self._require_runtime_value(
            "AI_SERVICE_BEARER_TOKEN",
            self.ai_service_bearer_token,
        )

        selected_providers = {self.llm_provider, self.embedding_provider}
        if "gemini" in selected_providers:
            self._require_runtime_value("GOOGLE_API_KEY", self.gemini_api_key)
        if "openai" in selected_providers:
            self._require_runtime_value("OPENAI_API_KEY", self.openai_api_key)
        if "opensource" in selected_providers and not self.llm_base_url:
            raise RuntimeConfigurationError("LLM_BASE_URL is required")


settings = Settings()
