# AI 보안 외부 근거 조사와 P13 적용 기준

- 상태: 조사 완료 — 정책 반영 전 제안
- 조사 기준일: 2026-09-04
- 적용 범위: P13의 G0 이전 데이터 분류·마스킹 test-only 준비

## 1. 결론

현재 [AI 보안 정책](./ai-security-policy.md)은 Zero Trust, 최소 권한, 데이터 최소화, 가명처리와 사람의 승인을 프로젝트 내부 원칙으로 채택했지만 외부 표준·법규와 통제 사이의 추적 관계는 기록하지 않는다.

이 프로젝트에는 외부 근거를 다음 네 층으로 나눠 적용하는 것이 적절하다.

1. 개인정보보호법과 AI기본법은 실제 서비스 조건에 따른 법적 적용성을 먼저 확인한다.
2. OWASP LLM Top 10과 NIST 문서는 구현·시험·위험관리 기준으로 사용한다.
3. ISO 문서는 조직 관리체계의 구조를 참고하되 현재 MVP의 인증 범위로 삼지 않는다.
4. 신용정보법·전자금융거래법·금융소비자보호법은 사업자 지위와 기능이 확장될 때 재판정한다.

이 문서는 법률 의견이나 새 보안 정책이 아니다. G0 이전에는 합성 sentinel과 테스트 판정 기준만 제공하고 production 통제와 기존 정책은 변경하지 않는다.

## 2. 프로젝트 전제와 조사 경계

현재 확인한 제품 경계는 다음과 같다.

- Browser는 AI 서비스를 직접 호출하지 않고 Django가 사용자·상담 소유권과 저장 승인을 통제한다.
- AI는 은퇴 설계 입력과 금융 프로필을 다루지만 사용자 승인 전에는 DB 변경을 확정하지 않는다.
- 현재 범위에는 결제·송금·투자 주문, 대출·신용 심사, 검토되지 않은 금융상품 추천이 없다.
- 외부 LLM과 관측 서비스의 실제 사업자, 처리 지역, 보존기간, 모델 학습 사용 여부와 재위탁자는 아직 이 조사로 확정하지 않는다.

마지막 항목이 확정되기 전에는 개인정보 처리위탁·제3자 제공·국외 이전 여부도 최종 판정할 수 없다.

## 3. 제안하는 외부 근거

| 적용 수준 | 근거 | 프로젝트 적용 | P13 연결 |
|---|---|---|---|
| 법적 적용성 필수 검토 | 개인정보보호법·개인정보위 지침 | 처리 근거, 최소수집, 위탁, 안전조치, 국외 이전, 가명처리 | 데이터 등급과 외부 sink별 원문 금지 |
| 법적 적용성 필수 검토 | AI기본법 | 생성형 AI 사용 고지·결과 표시, 고영향 AI 예비 판단 | 직접 테스트 범위 밖이며 G0 이후 제품 수락 조건으로 전달 |
| 기술 기준 채택 | OWASP LLM Top 10 2026 | 민감정보 유출, 숨은 문맥 노출, 과도한 권한, 출력 검증 | LLM02·LLM08을 sentinel 누락 검사에 연결 |
| 기술 원칙 채택 | NIST SP 800-207 | 네트워크 위치에 의존하지 않는 요청별 인증·인가 | Django→AI→MCP·callback 경계의 별도 통제로 전달 |
| 위험관리 구조 채택 | NIST AI RMF·GenAI Profile | Govern·Map·Measure·Manage 위험 등록과 재검토 | 테스트 결과를 위험·완화·잔여위험과 연결 |
| 관리체계 참고 | ISO/IEC 27001·27701·42001·23894 | 정보보호·개인정보·AI 관리체계의 구조 | 현재는 추적표 참고 근거이며 인증은 제외 |
| 조건부 적용 | 국내 금융 법규·금융권 AI 가이드라인 | 신용정보, 금융거래, 금융상품 자문 범위 확장 시 재평가 | 현재 sentinel 분류를 법적 적용 확정으로 사용하지 않음 |

### 3.1 개인정보보호법과 개인정보위 지침

한국 내 개인정보 처리 서비스라는 전제에서 우선 연결할 조항은 다음과 같다.

