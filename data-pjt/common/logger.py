"""공통 로깅 설정.

news / reports 양쪽에서 동일한 포맷의 로거를 쓰기 위한 헬퍼.
기존 producer/consumer 의 로깅 포맷을 그대로 따른다.

사용:
    from common.logger import get_logger
    log = get_logger(__name__)
    log.info("...")
"""

import logging
import os

# 로그 레벨은 환경변수 LOG_LEVEL 로 조정 가능 (기본 INFO)
_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def get_logger(name: str) -> logging.Logger:
    """이름별 로거를 반환한다. 루트 핸들러는 최초 1회만 설정한다."""
    global _configured
    if not _configured:
        logging.basicConfig(level=_LEVEL, format=_FORMAT, datefmt=_DATEFMT)
        _configured = True
    return logging.getLogger(name)
