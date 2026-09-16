from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # Redis 전환 시 활성화

from core.config import settings
from core.context import ContextStateAPI
from core.context_store import PostgresCommittedContextStore


@asynccontextmanager
async def get_checkpointer():
    async with AsyncPostgresSaver.from_conn_string(settings.db_url) as checkpointer:
        await checkpointer.setup()
        yield checkpointer

    # Redis로 전환 시 위 블록 대신 아래 사용 (setup() 불필요):
    # async with AsyncRedisSaver.from_conn_string(settings.redis_url) as checkpointer:
    #     yield checkpointer


def build_committed_context_store() -> PostgresCommittedContextStore:
    return PostgresCommittedContextStore(
        settings.db_url,
        execution_ttl_seconds=settings.context_execution_ttl_seconds,
        receipt_ttl_seconds=settings.context_receipt_ttl_seconds,
        closed_retention_seconds=settings.context_closed_retention_seconds,
        embedding_dimensions=settings.embed_dim,
    )


def build_context_state_api(checkpointer, committed) -> ContextStateAPI:
    """Expose the context boundary without coupling it to FastAPI transport."""

    return ContextStateAPI(checkpointer, committed)
