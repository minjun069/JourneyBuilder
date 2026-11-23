"""Durable local artifacts and conservative usage summaries for scenario runs."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
    Path(path).chmod(0o600)


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def revision(worktree):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(worktree), *args])
    # Include uncommitted and untracked AI files without storing their contents.
    changes = git("status", "--porcelain", "--untracked-files=all", "--", "ai-pjt")
    digest = hashlib.sha256()
    files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "ai-pjt").split(b"\0")
    for name in sorted(set(files)):
        if name:
            path = worktree / name.decode()
            digest.update(name + b"\0")
            digest.update(path.read_bytes() if path.is_file() else b"<deleted>")
    return {"commit": git("rev-parse", "HEAD").decode().strip(),
            "ai_dirty": bool(changes), "ai_tree_sha256": digest.hexdigest()}


class EventCollector:
    """Only receives events after the selected version's masking boundary."""
    def __init__(self, path, exporter=None):
        self.path, self.exporter = path, exporter
        self.events = []
        self.export_errors = 0
        self.export_failures = []

    def emit(self, payload):
        value = dict(payload)
        self.events.append(value)
        append_json(self.path, value)
        if self.exporter is not None:
            try:
                self.exporter.emit(value)
            except Exception as error:
                self.export_errors += 1
                self.export_failures.append({
                    "event_family": value.get("event_family"), "stage": value.get("stage"),
                    "operation_name": value.get("operation_name"), "error_type": type(error).__name__,
                    "trace_id": value.get("trace_id"),
                })


def measured_calls(events):
    calls = {}
    for event in events:
        if event.get("stage") != "model" or event.get("status") == "started":
            continue
        call_id = event.get("call_id")
        if call_id:
            calls[call_id] = {key: event[key] for key in (
                "call_id", "operation_name", "status", "duration_ms", "model_name",
                "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
                "reasoning_output_tokens", "usage_status", "cost_microusd",
            ) if key in event}
    return list(calls.values())


def summarize(turns):
    calls = [call for turn in turns for call in turn.get("model_calls", [])]
    missing = sum(call.get("usage_status") != "reported" for call in calls)
    latencies = sorted(turn["elapsed_ms"] for turn in turns if "elapsed_ms" in turn)
    return {"turns": len(turns), "statuses": dict(Counter(t["status"] for t in turns)),
            "model_calls": len(calls), "usage_unavailable_calls": missing,
            "reported_input_tokens": sum(c.get("input_tokens", 0) for c in calls),
            "reported_output_tokens": sum(c.get("output_tokens", 0) for c in calls),
            "usage_complete": bool(calls) and missing == 0,
            "elapsed_ms": sum(latencies),
            "median_turn_ms": (latencies[(len(latencies)-1)//2] + latencies[len(latencies)//2])/2 if latencies else None,
            "quality_evaluated": False}


def markdown_report(manifest, turns, summary):
    lines = ["# AI 시나리오 실행 결과", "", f"- 버전: `{manifest['label']}` / `{manifest['revision']['commit']}`",
             f"- 실행: `{manifest['run_id']}`", f"- 모드: {manifest['mode']}",
             "- 점수·품질 합격 판정은 수행하지 않음", "",
             "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", ""]
    for turn in turns:
        lines.extend([f"## {turn['ordinal']}. {turn['step_id']} ({turn['status']})", "",
                      f"사용자: {turn['message']}", "", f"시간: {turn.get('elapsed_ms', 0):.1f} ms", ""])
        output = turn.get("output", {})
        lines.extend([output.get("response", "") if isinstance(output, dict) else str(output), ""])
        if turn.get("error"):
            lines.extend([f"실행 오류 유형: `{turn['error']}`", ""])
        if turn.get("trace_url"):
            lines.extend([f"[Langfuse trace]({turn['trace_url']})", ""])
    return "\n".join(lines)
