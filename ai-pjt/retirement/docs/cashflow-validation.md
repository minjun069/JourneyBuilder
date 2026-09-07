# 현금흐름 검증 기록

## 실행 범위와 결과

2026-09-07, Python 3.13.13에서 현금흐름 브랜치의 2·3·4번 작업을 검증했다.
최종 전용 테스트 **73개 통과**: 기존 모델 32개, 기본 현금흐름 5개, 경계·오류 26개,
합성 입력·계산 실행기 10개. subtest 조합은 테스트 메서드 개수에 더하지 않는다.
단계별 통과 수는 2번 42개 → 3번 68개 → 4번 73개다. 기존 입력 모델 테스트는
그대로 유지하고 샘플 실행 테스트는 입력 검증 전용에서 실제 계산 출력 검증으로 갱신했다.

저장소 루트에서 다음 명령을 재실행할 수 있다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ai-pjt/retirement/tests -v
python3 -S -B -m unittest discover -s ai-pjt/retirement/tests -v
git diff --check
```

두 번째 명령은 site-packages 없이 같은 테스트를 실행한다. 외부 패키지·DB·환경변수·
네트워크·실제 고객 입력을 사용하지 않는다. 공용 `make g1-check`에 자동 포함된
테스트가 아니므로 별도로 실행해야 한다.

## 요구사항과 테스트 대응

| 검증 내용 | 테스트 위치·메서드 |
|---|---|
| A: 투자수익 적용 순서, 은퇴 전환, 연금 종료, 생활비 변경 | [기본 테스트](../tests/test_cashflow.py)의 `test_example_a_timing_retirement_pension_and_expense_changes` |
| B: 현재부터 누적하는 물가, 구간 전환 시 지수 유지 | 기본 테스트의 `test_example_b_inflation_starts_now_not_at_retirement_or_period_change` |
| 명시적 경로의 평균 대체·연도 순서·고정 실행 동등성 | 기본 테스트의 `test_explicit_path_replaces_mean_and_preserves_year_order`, `test_repeated_explicit_rates_match_fixed_execution` |
| 연말 점만 연도 수만큼 반환 | 기본 테스트의 `test_asset_path_has_one_closing_point_per_year_without_opening_duplicate` |
| C: 정확히 0, 고갈·회복, 최초 고갈 보존, 최종 부족액 | [경계 테스트](../tests/test_cashflow_edges.py)의 `test_example_c_zero_depletion_recovery_and_last_shortfall` |
| 음수 이월에 수익·이자 없음, 회복 다음 해 수익 재개 | 경계 테스트의 `test_negative_opening_balance_never_earns_return_or_interest`, `test_returns_resume_only_after_shortfall_is_offset` |
| 이미 은퇴, 은퇴 당해 적립 중단, 1년·최대 103년 기간 | 경계 테스트의 `test_already_retired_has_no_savings_or_backdated_inflation`, `test_one_year_horizon_and_exact_zero_are_valid`, `test_maximum_supported_horizon_has_all_years` |
| 연금 겹침·양 끝 포함·은퇴 전 수령·미수령·0원 | 경계 테스트의 `test_overlapping_pensions_are_nominal_inclusive_and_can_precede_retirement`, `test_zero_expenses_and_no_pension_stay_zero` |
| 음의 물가·수익률, -1 직상 값, 미세한 음수 보존 | 경계 테스트의 `test_negative_inflation_reduces_expenses`, `test_negative_return_applies_to_positive_opening_balance`, `test_nearest_float_above_total_loss_is_accepted`, `test_tiny_negative_balance_is_not_rounded_to_zero` |
| 미사용 확률 가정 무영향·불변성·반복 실행·금액 repr 비노출 | 경계 테스트의 `test_unused_stochastic_settings_do_not_change_fixed_result`, `test_inputs_paths_and_results_remain_immutable_and_repeatable`, `test_results_repr_does_not_echo_financial_values` |
| 잘못된 모델·경로 컨테이너·길이·rate, 전체 경로 선검증 | 경계 테스트의 `CashflowValidationTests` |
| 결과의 유한 필드·나이 연속성·잔액 이월·흐름 부호 | `CashflowValidationTests`의 `test_result_rejects_empty_invalid_or_discontinuous_years`, `test_year_fields_validate_finite_numbers_and_flow_signs` |
| 변환·연 금액·수익·물가 거듭제곱·잔액 누적의 overflow | `CashflowArithmeticTests`의 `test_overflow_at_each_calculation_stage_is_explicit_and_safe` |
| 0원 생활비·은퇴 후 적립·범위 밖 연금의 불필요한 계산 없음 | `CashflowArithmeticTests`의 `test_zero_expenses_skip_unused_inflation_overflow`, `test_unused_retired_savings_and_inactive_pensions_do_not_overflow` |
| 별도 Decimal 참조값과 여러 경로 비교 | `CashflowArithmeticTests`의 `test_non_binary_rates_match_independent_decimal_reference_grid` |
| 샘플 실제 엔진 호출·별도 합계 검산·범위 안내 | [샘플 테스트](../tests/test_samples.py)의 `test_runner_calls_real_engine_once_per_case`, `test_sample_balances_match_independent_zero_rate_totals`, `test_runner_reports_fixed_results_with_scope_and_shortfall_notices` |
| 상세 106개 연도 행·반복 출력·반올림과 고갈 구분 | 샘플 테스트의 `test_detail_output_contains_every_year_and_is_repeatable`, `test_display_rounding_does_not_hide_depletion_age` |
| 추가 패키지 없는 CLI 실행·옵션 도움말 | 샘플 테스트의 `test_package_and_samples_run_without_site_packages`, `test_cli_help_documents_details_option` |

Decimal 참조 검증은 물가 3가지 × 연도별 수익률 경로 81가지 = **243개 조합**이다.
엔진을 참조값 생성에 사용하지 않고 50자리 Decimal과 별도 연 현금흐름 상수를
사용한다. 잔액 비교 허용오차는 상대 `1e-11`, 절대 `1e-6`원이다. 실제 엔진의
고갈 판정에는 허용오차를 적용하지 않으며 최초 고갈 나이는 정확히 비교한다.

overflow 테스트는 첫해 입력 변환뿐 아니라 중간 연도 오류도 확인한다. 오류 필드와
나이, 민감한 원문을 메시지에 포함하지 않는 성질을 확인하고, 부분 결과 대신 예외가
발생해야 한다. 모든 예외가 금융 입력을 로깅해도 안전하다는 의미는 아니므로 실제
서비스 연결 시 요청·로컬 변수·추적 도구의 로깅 정책은 별도로 검토해야 한다.

`ai-pjt/`에서 `python3 -S -B -m retirement.samples`와 `--details` 실행을 확인했다.
상세 출력의 51+23+32=106개 연도 행을 결과 모델과 비교하고, 합성 샘플의 기말 합계는
별도 산술식으로 검산했다. [사용 가이드](cashflow-engine.md)의 Python 예시도 실행해
assertion 통과를 확인했고, README·계산 규칙·엔진 가이드·검증 기록의 내부 상대
링크 27개가 실제 파일을 가리키는지 검사했다.

## 검증하지 않은 것

- 시장 가정의 현실성, 확률분포·성공확률·분위수, 대량 Monte Carlo 성능.
- What-if 시나리오 변환과 비교 정책.
- 실제 snapshot 매핑, BE 준비율·보고서 결과와의 일치, Agent 호출·HTTP·FE 표시.
- 월별 인출 타이밍·연중 지급 가능성, 세금·수수료·부동산·부채·연금 계좌 모델.
- 전체 AI/BE/FE 테스트. 이번 변경은 독립 모듈만 검증한 것이며 서비스 통합 테스트
  통과를 주장하지 않는다.

테스트 통과는 [정의한 계산 규칙](cashflow-rules.md)을 구현했다는 근거이며,
투자 결과의 정확성·수익 보장이나 금융 서비스 운영 준비 완료를 뜻하지 않는다.
