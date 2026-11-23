"""Execute scenario-v1 against one worktree per process, without HTTP services.

Use the selected version's Python environment. --dry-run validates all inputs
without importing a model. See README.md for commands and boundaries.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import runpy
import sys
import traceback
from time import perf_counter, sleep
from uuid import uuid4
from urllib.parse import urlsplit

# Also support `python -m evals.scenario_runner`; historical checkouts do not need
# a copy of this runner or of the new schema.
RUNNER_DIR = Path(__file__).resolve().parent
if str(RUNNER_DIR) not in sys.path:
    sys.path.append(str(RUNNER_DIR))
from scenario_inputs import load_registry, load_scenario, prepare_turns
from scenario_results import (EventCollector, append_json, digest_file, markdown_report,
                              measured_calls, revision, summarize, write_json)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing results are never overwritten")
    parser.add_argument("--schema", type=Path, default=RUNNER_DIR / "schemas/ai-evaluation-scenario-v1.schema.json")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--label", default="candidate")
    parser.add_argument("--mode", choices=("graph", "vanilla"), default="graph")
    parser.add_argument("--preserve-input-values", action="store_true", help="Preserve authored choices outside form enums; report warnings instead of coercing values")
    parser.add_argument("--database-url", help="Read-only RAG database connection; never included in artifacts")
    parser.add_argument("--report-fixtures", type=Path, help="JSON keyed by step_id; chart_payload/report_snapshot/tool_context")
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180, help="Seconds per turn, including graph/tool calls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--runtime-preflight", action="store_true", help="Construct the graph locally; do not invoke models or export traces")
    parser.add_argument("--no-langfuse", action="store_true")
    parser.add_argument("--environment", default="benchmark")
    parser.add_argument("--no-readback", action="store_true", help="Skip remote verification; export remains unverified")
    parser.add_argument("--readback-only", action="store_true", help="Refresh Langfuse results in an existing run without model calls")
    parser.add_argument("--refresh-readback", action="store_true", help="With --readback-only, refetch verified traces too (for updated model prices)")
    parser.add_argument("--continue-from", type=Path, help="Continue a saved prefix in a new output directory without repeating recorded turns")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.timeout <= 0 or (args.max_turns is not None and args.max_turns < 1):
        parser.error("repeat, timeout and max-turns must be positive")
    if not args.environment.startswith("benchmark"):
        parser.error("environment must start with benchmark to keep evaluation out of production")
    if args.readback_only and (args.no_langfuse or args.no_readback or args.dry_run or args.runtime_preflight):
        parser.error("readback-only cannot be combined with disabled export/readback or local preflight")
    if args.refresh_readback and not args.readback_only:
        parser.error("refresh-readback requires readback-only")
    if args.continue_from and (args.repeat != 1 or args.readback_only):
        parser.error("continue-from requires repeat=1 and cannot be combined with readback-only")
    for name in ("worktree", "input", "output", "schema", "env_file", "report_fixtures"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    return args


def configure_environment(args, manifest):
    if args.env_file:
        from dotenv import dotenv_values
        for key, value in dotenv_values(args.env_file).items():
            if value is not None:
                os.environ.setdefault(key, value)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    legacy = (args.worktree / "ai-pjt/core/model_observation.py").exists()
    os.environ["LANGFUSE_ENABLED"] = "false" if legacy or args.no_langfuse else "true"
    os.environ["LANGFUSE_MASKED_ENABLED"] = "false" if args.no_langfuse else "true"
    os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = args.environment
    os.environ["LANGFUSE_RELEASE"] = f"{args.label}-{manifest['revision']['commit'][:12]}-{manifest['revision']['ai_tree_sha256'][:12]}"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    # A previously disabled OTel SDK must not silently turn a requested run into
    # a non-exporting run. Fail before invoking any models.
    if not args.no_langfuse and os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        raise ValueError("OTEL_SDK_DISABLED=true conflicts with Langfuse collection")
    sys.path.insert(0, str(args.worktree / "ai-pjt"))
    os.chdir(args.worktree / "ai-pjt")


def runtime_metadata(settings):
    metadata = {key: getattr(settings, key, None) for key in (
        "llm_provider", "llm_model", "llm_temperature", "embedding_provider", "embedding_model",
        "prompt_release", "counseling_rag_enabled", "plan_validation_enabled", "langfuse_release",
        "rag_top_k", "rag_fetch_k", "rag_min_dense_score")}
    packages = {}
    for name in ("langfuse", "langgraph", "langchain-core", "langchain-google-genai", "langchain-openai", "jsonschema"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {**metadata, "python": sys.version.split()[0], "executable": sys.executable, "packages": packages,
            "model_endpoint_host": urlsplit(getattr(settings, "gemini_base_url", "") or getattr(settings, "llm_base_url", "")).hostname}


def index_metadata(settings):
    import psycopg
    with psycopg.connect(settings.db_url, connect_timeout=5) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT logical_collection, physical_collection FROM rag_active_indexes ORDER BY logical_collection")
            indexes = dict(cursor.fetchall())
    missing = {"financial-knowledge", "interface-guides"} - indexes.keys()
    if getattr(settings, "counseling_rag_enabled", False) and "counseling-candidate" not in indexes:
        missing.add("counseling-candidate")
    if missing:
        raise ValueError("missing RAG indexes: " + ", ".join(sorted(missing)))
    return indexes


def _readback_get(client, path):
    for attempt in range(4):
        response = client.get(path)
        if response.status_code != 429 or attempt == 3:
            return response
        try:
            delay = float(response.headers.get("retry-after", "60"))
        except (TypeError, ValueError):
            delay = 60
        sleep(max(1, min(delay, 60)))


def readback(settings, turns, previous=None):
    """Read only this run's trace IDs; no raw content is retained or printed."""
    import httpx
    from scenario_observations import audit_trace
    report = {"status": "unverified", "traces": [], "total_cost_usd": None,
              "verification": "trace and call IDs; terminal usage checked; duplicate calls charged once"}
    with httpx.Client(base_url=settings.langfuse_base_url.rstrip("/"),
                      auth=(settings.langfuse_public_key, settings.langfuse_secret_key), timeout=20) as client:
        project_id = None
        projects = _readback_get(client, "/api/public/projects")
        if projects.is_success:
            data = projects.json().get("data", [])
            if len(data) == 1:
                project_id = data[0]["id"]
        for turn in turns:
            trace_id = turn.get("trace_id")
            cached = next((t for t in (previous or {}).get("traces", [])
                           if t.get("trace_id") == trace_id and t.get("complete") and (previous or {}).get("verification")
                           and not any(c.get("status") == "unfinished" for c in turn.get("model_calls", []))
                           and {m.get("call_id") for m in t.get("measurements", [])} == {c["call_id"] for c in turn.get("model_calls", [])}), None)
            if cached:
                report["traces"].append(deepcopy(cached))
                continue
            entry = {"ordinal": turn["ordinal"], "repetition": turn["repetition"], "trace_id": trace_id}
            if not trace_id:
                entry["status"] = "missing_local_trace"
                report["traces"].append(entry)
                continue
            response = _readback_get(client, f"/api/public/traces/{trace_id}")
            if not response.is_success:
                entry.update(status="unavailable", http_status=response.status_code)
            else:
                trace = response.json()
                entry.update(audit_trace(turn, trace))
                if project_id:
                    turn["trace_url"] = f"{settings.langfuse_base_url.rstrip('/')}/project/{project_id}/traces/{trace_id}"
            report["traces"].append(entry)
    complete = bool(turns) and all(x.get("complete") for x in report["traces"])
    usage_complete = all(c.get("usage_status") == "reported" for t in turns for c in t.get("model_calls", []))
    costs = [x.get("total_cost_usd") for x in report["traces"]]
    report["status"] = "verified" if complete else "incomplete"
    # A missing provider count or absent price is not a zero-cost run.
    prices_complete = all(x.get("priced_observations") == x.get("expected_calls") for x in report["traces"])
    if complete and usage_complete and prices_complete and all(isinstance(x, (int, float)) for x in costs):
        report["total_cost_usd"] = sum(costs)
        report["cost_status"] = "langfuse_estimate_deduplicated"
    else:
        report["cost_status"] = "unavailable_or_partial"
    report["reported_cost_subtotal_usd"] = sum(x.get("reported_cost_subtotal_usd", 0) for x in report["traces"])
    report["duplicate_observations"] = sum(x.get("duplicate_observations", 0) for x in report["traces"])
    return report


