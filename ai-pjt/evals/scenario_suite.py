"""Run a scenario folder in isolated processes and collect three-way artifacts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import threading
from time import perf_counter

from scenario_inputs import load_scenario, load_registry, prepare_turns
from scenario_results import digest_file, write_json, summarize

_print_lock = threading.Lock()
_print = print


def print(*args, **kwargs):
    with _print_lock:
        _print(*args, **kwargs)


def publish_results(destination, target):
    """Keep the existing judge input contract, including failed/partial turns."""
    source = destination / "results.json"
    journal = destination / "turns.jsonl"
    if source.exists():
        rows = json.loads(source.read_text())
    elif journal.exists():
        rows = []
        for line in journal.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                break  # A killed writer may leave its final journal line incomplete.
    else:
        return
    events = destination / "observations.jsonl"
    if events.exists():
        from scenario_observations import reconcile_calls
        complete_events = []
        for line in events.read_text().splitlines():
            try:
                complete_events.append(json.loads(line))
            except json.JSONDecodeError:
                break
        reconcile_calls(rows, complete_events)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(target, rows)


def print_totals(output, results, state):
    """One log entry per version; unknown usage/prices are never called zero."""
    for label in ("vanilla", "sep20", "master"):
        rows, remote = [], []
        for item in results:
            if item["version"] != label:
                continue
            directory = output / label / item["case"]
            source = directory / "results.json"
            if source.exists():
                rows.extend(json.loads(source.read_text()))
            costs = directory / "langfuse.json"
            remote.append(json.loads(costs.read_text()) if costs.exists() else {})
        costs_complete = bool(remote) and all(r.get("total_cost_usd") is not None for r in remote)
        print(json.dumps({"state": state, "version": label, **summarize(rows),
                          "total_cost_usd": sum(r["total_cost_usd"] for r in remote) if costs_complete else None,
                          "reported_cost_subtotal_usd": sum(r.get("reported_cost_subtotal_usd", 0) for r in remote),
                          "cost_complete": costs_complete}, ensure_ascii=False), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True, help="Version to python/worktree/database_url mapping; never copied to artifacts")
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--cases", nargs="*", help="Optional case stems, e.g. case-10")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--results-only", action="store_true", help="Keep only VERSION/CASE.results.json; print totals to stdout")
    p.add_argument("--validate-only", action="store_true", help="Validate all inputs and paths without model calls or artifacts")
    p.add_argument("--reuse-run", type=Path, action="append", default=[], help="Previous internal run directory; reuse completed cases and continue saved prefixes")
    args = p.parse_args(argv)
    if args.results_only and args.resume:
        p.error("--results-only requires a new output directory; completed model calls are never replayed automatically")
    if args.workers < 1 or args.workers > 24:
        p.error("workers must be between 1 and 24")
    runner = Path(__file__).resolve().with_name("scenario_runner.py")
    schema = runner.parent / "schemas/ai-evaluation-scenario-v1.schema.json"
    config = json.loads(args.config.read_text())
    files = sorted(args.dataset.resolve().glob("case-*.input.json"))
    if args.cases:
        files = [f for f in files if f.name.removesuffix(".input.json") in args.cases]
    if not files or set(config) != {"vanilla", "sep20", "master"}:
        p.error("requires scenario files and exactly vanilla, sep20, master configurations")
    if not args.env_file.is_file():
        p.error("env file does not exist")
    for item in config.values():
        if not Path(item["python"]).is_file() or not Path(item["worktree"]).is_dir():
            p.error("configured python or worktree does not exist")
    # Prevalidate every scenario against both versions before any paid request.
    sources = []
    for path in files:
        data = load_scenario(path, schema)
        for label in ("sep20", "master"):
            prepare_turns(data, load_registry(Path(config[label]["worktree"])), True)
        sources.append({"file": path.name, "sha256": digest_file(path), "turns": sum(len(s["대화"]) for s in data)})
    for root in args.reuse_run:
        old = json.loads((root / "suite.json").read_text())
        if old["sources"] != sources or old["model"] != "gemini-2.5-flash-lite" or old["temperature"] != 0:
            p.error("reuse-run dataset or model settings differ")
    if args.validate_only:
        print(json.dumps({"state": "validated", "cases": len(files), "versions": 3,
                          "turns": 3 * sum(s["turns"] for s in sources), "model_calls": 0}))
        return 0
    output = args.output.resolve()
    if output.exists() and not args.resume:
        p.error("output exists; use --resume to reuse completed cases")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="scenario-suite-") as scratch:
        return run_suite(args, config, files, sources, runner,
                         Path(scratch) if args.results_only else output,
                         output if args.results_only else None)


def run_suite(args, config, files, sources, runner, output, public_output):
    started = perf_counter()
    settings = {"dataset": str(args.dataset.resolve()), "sources": sources, "model": "gemini-2.5-flash-lite", "temperature": 0,
                "workers": args.workers, "started_at": datetime.now(timezone.utc).isoformat(),
                "runner_sha256": {f.name: digest_file(f) for f in runner.parent.glob("scenario_*.py")}}
    if public_output:
        print(json.dumps({"state": "started", **settings}), flush=True)
    manifest_path = output / "suite.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old["sources"] != sources or old["runner_sha256"] != settings["runner_sha256"]:
            raise ValueError("resume input or runner hash differs; use a new output directory")
    else:
        write_json(manifest_path, settings)
    inputs = output / "inputs"
    inputs.mkdir(exist_ok=True, mode=0o700)
    for path in files:
        target = inputs / path.name
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
    # A single reader avoids multiplying Langfuse read-API rate limits while
    # independent model runs continue. Never repeat billable calls for readback.
    readbacks = ThreadPoolExecutor(max_workers=1)
    readback_futures = []
    def read_remote(command, env, destination):
        with (destination / "readback.log").open("w") as log:
            read_command = [arg for arg in command if arg != "--no-readback"]
            if "--continue-from" in read_command:
                index = read_command.index("--continue-from")
                del read_command[index:index + 2]
            result = subprocess.run(read_command + ["--readback-only"], env=env, stdout=log, stderr=subprocess.STDOUT)
        if public_output:
            publish_results(destination, public_output / destination.parent.name / f"{destination.name}.results.json")
            summary = json.loads((destination / "summary.json").read_text())
            remote_path = destination / "langfuse.json"
            remote = json.loads(remote_path.read_text()) if remote_path.exists() else {}
            print(json.dumps({"state": "measured", "version": destination.parent.name,
                              "case": destination.name, "exit_code": result.returncode, **summary,
                              "reported_cost_subtotal_usd": remote.get("reported_cost_subtotal_usd")}), flush=True)
        return result.returncode
    def run(label, path):
        item = config[label]
        case = path.name.removesuffix(".input.json")
        destination = output / label / case
        if destination.exists():
            return {"version": label, "case": case, "state": "reused" if (destination / "results.json").exists() else "partial_not_replayed"}
        destination.parent.mkdir(exist_ok=True, mode=0o700)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LLM_PROVIDER="gemini", LLM_MODEL=settings["model"],
                   LLM_TEMPERATURE="0", GOOGLE_GEMINI_BASE_URL="https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com")
        command = [item["python"], str(runner), "--worktree", item["worktree"], "--input", str(inputs/path.name),
                   "--env-file", str(args.env_file.resolve()), "--label", label, "--output", str(destination),
                   "--mode", "vanilla" if label == "vanilla" else "graph", "--preserve-input-values", "--no-readback"]
        if item.get("database_url"):
            command += ["--database-url", item["database_url"]]
        from scenario_resume import choose_run
        expected = next(s["turns"] for s in sources if s["file"] == path.name)
        prior, complete = choose_run(args.reuse_run, label, case, expected)
        if complete:
            shutil.copytree(prior, destination)
            if not (destination / "results.json").exists():
                from scenario_resume import read_rows
                write_json(destination / "results.json", read_rows(prior))
            if public_output:
                publish_results(destination, public_output / label / f"{case}.results.json")
            readback_futures.append(readbacks.submit(read_remote, command, env, destination))
            result = {"version": label, "case": case, "state": "reused"}
            print(json.dumps(result), flush=True)
            return result
        if prior:
            command += ["--continue-from", str(prior.resolve())]
        log = destination.parent / f"{case}.log"
        print(json.dumps({"version": label, "case": case, "state": "started"}), flush=True)
        with log.open("w") as stream:
            log.chmod(0o600)
            proc = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if public_output:
            publish_results(destination, public_output / label / f"{case}.results.json")
        if (destination / "results.json").exists():
            readback_futures.append(readbacks.submit(read_remote, command, env, destination))
        result = {"version": label, "case": case, "exit_code": proc.returncode,
                  "state": "collected" if (destination / "results.json").exists() else "failed"}
        write_json(destination.parent / f"{case}.exit.json", result)
        print(json.dumps(result), flush=True)
        return result
    jobs = [(label, path) for path in files for label in ("vanilla", "sep20", "master")]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, *job) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
            write_json(output / "progress.json", results)
    readbacks.shutdown(wait=True)
    readback_failed = any([future.result() != 0 for future in readback_futures])
    failed = readback_failed or any(r.get("exit_code", 0) != 0 or r["state"] in {"failed", "partial_not_replayed"} for r in results)
    if public_output:
        print_totals(output, results, "finished_with_errors" if failed else "finished")
        print(json.dumps({"state": "finished_with_errors" if failed else "finished",
                          "wall_elapsed_ms": round((perf_counter() - started) * 1000, 3),
                          "cases": len(results), "exit_code": int(failed)}), flush=True)
    else:
        from scenario_comparison import collect
        collect(output)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
