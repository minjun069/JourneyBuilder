"""LLM 기반 텍스트 변환 (키워드 추출 / 카테고리 분류 / 임베딩 생성).

기존 consumer/preprocessing.py 의 변환 로직을 common 으로 이동한 모듈.
news(배치) 와 reports(검색/리포트) 양쪽에서 재사용한다.

환경변수:
  OPENAI_API_KEY    : (필수) GMS 키
  OPENAI_BASE_URL   : OpenAI 호환 엔드포인트 (기본 GMS)
  LLM_CHAT_MODEL    : 키워드/분류 모델 (기본 gpt-4o-mini)
  GEMINI_API_KEY    : (필수) Gemini 임베딩 키
  GEMINI_EMBED_MODEL: 임베딩 모델 (기본 models/gemini-embedding-001)
"""

import json
import os

import tiktoken
from openai import OpenAI

from common.logger import get_logger
from common.taxonomy import CATEGORIES, TAXONOMY, UNCLASSIFIED, tags_of

log = get_logger(__name__)

# --- 설정 ---
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1")
CHAT_MODEL = os.getenv("LLM_CHAT_MODEL", "gpt-4o-mini")
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/gemini-embedding-001")

# 임베딩 차원 (DB news.embedding VECTOR(1536) 및 ES dense_vector dims 와 일치해야 함)
EMBEDDING_DIM = 1536
EMBED_TASK_TYPE = "RETRIEVAL_DOCUMENT"

# 텍스트 토큰 상한 (처리 효율 및 비용 제어)
MAX_INPUT_TOKENS = 5000

# 카테고리/태그 분류 체계는 common/taxonomy.py 에서 관리 (news·reports 공유).
# DEFAULT_CATEGORIES / UNCLASSIFIED 는 taxonomy 에서 가져온다.
DEFAULT_CATEGORIES = CATEGORIES

_client: OpenAI | None = None
_encoding = None
_gemini_configured = False


def get_client() -> OpenAI:
    """OpenAI(GMS) 클라이언트를 lazy 하게 생성/반환한다."""
    global _client
    if _client is None:
        _client = OpenAI(base_url=BASE_URL)
    return _client


def _get_gemini():
    """Gemini 클라이언트를 lazy import/configure 한다."""
    global _gemini_configured
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise RuntimeError(
            "google-generativeai 패키지가 필요합니다. "
            "`pip install -r data-pjt/requirements.txt` 후 다시 실행하세요."
        ) from e

    if not _gemini_configured:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")
        genai.configure(api_key=api_key)
        _gemini_configured = True

    return genai


def _get_encoding():
    global _encoding
    if _encoding is None:
        try:
            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            log.warning("tiktoken 인코딩 로드 실패, 문자 길이 기준으로 제한합니다: %s", e)
            _encoding = False
    return _encoding


def preprocess_content(content: str) -> str:
    """텍스트 길이를 토큰 기준으로 제한한다 (MAX_INPUT_TOKENS 초과 시 잘라냄)."""
    if not content:
        return ""

    encoding = _get_encoding()
    if encoding is False:
        return content[: MAX_INPUT_TOKENS * 4]

    tokens = encoding.encode(content)

    if len(tokens) > MAX_INPUT_TOKENS:
        return encoding.decode(tokens[:MAX_INPUT_TOKENS])

    return content


def transform_extract_keywords(text: str, top_n: int = 7) -> list[str]:
    """텍스트에서 핵심 키워드 top_n 개를 추출해 리스트로 반환한다."""
    text = preprocess_content(text)
    if not text:
        return []

    response = get_client().chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 텍스트에서 주요 키워드를 추출하는 뉴스 기사 분석 전문가입니다."
                    f"다음 뉴스 기사에서 검색과 추천에 유용한 핵심 {top_n}개의 키워드를 추출해주세요. "
                    """
                    규칙:
                    1. 인물명, 기업명, 기관명, 국가명, 기술명, 산업 용어를 우선 선택
                    2. '시장', '기술', '기업', '산업', '발표' 같은 일반 단어는 제외
                    3. 중복 의미 키워드는 하나만 선택
                    """
                    "키워드는 쉼표로 구분하여 반환해주세요."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=100,
    )

    raw = response.choices[0].message.content.strip()
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def transform_to_embedding(text: str) -> list[float]:
    """뉴스 본문을 Gemini 문서 임베딩 벡터(EMBEDDING_DIM 차원)로 변환한다."""
    text = preprocess_content(text)
    if not text:
        return []

    result = _get_gemini().embed_content(
        model=EMBED_MODEL,
        content=text,
        task_type=EMBED_TASK_TYPE,
        output_dimensionality=EMBEDDING_DIM,
    )
    return result["embedding"]


def _taxonomy_prompt() -> str:
    """카테고리별 통제 태그 목록을 프롬프트용 문자열로 만든다."""
    lines = [f"- {cat}: {', '.join(tags)}" for cat, tags in TAXONOMY.items()]
    return "\n".join(lines)


def transform_classify(content: str) -> dict:
    """뉴스를 카테고리 1개로 분류하고 해당 카테고리의 통제 태그를 선택한다.

    category 와 tags 를 한 번의 LLM 호출로 처리한다.
    반환: {"category": str, "tags": list[str]}
      - category: CATEGORIES 중 하나 (범위 밖/실패 시 '미분류')
      - tags: 선택된 category 의 통제 태그 부분집합 (없으면 빈 리스트)
    """
    content = preprocess_content(content)
    if not content:
        return {"category": UNCLASSIFIED, "tags": []}

    try:
        response = get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 뉴스 기사를 분류하는 어시스턴트입니다. "
                        "아래 분류 체계에서 가장 적절한 카테고리 1개를 고르고, "
                        "그 카테고리에 속한 태그 중 기사에 해당하는 것만 선택하세요. "
                        "태그는 반드시 해당 카테고리의 목록에서만 고르고, 없으면 빈 배열로 두세요.\n\n"
                        f"[분류 체계]\n{_taxonomy_prompt()}\n\n"
                        '반드시 JSON 으로만 응답: {"category": "...", "tags": ["...", "..."]}'
                    ),
                },
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            max_tokens=120,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as e:
        log.error("분류 실패: %s", e)
        return {"category": UNCLASSIFIED, "tags": []}

    category = str(data.get("category", "")).strip()
    if category not in CATEGORIES:
        return {"category": UNCLASSIFIED, "tags": []}

    allowed = set(tags_of(category))
    raw_tags = data.get("tags") or []
    tags = [t for t in raw_tags if t in allowed]

    return {"category": category, "tags": tags}


def transform_classify_category(content: str) -> str:
    """카테고리만 필요할 때의 헬퍼 (내부적으로 transform_classify 사용)."""
    return transform_classify(content)["category"]
