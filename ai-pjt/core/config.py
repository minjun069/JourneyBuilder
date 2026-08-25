import os

from dotenv import load_dotenv

load_dotenv()

_EMBEDDING_DEFAULTS = {
    "openai":     "text-embedding-3-small",
    "opensource": "text-embedding-3-small",
    "gemini":     "models/text-embedding-004",
}

# 임베딩 모델별 벡터 차원수.
# PGVector 테이블은 생성 시점의 차원으로 고정되므로,
# 모델을 바꿀 경우 기존 테이블을 DROP하고 재생성해야 합니다.
#   OpenAI  text-embedding-3-small  : 1536
#   Gemini  text-embedding-004      : 768
#   Gemini  gemini-embedding-001    : 3072  ← 현재 기본값
_EMBED_DIM_DEFAULTS = {
    "openai":     1536,
    "opensource": 1536,
    "gemini":     3072,
}


class Settings:
    # LLM Provider: "openai" | "gemini" | "opensource"
    llm_provider: str = os.environ.get("LLM_PROVIDER", "gemini")

    # Embeddig Provider: "openai" | "gemini" | "opensource"
    embedding_provider: str = os.environ.get("EMBEDDING_PROVIDER", "gemini")

    llm_model: str = os.environ.get("LLM_MODEL", "gemini-2.5-flash-lite")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")

    # @property
    # def embedding_model(self) -> str:
    #     return os.environ.get("EMBEDDING_MODEL") or _EMBEDDING_DEFAULTS.get(self.llm_provider, "text-embedding-3-small")


    # OpenAI / OpenAI-compatible
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1")
    
    # Gemini
    gemini_api_key: str = os.environ.get("GOOGLE_API_KEY", "")

    # DB
    db_url: str = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/de_db")
    redis_url: str = os.environ.get("REDIS_URL", "redis://redis:6379")

    # RAG
    rag_collection: str = os.environ.get("RAG_COLLECTION", "retirement_docs")
    rag_top_k: int = int(os.environ.get("RAG_TOP_K", "5"))
    # EMBED_DIM 환경변수로 직접 지정 가능. 미설정 시 EMBEDDING_PROVIDER 기반 기본값 사용.
    embed_dim: int = int(os.environ.get("EMBED_DIM") or _EMBED_DIM_DEFAULTS.get(
        os.environ.get("EMBEDDING_PROVIDER", "gemini"), 3072
    ))

    

    # Backend
    backend_url: str = os.environ.get("BACKEND_URL", "http://localhost:8080")
    ai_callback_secret: str = os.environ.get("AI_CALLBACK_SECRET", "")

    # Langfuse (자체 호스팅 LLM 모니터링)
    langfuse_enabled: bool = os.environ.get("LANGFUSE_ENABLED", "false").lower() == "true"
    langfuse_public_key: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_host: str = os.environ.get("LANGFUSE_HOST", "http://langfuse:3000")


settings = Settings()
