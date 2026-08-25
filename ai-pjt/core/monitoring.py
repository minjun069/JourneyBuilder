"""
LLM 모니터링 콜백 프로바이더

지원:
  - Langfuse  : 자체 호스팅. LANGFUSE_ENABLED=true + 키 설정 시 활성화.
  - LangSmith : 클라우드. 코드 변경 없이 env var 만으로 자동 활성화.
                LANGCHAIN_TRACING_V2=true, LANGCHAIN_API_KEY 설정으로 동작.
"""
from __future__ import annotations

from core.config import settings


def get_callbacks() -> list:
    """LangGraph config 의 "callbacks" 키에 주입할 핸들러 리스트 반환."""
    callbacks = []

    if settings.langfuse_enabled:
        try:
            from langfuse.callback import CallbackHandler
            callbacks.append(
                CallbackHandler(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
            )
        except ImportError:
            import warnings
            warnings.warn(
                "LANGFUSE_ENABLED=true 이지만 langfuse 패키지가 없습니다. "
                "`pip install langfuse` 를 실행하세요.",
                stacklevel=2,
            )

    # LangSmith: LANGCHAIN_TRACING_V2=true 이면 LangChain 이 자동으로 트레이서를 붙임.
    # 별도 코드 불필요 — 여기서는 아무것도 추가하지 않음.

    return callbacks
