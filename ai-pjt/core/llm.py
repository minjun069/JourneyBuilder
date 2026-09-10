import asyncio

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


async def _generate_with_sync_rest_client(
    model: BaseChatModel,
    messages,
    stop=None,
    **kwargs,
):
    """Keep path-based GMS endpoints on the provider's REST transport."""

    return await asyncio.to_thread(
        model._generate,
        messages,
        stop,
        None,
        **kwargs,
    )


def _create_llm() -> BaseChatModel:
    provider = settings.llm_provider

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        class GMSRestChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
            async def _agenerate(
                self,
                messages,
                stop=None,
                run_manager=None,
                **kwargs,
            ):
                return await _generate_with_sync_rest_client(
                    self,
                    messages,
                    stop,
                    **kwargs,
                )

        # GOOGLE_GEMINI_BASE_URL 이 있으면 그 주소로 보낸다(GMS 등 프록시).
        # 설치된 langchain-google-genai 2.x 는 구형 generativelanguage 클라이언트를 써서
        # 이 환경변수를 스스로 읽지 않으므로 client_options 로 명시해야 한다.
        # api_endpoint 는 scheme 없는 host+경로 형식이고, 경로 접두어가 빠지면 404·405 가 된다.
        proxy_options: dict[str, object] = {}
        if settings.gemini_base_url:
            proxy_options = {
                "transport": "rest",
                "client_options": {
                    "api_endpoint": settings.gemini_base_url.split("://", 1)[-1],
                },
            }
        model_class = (
            GMSRestChatGoogleGenerativeAI
            if settings.gemini_base_url
            else ChatGoogleGenerativeAI
        )
        return model_class(
            model=settings.llm_model,
            google_api_key=settings.gemini_api_key,
            streaming=True,
            **proxy_options,
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