- [개인정보보호법 제15조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1029334735): 개인정보 처리의 법적 근거
- [제16조](https://law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1006183947): 목적에 필요한 최소한의 수집
- [제26조](https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900079061): 처리위탁 문서화·공개·관리감독
- [제29조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1033215737): 개인정보 안전성 확보조치
- [제28조의8](https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1029334953): 국외 이전의 근거와 고지·동의 요건
- [제28조의2부터 제28조의5](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1020398635): 가명정보의 제한된 활용, 추가정보 분리와 재식별 금지

실무 적용에는 개인정보위의 다음 최신 자료를 함께 사용한다.

- [생성형 AI 개발·활용을 위한 개인정보 처리 안내서](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030020&nttId=11439)
- [AI 프라이버시 위험관리 모델](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=D010030000&nttId=11014)
- [가명정보 처리 가이드라인](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=D010030000&nttId=11931)
- [개인정보의 안전성 확보조치 안내서](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=D010030000&nttId=11641)

가명처리는 익명화가 아니며 일반 서비스 처리의 독립적인 법적 근거도 아니다. 재식별 가능성이 남은 가명정보는 개인정보 보호 의무의 대상이라는 전제로 설계한다.

### 3.2 데이터 분류 용어

개인정보보호법의 법정 `민감정보`는 [제23조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1029335401)와 [시행령 제18조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=67045)의 범위를 따른다. 일반적인 소득·자산·부채가 그 자체로 이 법정 분류가 되는 것은 아니다.

따라서 P13 테스트에서는 다음 용어를 사용한다.

| 분류 | 의미 | 기본 검사 |
|---|---|---|
| 직접 식별정보 | 이메일, 전화번호, 계좌 식별자처럼 개인을 직접 식별하는 값 | 모델·로그·trace의 원문 노출 금지 |
| 준식별정보 | 나이·직업·가족구성과 같이 결합해 식별 가능성을 높이는 값 | 모델은 과업별 allowlist, 로그·trace는 원문 금지 |
| 프로젝트 고위험 금융정보 | 소득·자산·부채·연금 등 프로젝트가 강화 보호하는 내부 등급 | 모델은 과업별 allowlist, 로그·trace는 마스킹 또는 구간화 |
| 운영정보 | request·thread·trace 식별자와 실행 메타데이터 | 외부 전달 전 가명 식별자로 대체 |
| 법정 민감정보 | 법 제23조와 시행령 제18조가 정한 정보 | 수집 시 별도 법적 근거와 강화 통제 검토 |
| 개인신용정보 | 신용정보법 적용성이 확인된 정보 | 현재 일괄 확정하지 않고 데이터 출처·사업자 지위로 재판정 |

### 3.3 OWASP LLM Top 10 2026

[OWASP GenAI LLM Top 10 2026](https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/)은 법규 준수 인증이 아니라 위협 분류와 시험 설계 기준으로 사용한다. 정책에는 연도와 검토일을 고정해 기록한다.

P13과 직접 연결되는 항목은 다음과 같다.

- LLM02 Sensitive Information Disclosure: 모델 입력·출력과 외부 관측 sink의 개인정보·금융정보 유출
- LLM08 Hidden Context Exposure: 시스템 지시, 내부 식별자와 숨은 문맥 노출
- LLM03 Excessive Agency: 저장·도구 실행·외부 부작용을 사람의 승인 뒤로 제한
- LLM10 Improper Output Handling: LLM 출력을 callback이나 도구 인자로 사용하기 전 재검증
- LLM06 Unbounded Consumption: 요청 크기·토큰·재시도·동시 실행 상한
- LLM05·LLM09: RAG 데이터 오염과 벡터·Embedding 경계

이번 P13 준비는 LLM02·LLM08의 literal sentinel 누락만 직접 검증한다. 인코딩·우회·간접 prompt injection은 D13 공격 회귀 범위로 전달한다.

### 3.4 NIST

[NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final)의 Zero Trust는 내부 네트워크나 서비스 소유만으로 암묵적 신뢰를 부여하지 않고 리소스 세션 전에 인증·인가를 수행하는 원칙이다. 이 프로젝트에서는 Browser→Django→AI→MCP와 AI→Django callback 각각을 별도 신뢰 경계로 취급하는 근거로 사용한다.

[NIST AI RMF 1.0](https://www.nist.gov/itl/ai-risk-management-framework)과 [NIST AI 600-1 생성형 AI 프로파일](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)은 자율 위험관리 프레임워크다. 인증이나 개별 코드 통제의 대체물이 아니라 다음 기록 구조로 사용한다.

1. Govern: 적용 정책, 예외와 재검토 조건
2. Map: 데이터 흐름, 사용자 영향과 외부 사업자
3. Measure: sentinel 누락, 과탐·미탐과 공격 회귀 결과
4. Manage: 차단, 완화, 수락한 잔여위험과 재평가

### 3.5 ISO

- [ISO/IEC 27001:2022](https://www.iso.org/standard/27001): 정보보호 관리체계
- [ISO/IEC 27701:2025](https://www.iso.org/standard/27701?browse=tc): 개인정보 관리체계
- [ISO/IEC 42001:2023](https://www.iso.org/standard/42001?browse=ics): AI 관리체계
- [ISO/IEC 23894:2023](https://www.iso.org/cms/%20render/live/en/sites/isoorg/contents/data/standard/07/73/77304.html?browse=tc): AI 위험관리 지침

현재는 통제 분류와 중장기 관리체계의 참고 근거로만 채택한다. 인증 획득이나 표준 전체 준수를 MVP 완료 기준으로 주장하지 않는다.

### 3.6 AI기본법

현재 기능은 생성형 AI를 사용하므로 [AI기본법 제31조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1031810723)의 AI 기반 운영 사전 고지와 생성형 AI 결과 표시를 검토한다.

현재처럼 제안 후 사용자 승인을 요구하고 대출·신용 심사처럼 권리·의무를 자동 결정하지 않는 범위는 고영향 AI와 거리가 있다. 다만 이는 확정 판단이 아니다. [고영향 AI 정의](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1031810747), [해당 여부 확인 절차](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1031810895)와 [사업자 책무](https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1031809455)를 기준으로 기능 변경 때 재평가한다.

개인정보보호법 [제37조의2](https://law.go.kr/LSW/lsInfoP.do?lsiSeq=248613)의 자동화된 결정 관련 권리도 권리·의무에 중대한 영향을 미치는 완전 자동화 판단을 추가할 때 재검토한다.

## 4. P13 데이터 흐름과 테스트 판정

G0 이전에는 production 인터페이스를 가정하지 않고 다음 직렬화 checkpoint를 test-only sink로 정의한다.

| checkpoint | 직접 식별정보 | 준식별정보 | 프로젝트 고위험 금융정보 | 운영정보 |
|---|---|---|---|---|
| 모델 입력 | 차단 또는 대체 | 과업별 allowlist | 과업별 allowlist | 원문 제거 |
| 모델 출력 | echo·재생성 원문 차단 | 결과 스키마에 필요한 범위만 허용 | 결과 스키마에 필요한 범위만 허용 | 원문 제거 |
| 애플리케이션 로그 | 마스킹 | 생략·구간화 | 마스킹·구간화 | 가명화 |
| trace exporter | 마스킹 | 생략·구간화 | 마스킹·구간화 | 가명화 |

`ai-pjt/tests/security/masking_contract.py`는 각 등급의 합성 sentinel과 raw literal 금지 sink를 선언한다. 현재 검사는 다음만 증명한다.

- 각 데이터 등급과 각 sink에 최소 하나 이상의 누락 탐지 사례가 있다.
- passthrough payload가 금지 sink에 도달하면 실패한다.
- 로그·trace에 포맷된 금융 숫자가 남으면 실패한다.
- 테스트 데이터가 실제 사용자 원문을 사용하지 않는다.

현재 검사가 증명하지 않는 항목은 다음과 같다.

- 실제 production masker·logger·trace exporter가 계약에 연결됐는지
- 과업별 모델 입력 allowlist가 정확한지
- 의미 추론, 부분 노출, 인코딩, 암호화된 형태의 우회 유출
- 외부 사업자의 보존·학습·국외 이전 조건

## 5. 프로젝트에 제안하는 것

1. P13은 개인정보보호법·개인정보위 지침과 OWASP LLM02·LLM08을 직접 근거로 사용한다.
2. `금융 민감정보`를 외부 법규의 민감정보로 단정하지 않고 `프로젝트 고위험 금융정보`라는 내부 등급으로 구분한다.
3. 외부 LLM·관측 서비스마다 사업자, 처리 목적, region, 보존기간, 학습 사용 여부, 재위탁자와 삭제 방법을 기록한다.
4. 마스킹은 prompt 지시가 아니라 애플리케이션의 결정론적 전송 경계에서 수행하고 실패 시 외부 전송을 닫는다.
5. 실제 사용자 원문을 보안 시험 증거로 보관하지 않고 합성 sentinel과 비식별 메타데이터로 재현한다.
6. 외부 근거 추적표에는 출처·버전·적용 수준·정책 통제·구현 위치·검증 방법·재검토 조건을 기록한다.
7. AI기본법의 생성형 AI 고지·표시와 고영향 AI 예비 판단을 G0 이후 제품 수락 조건으로 전달한다.

## 6. 현재 제안하지 않는 것

1. ISO 27001·27701·42001 인증이나 전체 통제를 3주 MVP 완료 기준으로 삼지 않는다.
2. OWASP·NIST의 모든 항목을 관련성 검토 없이 일괄 편입하지 않는다.
3. 가명처리를 익명화 또는 일반 처리의 독립적인 법적 근거로 사용하지 않는다.
4. prompt 지시만으로 개인정보·금융정보 마스킹을 보장하지 않는다.
5. 재현을 이유로 원문 prompt나 실제 금융 값을 일반 로그·trace에 저장하지 않는다.
6. 금융 값을 처리한다는 사실만으로 신용정보법·전자금융거래법·금융소비자보호법의 전면 적용을 확정하지 않는다.
7. 해외 사용자·사업 범위가 없는 상태에서 GDPR이나 미국 주별 규제를 현재 필수 통제로 확장하지 않는다.

## 7. 금융 규제 재판정 조건

다음 중 하나가 추가되면 금융 규제 적용성을 다시 조사한다.

- 은행·카드·마이데이터·신용정보원 등에서 실제 금융·신용 데이터를 수집한다.
- 신용평가, 대출 가능 여부, 보험 인수 등 권리·의무에 영향을 주는 판단을 한다.
- 결제·송금·투자 주문을 실행한다.
- 특정 금융상품을 개인화 추천하거나 계속적·반복적으로 자문한다.
- 금융회사에 납품하거나 금융회사를 대신해 AI 기능을 제공한다.

재판정의 1차 근거는 [신용정보법 제15조](https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1025128241), [전자금융거래법의 정의](https://www.law.go.kr/LSW/lsSideInfoP.do?chrClsCd=010202&docCls=jo&joBrNo=00&joNo=0002&lsiSeq=280277&urlMode=lsScJoRltInfoR), [금융소비자보호법의 정의](https://www.law.go.kr/LSW/lsSideInfoP.do?chrClsCd=010202&docCls=jo&joBrNo=00&joNo=0002&lsiSeq=277247&urlMode=lsScJoRltInfoR)와 [금융위원회 금융권 AI 가이드라인](https://www.fsc.go.kr/no010101/87142)이다.

## 8. G0 이후 전달 사항

1. production masker 공개 뒤 모델 request·response, callback, 로그, trace 직전 직렬화 값에 P13 계약을 연결한다.
2. Agent별 입력 allowlist와 필드 목적을 실제 단계 registry에 결합한다.
3. 외부 사업자 조건이 정해지면 위탁·제3자 제공·국외 이전을 구분하고 개인정보 처리방침·동의·계약 반영 여부를 검토한다.
4. 생성형 AI 표시와 고영향 AI 예비 판단 결과를 제품 UI·정책 수락 기준에 연결한다.
5. 단순 literal 이외의 우회 유출은 D13 회귀셋에서 실행하고 P11 관측 통합은 P13 production 검사가 통과한 뒤 허용한다.
