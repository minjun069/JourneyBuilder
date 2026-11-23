"""Combine collected scenario artifacts without evaluating answer quality."""
from collections import Counter
import csv
import json
import math
from pathlib import Path
from statistics import median

from scenario_results import digest_file, write_json
from scenario_vanilla import user_text


VERSIONS = ("vanilla", "sep20", "master")


def collect(output):
    output = Path(output)
    manifest = json.loads((output / "suite.json").read_text())
    all_turns, stats, checks, comparisons = [], {}, [], {}
    for version in VERSIONS:
        turns, remotes, cases = [], [], 0
        for source in manifest["sources"]:
            case = source["file"].removesuffix(".input.json")
            folder = output / version / case
            if not (folder / "results.json").exists():
                checks.append({"version": version, "case": case, "status": "missing_results"})
                continue
            rows = json.loads((folder / "results.json").read_text())
            remote = json.loads((folder / "langfuse.json").read_text()) if (folder / "langfuse.json").exists() else {}
            run = json.loads((folder / "manifest.json").read_text())
            prices = {t.get("ordinal"): t for t in remote.get("traces", [])}
            original = json.loads((output / "inputs" / source["file"]).read_text())
            expected = [{"step_id": s["step_id"], "프로세스": s["프로세스"], **d} for s in original for d in s["대화"]]
            problems = []
            if digest_file(output / "inputs" / source["file"]) != source["sha256"]:
                problems.append("input_snapshot_hash_mismatch")
            if [t["ordinal"] for t in rows] != list(range(1, source["turns"] + 1)):
                problems.append("ordinal_coverage_mismatch")
            runtime = run.get("runtime", {})
            if runtime.get("llm_model") != manifest["model"] or runtime.get("llm_temperature") != 0:
                problems.append("model_configuration_mismatch")
            expected_history = []
            for row in rows:
                ordinal = row["ordinal"]
                source_turn = expected[ordinal-1]
                if row.get("message") != source_turn["사용자"] or row.get("input_delta") != source_turn["입력값"]:
                    problems.append(f"turn_{ordinal}_source_mismatch")
                if version == "vanilla":
                    expected_history.append({"role": "user", "parts": [{"text": user_text(source_turn)}]})
                    provider = row.get("provider_input", {})
                    if provider.get("contents") != expected_history or set(provider) != {"contents", "generationConfig"}:
                        problems.append(f"turn_{ordinal}_vanilla_history_mismatch")
                    if provider.get("generationConfig") != {"temperature": 0}:
                        problems.append(f"turn_{ordinal}_vanilla_temperature_mismatch")
                    if row.get("output", {}).get("response"):
                        expected_history.append({"role": "model", "parts": [{"text": row["output"]["response"]}]})
                entry = {"case": case, "version": version, **row}
                if any(c.get("model_name") != manifest["model"] for c in row.get("model_calls", [])
                       if c.get("operation_name") != "embedding.embed" and "embed" not in c.get("operation_name", "")):
                    problems.append(f"turn_{ordinal}_generation_model_mismatch")
                entry["remote_cost_usd"] = prices.get(ordinal, {}).get("total_cost_usd")
                entry["remote_cost_subtotal_usd"] = prices.get(ordinal, {}).get("reported_cost_subtotal_usd")
                all_turns.append(entry)
                turns.append(entry)
                comparisons.setdefault(case, {}).setdefault(ordinal, {})[version] = entry
            checks.append({"case": case, "version": version, "status": "pass" if not problems else "fail",
                           "problems": problems, "turns": len(rows), "langfuse": remote.get("status", "unverified"),
                           "dropped_events": run.get("dropped_events", 0), "export_errors": run.get("export_errors", 0)})
            cases += 1
            remotes.append(remote)
        calls = [c for t in turns for c in t.get("model_calls", [])]
        complete_costs = [r.get("total_cost_usd") for r in remotes]
        latencies = sorted(t.get("elapsed_ms", 0) / 1000 for t in turns)
        stats[version] = {"cases": cases, "expected_cases": len(manifest["sources"]), "turns": len(turns),
                          "expected_turns": sum(s["turns"] for s in manifest["sources"]),
                          "responses": sum(bool(t.get("output", {}).get("response")) for t in turns),
                          "statuses": dict(Counter(t["status"] for t in turns)),
                          "total_elapsed_seconds": sum(t.get("elapsed_ms", 0) for t in turns)/1000,
                          "median_elapsed_seconds": median(latencies) if latencies else None,
                          "p95_elapsed_seconds": latencies[math.ceil(len(latencies) * .95) - 1] if latencies else None,
                          "latency_population": "all recorded turns, including errors and timeouts; p95 nearest rank",
                          "model_and_embedding_calls": len(calls),
                          "input_tokens_reported": sum(c.get("input_tokens", 0) for c in calls),
                          "output_tokens_reported": sum(c.get("output_tokens", 0) for c in calls),
                          "usage_unavailable_calls": sum(c.get("usage_status") != "reported" for c in calls),
                          "remote_verification": dict(Counter(r.get("status", "unverified") for r in remotes)),
                          "duplicate_observations_excluded": sum(r.get("duplicate_observations", 0) for r in remotes),
                          "total_cost_usd": sum(complete_costs) if cases == len(manifest["sources"]) and all(isinstance(c, (int, float)) for c in complete_costs) else None,
                          "reported_cost_subtotal_usd": sum(r.get("total_cost_usd") or r.get("reported_cost_subtotal_usd", 0) for r in remotes),
                          "quality_evaluated": False}
    write_json(output / "summary.json", stats)
    write_json(output / "validation.json", checks)
    with (output / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case", "version", "turn", "status", "error", "elapsed_ms", "model_calls",
                         "input_tokens_reported", "output_tokens_reported", "usage_unavailable_calls",
                         "remote_cost_usd", "remote_cost_subtotal_usd", "response_chars", "response_space_chars", "trace_id"])
        for t in all_turns:
            calls = t.get("model_calls", [])
            response = t.get("output", {}).get("response", "")
            writer.writerow([t["case"], t["version"], t["ordinal"], t["status"], t.get("error"), t.get("elapsed_ms"),
                             len(calls), sum(c.get("input_tokens", 0) for c in calls), sum(c.get("output_tokens", 0) for c in calls),
                             sum(c.get("usage_status") != "reported" for c in calls), t.get("remote_cost_usd"),
                             t.get("remote_cost_subtotal_usd"), len(response), response.count(" "), t.get("trace_id")])
    with (output / "turns.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case", "version", "turn", "step", "status", "user", "input_values", "response", "elapsed_ms", "input_tokens", "output_tokens", "remote_cost_usd", "trace_id", "error", "usage_unavailable_calls", "remote_cost_subtotal_usd"])
        for t in all_turns:
            calls = t.get("model_calls", [])
            writer.writerow([t["case"], t["version"], t["ordinal"], t["step_id"], t["status"], t["message"],
                             json.dumps(t["input_delta"], ensure_ascii=False), t.get("output", {}).get("response", ""),
                             t.get("elapsed_ms"), sum(c.get("input_tokens", 0) for c in calls), sum(c.get("output_tokens", 0) for c in calls),
                             t.get("remote_cost_usd"), t.get("trace_id"), t.get("error"),
                             sum(c.get("usage_status") != "reported" for c in calls), t.get("remote_cost_subtotal_usd")])
    with (output / "all-results.jsonl").open("w", encoding="utf-8") as stream:
        for t in all_turns:
            stream.write(json.dumps(t, ensure_ascii=False) + "\n")
    compare_dir = output / "comparisons"
    compare_dir.mkdir(exist_ok=True, mode=0o700)
    for case, turn_map in comparisons.items():
        lines = [f"# {case} — 세 방식 응답 비교", ""]
        for ordinal, by_version in sorted(turn_map.items()):
            row = next(iter(by_version.values()))
            lines.extend([f"## {ordinal}. {row['step_id']}", "", row["message"], "", "입력값:", "```json", json.dumps(row["input_delta"], ensure_ascii=False, indent=2), "```", ""])
            for version in VERSIONS:
                t = by_version.get(version, {})
                lines.extend([f"### {version}", "", f"상태: {t.get('status', '미수집')} / 시간: {t.get('elapsed_ms', 0)/1000:.3f}초", "",
                              t.get("output", {}).get("response", "응답 없음"), ""])
        (compare_dir / f"{case}.md").write_text("\n".join(lines), encoding="utf-8")
    lines = ["# 토리금융 시나리오 — AI 실행 결과", "", f"모델: `{manifest['model']}`, temperature 0. 시나리오 {len(manifest['sources'])}개를 각각 독립 세션으로 실행.", "",
             "| 방식 | 사례 | 응답/예정 턴 | 모델·임베딩 호출 | 중앙값(초) | p95(초) | 확인된 비용 소계(USD) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for version, s in stats.items():
        lines.append(f"| {version} | {s['cases']} | {s['responses']}/{s['expected_turns']} | {s['model_and_embedding_calls']} | {s['median_elapsed_seconds'] or 0:.3f} | {s['p95_elapsed_seconds'] or 0:.3f} | {s['reported_cost_subtotal_usd']:.6f} |")
    lines.extend(["", "- 응답 수는 품질 합격 수가 아닙니다. 내부 작업 실패·계획 문제·정책 차단은 summary.json의 statuses를 확인하세요.",
                  "- Vanilla는 원문 입력값과 이전 사용자·모델 메시지 전체를 전달합니다. system prompt·도구·RAG가 없으며 Gemini 웹 제품 자체를 재현하지 않습니다.",
                  "- 제품 버전은 기존 프롬프트·마스킹·검색·대화 압축을 유지합니다. 20일은 공식 catalog 2.1.0, master는 2.2.0과 상담 후보 인덱스를 사용합니다.",
                  f"- 실행 프로세스 동시성은 {manifest['workers']}입니다. 시간에는 공급자 부하와 내부 재시도가 포함됩니다. 단일 사용자 지연 벤치마크로 단정하지 않습니다.",
                  "- 보고서 fixture를 제공하지 않았으므로 보고서 미준비 응답을 정상 차트 해석 품질로 해석하지 않습니다.",
                  "- API 사용량·가격·원격 관측이 완전하지 않으면 총비용은 null입니다. 비용 소계는 실제 청구 총액이 아닙니다.",
                  "- 시간 통계는 실패·타임아웃을 포함한 전체 턴 기준이며 p95는 최근접 순위입니다. 모델 첫 토큰 지연이 아닌 전체 턴 경과시간입니다.",
                  "", "## 파일", "", "- [분석용 지표 CSV](metrics.csv)", "- [원문 포함 전체 턴 CSV](turns.csv)", "- [전체 결과 JSONL](all-results.jsonl)",
                  "- [요약](summary.json)", "- [입력·모델·vanilla 문맥 검증](validation.json)", "", "## 사례별 비교", ""])
    for case in sorted(comparisons):
        lines.append(f"- [{case}](comparisons/{case}.md)")
    if (output / "recovery-plan.json").exists():
        lines.extend(["", "## 실행 및 관측 유의사항", "",
                      "- 연결 장애 이후 완료된 사례·정상 prefix를 재사용하고 남은 대화를 복구했습니다. 단일 연속 실행 결과가 아닙니다.",
                      "- 비용 소계는 최종 선택된 호출만 포함합니다. 복구 전 폐기된 시도·연결 진단·smoke 호출은 제외되므로 전체 실험 청구액과 다릅니다.",
                      "- 비동기 지연 이벤트는 trace·call ID로 다시 귀속하고 중복 관측 비용을 제외했습니다. 누락 요청·모델 기록의 사후 복원은 사례별 trace-restoration.json에 남겼습니다.",
                      "- 보안 이벤트 export 오류는 별도 제한입니다. 요청·모델 측정 검증 완료가 전체 관측 체계 정상이라는 의미는 아닙니다.",
                      "- [실행·관측 상세 감사](execution-audit.json)", "- [측정 해석 및 검증 기록](MEASUREMENT-NOTES.md)"])
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for path in output.rglob("*"):
        if path.is_file(): path.chmod(0o600)
    return stats


if __name__ == "__main__":
    import sys
    collect(Path(sys.argv[1]))
