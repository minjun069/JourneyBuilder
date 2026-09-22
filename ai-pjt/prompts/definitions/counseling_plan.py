"""Optional counseling topic selection after verified answer aggregation."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "counseling.plan",
    "aggregator",
    """[할 일]

이미 답한 내용에 상담 지식이 실질적으로 보탬이 되는지 판단합니다. 원래 답변을 고치거나 고객의 숨은 사정을 확정하지 않습니다.

[선택 규칙]

- topic 하나만 반환합니다. 자유 검색어, 고객 정보, 이유나 답변은 반환하지 않습니다.
- 감사·종료, 숫자 정정만, 저장·조회만, 짧은 용어 정의만, 답변·추가 질문 거절이면 none입니다.
- 이미 필요한 설명을 충분히 했거나 보충하면 반복·불필요한 질문이 되는 경우도 none입니다.
- 정정과 함께 실질적인 새 재무 질문을 했다면 그 질문을 검토합니다. 정정 단어만으로 혼합 요청을 생략하지 않습니다.
- 현재 질문과 최신 고객 정정이 이전 요약·AI 추정보다 우선합니다. 걱정의 원인을 고객이 확인하지 않았다면 가설로만 봅니다.
- 실시간 수치, 상품 매매·가입 실행, 새로운 계산·판정은 이 보충 단계의 일이 아니므로 none입니다.

[topic]

none: 보충 불필요
cashflow: 입출금 시점·소득 중단·미확정 자금
debt: 월 상환 부담과 완충자금의 관계
assets: 자산 총액·편중·실제 사용가능성
pension: 예상 연금·개시 전 공백·동일 재원 중복
retirement: 은퇴진단의 의미와 가정의 한계
goals: 목표·시점·가족 재원·우선순위
wellbeing: 명시한 재무 걱정과 근거 있는 설명
comparison: 집단 비교·경제 맥락의 개인 적용 한계
understanding: 명시한 관심사와 미확인 해석의 구분""",
    (
        ("question", "현재 사용자 질문"),
        ("conversation_context", "이전 대화 문맥"),
        ("answer", "검증된 기존 답변"),
        ("task_types", "완료된 작업 유형"),
        ("step_id", "현재 화면"),
    ),
    output_schema="counseling_plan",
    risk="financial_explanation",
)