async def execute(args, manifest, prepared, fixtures):
    from core.config import settings
    from core.langfuse_exporter import build_langfuse_instrumentation_exporter
    from core.runtime_instrumentation import RuntimeStage
    from scenario_runtime import (build_runtime, graph_input, result_status,
                                  bind_request, request_start, request_succeed)
    settings.validate_runtime()
    manifest["runtime"] = runtime_metadata(settings)
    write_json(args.output / "manifest.json", manifest)
    # Read-only readiness check before the first potentially billable call.
    manifest["rag_indexes"] = index_metadata(settings) if args.mode == "graph" else {}
    exporter = None
    if not args.no_langfuse:
        exporter = build_langfuse_instrumentation_exporter(
            public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url, release=settings.langfuse_release)
    collector = EventCollector(args.output / "observations.jsonl", exporter)
    turns = []
    if getattr(args, "continue_from", None):
        from scenario_resume import read_rows, validate_prefix
        turns = read_rows(args.continue_from)
        validate_prefix(turns, prepared)
        for record in turns:
            append_json(args.output / "turns.jsonl", record)
        previous_events = args.continue_from / "observations.jsonl"
        if previous_events.exists():
            for line in previous_events.read_text().splitlines():
                try:
                    append_json(args.output / "observations.jsonl", json.loads(line))
                except json.JSONDecodeError:
                    break
    reused = len(turns)
    write_json(args.output / "manifest.json", manifest)
    dropped = 0
    try:
        for repetition in range(1, args.repeat + 1):
            if args.mode == "vanilla":
                from scenario_vanilla import build_vanilla_runtime
                graph, instrumentation, sink, context = build_vanilla_runtime(collector)
            else:
                graph, instrumentation, sink, context = build_runtime(collector)
            if reused:
                from scenario_resume import restore_context
                context = restore_context(graph, context, turns, args.mode)
            # Old constructors use SDK defaults absent from Settings.
            model_module = sys.modules.get("core.llm")
            manifest["runtime"]["effective_model_temperature"] = getattr(
                getattr(model_module, "llm", None), "temperature", None)
            write_json(args.output / "manifest.json", manifest)
            thread_id = f"eval-{manifest['run_id']}-{repetition}"
            if reused:
                thread_id = turns[-1]["graph_input"]["thread_id"]
            for turn in prepared[reused:]:
                request_id = f"{thread_id}-{turn['ordinal']}"
                if reused:
                    request_id += f"-resume-{manifest['run_id']}"
                state = graph_input(turn, context, request_id, thread_id, fixtures)
                if args.mode == "vanilla":
                    state["raw_turn"] = deepcopy(turn["raw_turn"])
                record = {k: deepcopy(v) for k, v in turn.items() if k != "field_registry"}
                record.update(repetition=repetition, request_id=request_id, graph_input=deepcopy(state))
                record["report_supplied"] = turn["step_id"] in fixtures
                offset = len(collector.events)
                observed = bind_request(instrumentation, request_id, thread_id, turn["step_id"])
                span = request_start(observed, sink, RuntimeStage.REQUEST_PROCESSING,
                                     {"message": turn["message"], "step_id": turn["step_id"]})
                started = perf_counter()
                try:
                    # Fresh checkpoint per request, explicitly carrying only the
                    # completed conversation; failed plans/actions never leak.
                    result = await asyncio.wait_for(graph.ainvoke(
                        state, config={"configurable": {"thread_id": request_id}}), timeout=args.timeout)
                    record["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
                    output = result.get("output")
                    if not isinstance(output, dict) or not isinstance(output.get("response"), str) or not output["response"].strip():
                        raise ValueError("graph did not return an output response")
                    record.update(status=result_status(result), output=output,
                                  plan_issues=result.get("plan_issues", []), task_results=result.get("task_results", []))
                    if "provider_input" in result:
                        record["provider_input"] = result["provider_input"]
                        record["finish_reason"] = result.get("finish_reason")
                    context = result.get("conversation_context", context)
                    request_succeed(observed, sink, span, output)
                except Exception as error:
                    record.update(status="timeout" if isinstance(error, TimeoutError) else "error",
                                  error=type(error).__name__, elapsed_ms=round((perf_counter() - started) * 1000, 3))
                    if args.mode == "vanilla" and "provider_input" in state:
                        record["provider_input"] = deepcopy(state["provider_input"])
                    if hasattr(sink, "set_content"):
                        sink.set_content(output={"error": record["error"]})
                    observed.fail(span, reason_code="evaluation_turn_failed")
                events = collector.events[offset:]
                record["model_calls"] = measured_calls(events)
                root = next((e for e in events if e.get("stage") == "request_processing"), None)
                if root and root.get("trace_id"):
                    record["trace_id"] = hashlib.sha256(f"retirewise:{root['trace_id']}".encode()).hexdigest()[:32]
                turns.append(record)
                append_json(args.output / "turns.jsonl", record)
                write_json(args.output / "summary.json", summarize(turns))
                print(json.dumps({"version": args.label, "turn": turn["ordinal"], "repeat": repetition,
                                  "step": turn["step_id"], "status": record["status"],
                                  "elapsed_ms": record["elapsed_ms"], "calls": len(record["model_calls"])}, ensure_ascii=False), flush=True)
            dropped += sink.dropped_exports
    finally:
        if exporter is not None:
            try:
                exporter.flush()
                manifest["flush"] = "completed"
            except Exception as error:
                manifest["flush"] = type(error).__name__
            finally:
                try:
                    exporter.shutdown()
                    manifest["shutdown"] = "completed"
                except Exception as error:
                    manifest["shutdown"] = type(error).__name__
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["dropped_events"] = dropped
        manifest["export_errors"] = collector.export_errors
        manifest["export_failures"] = collector.export_failures
        write_json(args.output / "manifest.json", manifest)
    remote = {"status": "disabled" if args.no_langfuse else "unverified", "total_cost_usd": None}
    if exporter is not None and not args.no_readback:
        for delay in (2, 5, 10):
            await asyncio.sleep(delay)
            try:
                remote = await asyncio.to_thread(readback, settings, turns, remote)
            except Exception as error:
                remote = {"status": "unverified", "error": type(error).__name__, "total_cost_usd": None}
            if remote["status"] == "verified":
                break
    write_json(args.output / "langfuse.json", remote)
    summary = {**summarize(turns), "langfuse": remote["status"],
               "total_cost_usd": remote.get("total_cost_usd"), "cost_status": remote.get("cost_status", "unavailable"),
               "dropped_events": dropped, "export_errors": collector.export_errors}
    write_json(args.output / "summary.json", summary)
    write_json(args.output / "results.json", turns)
    report = args.output / "report.md"
    report.write_text(markdown_report(manifest, turns, summary), encoding="utf-8")
    report.chmod(0o600)
    failed = any(t["status"] in {"error", "timeout"} for t in turns)
    unverified = exporter is not None and not args.no_readback and remote["status"] != "verified"
    export_shutdown_failed = exporter is not None and (
        manifest.get("flush") != "completed" or manifest.get("shutdown") != "completed")
    return 1 if failed or dropped or collector.export_errors or unverified or export_shutdown_failed else 0


def main(argv=None):
    args = parse_args(argv)
    if args.readback_only:
        manifest = json.loads((args.output / "manifest.json").read_text())
        turns = json.loads((args.output / "results.json").read_text())
        changes = []
        events_path = args.output / "observations.jsonl"
        if events_path.exists():
            from scenario_observations import reconcile_calls
            changes = reconcile_calls(turns, (json.loads(line) for line in events_path.read_text().splitlines()))
            if changes:
                write_json(args.output / "measurement-reconciliation.json", changes)
        args.label = manifest["label"]
        configure_environment(args, manifest)
        from core.config import settings
        previous_path = args.output / "langfuse.json"
        previous = json.loads(previous_path.read_text()) if previous_path.exists() and not args.refresh_readback else None
        for entry in (previous or {}).get("traces", []):
            if entry.get("trace_id") in {change["trace_id"] for change in changes}:
                entry["complete"] = False
        remote = readback(settings, turns, previous)
        manifest["readback_updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["readback_runner_sha256"] = digest_file(Path(__file__))
        write_json(args.output / "manifest.json", manifest)
        summary = {**summarize(turns), "langfuse": remote["status"], "total_cost_usd": remote.get("total_cost_usd"),
                   "cost_status": remote.get("cost_status", "unavailable"),
                   "dropped_events": manifest.get("dropped_events", 0), "export_errors": manifest.get("export_errors", 0)}
        write_json(args.output / "langfuse.json", remote)
        write_json(args.output / "results.json", turns)
        write_json(args.output / "summary.json", summary)
        (args.output / "report.md").write_text(markdown_report(manifest, turns, summary), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if remote["status"] == "verified" else 1
    # Validate the entire file even when a smoke run requests only its prefix.
    data = load_scenario(args.input, args.schema)
    if args.mode == "vanilla":
        from scenario_vanilla import raw_turns
        prepared = raw_turns(data)
    else:
        prepared = prepare_turns(data, load_registry(args.worktree), args.preserve_input_values)
    if args.continue_from:
        from scenario_resume import read_rows, validate_prefix
        previous = json.loads((args.continue_from / "manifest.json").read_text())
        if (previous["input_sha256"] != digest_file(args.input)
                or previous["label"] != args.label
                or previous["mode"] != ("vanilla" if args.mode == "vanilla" else "graph-direct")):
            raise ValueError("continuation input, version or mode differs")
        validate_prefix(read_rows(args.continue_from), prepared[:args.max_turns])
    fixtures = json.loads(args.report_fixtures.read_text()) if args.report_fixtures else {}
    if not isinstance(fixtures, dict) or any(not isinstance(v, dict) or set(v) - {"chart_payload", "report_snapshot", "tool_context"} for v in fixtures.values()):
        raise ValueError("report fixtures must map step IDs to chart_payload/report_snapshot/tool_context")
    unknown_steps = set(fixtures) - {turn["step_id"] for turn in prepared}
    if unknown_steps:
        raise ValueError("report fixture contains unknown scenario steps: " + ", ".join(sorted(unknown_steps)))
    state_fields = runpy.run_path(str(args.worktree / "ai-pjt/graph/state.py"))["ChatState"].__annotations__
    for fixture in fixtures.values():
        if "tool_context" in fixture and "tool_context" not in state_fields:
            raise ValueError("this version has no graph tool_context; use its report_snapshot contract")
    if args.output.exists():
        raise ValueError("output directory already exists; choose a new directory")
    args.output.mkdir(parents=True, mode=0o700)
    manifest = {"run_id": uuid4().hex[:16], "label": args.label, "worktree": str(args.worktree),
                "revision": revision(args.worktree), "input_sha256": digest_file(args.input),
                "schema_sha256": digest_file(args.schema), "runner_sha256": {p.name: digest_file(p) for p in RUNNER_DIR.glob("scenario_*.py")},
                "report_fixtures_sha256": digest_file(args.report_fixtures) if args.report_fixtures else None,
                "started_at": datetime.now(timezone.utc).isoformat(), "mode": "vanilla" if args.mode == "vanilla" else "graph-direct",
                "preserve_input_values": args.preserve_input_values,
                "turns_available": len(prepared), "turns_selected": min(args.max_turns or len(prepared), len(prepared)),
                "repeat": args.repeat, "timeout_seconds": args.timeout, "environment": args.environment,
                "boundaries": ["no frontend/backend HTTP", "in-memory context; no background summary/archive retrieval",
                               "scenario deltas are confirmed inputs; AI actions are not applied",
                               "user turns use request_mode=chat; no automatic UI entry/error-review requests",
                               "report not fabricated when fixture is absent", "no quality scoring"]}
    if args.mode == "vanilla":
        manifest["boundaries"] = ["plain Gemini API, not the Gemini web product", "no system prompt, tools, RAG or product masking of model input",
                                  "original authored input values", "all prior user and model turns retained within each scenario",
                                  "observation export remains masked", "no quality scoring"]
    write_json(args.output / "manifest.json", manifest)
    write_json(args.output / "prepared.json", prepared)
    if args.continue_from:
        manifest["continued_from"] = str(args.continue_from.resolve())
    if args.dry_run:
        print(json.dumps({"status": "validated", "turns": len(prepared), "output": str(args.output)}))
        return 0
    configure_environment(args, manifest)
    logging.disable(logging.CRITICAL)  # SDK logs can contain prompts/provider URLs.
    try:
        if args.runtime_preflight:
            from core.config import settings
            from scenario_runtime import build_runtime
            manifest["runtime"] = runtime_metadata(settings)
            write_json(args.output / "manifest.json", manifest)
            build_runtime(EventCollector(args.output / "observations.jsonl"))
            write_json(args.output / "preflight.json", {"status": "graph_constructed", "provider_calls": 0, "langfuse_exports": 0})
            print(json.dumps({"status": "graph_constructed", "output": str(args.output)}))
            return 0
        return asyncio.run(execute(args, manifest, prepared[:args.max_turns], fixtures))
    except Exception as error:
        write_json(args.output / "run_error.json", {
            "error": type(error).__name__,
            "frames": [{"file": f.filename, "line": f.lineno, "function": f.name}
                       for f in traceback.extract_tb(error.__traceback__)[-10:]],
        })
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Do not expose connection strings or provider exception bodies.
        print(f"scenario runner failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(2)
