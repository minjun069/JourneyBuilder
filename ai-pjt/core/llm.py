from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from core.config import RuntimeConfigurationError, settings


from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core import Settings


settings.validate_runtime()


# ==========================================
# 1. LLM
# ==========================================

def _create_llm() -> BaseChatModel:
    provider = settings.llm_provider

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            google_api_key=settings.gemini_api_key,
            streaming=True,
        )

    # "openai" or "opensource" — vLLM 등 OpenAI 호환 엔드포인트 모두 처리
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url or None,
        api_key=settings.openai_api_key or "not-needed",
        streaming=True,
    )


# ==========================================
# 2. Embedding
# ==========================================


def _create_embeddings() -> Embeddings:
    provider = settings.llm_provider

    if provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        return GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
            google_api_key=settings.gemini_api_key,
        )

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        base_url=settings.llm_base_url or None,
        api_key=settings.openai_api_key or "not-needed",
    )


def _create_llamaindex_embedding() -> BaseEmbedding:
    provider = settings.embedding_provider

    if provider == "gemini":
        from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
        return GoogleGenAIEmbedding(
            model_name=settings.embedding_model, # 예: "models/text-embedding-004"
            api_key=settings.gemini_api_key,
        )
    elif provider == "openai":
        raise RuntimeConfigurationError(
            "EMBEDDING_PROVIDER=openai has no installed runtime adapter"
        )
    raise RuntimeConfigurationError("EMBEDDING_PROVIDER is unsupported")

llm: BaseChatModel = _create_llm()
# embeddings: Embeddings = _create_embeddings()

embed_model: BaseEmbedding = _create_llamaindex_embedding()
# Settings.embed_model = _create_llamaindex_embedding()


def call_llm(state, system_prompt: str) -> dict:
    system = SystemMessage(content=system_prompt)
    response = llm.invoke([system, HumanMessage(content=state["message"])])
    return {"output": {"response": response.content, "update": {}}}
