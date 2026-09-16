"""Bounded, synthetic-only performance tooling; never imported by the public API.

Time and traced allocations are measured in separate, fresh child processes.
This CLI emits benchmark metrics, not customer analysis JSON or an HTTP response.
See docs/analysis-performance.md for methodology and operational limitations.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from time import perf_counter
import tracemalloc

from .analysis import analyze_retirement
from .analysis_output import analysis_to_dict


BENCHMARK_VERSION = "retirement-analysis-benchmark-v1"
_PACKAGE_PARENT = Path(__file__).resolve().parent.parent
_MAX_WORK = 600_000  # Tool guard only: paths * years * (baseline + scenarios).
_PHASES = ("analysis_s", "conversion_s", "encoding_s", "total_s")


def _bounded_int(name: str, value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    num_paths: int
    num_years: int
    num_scenarios: int
    constant_returns: bool = False

    def __post_init__(self) -> None:
        _bounded_int("num_paths", self.num_paths, 1, 5_000)
        _bounded_int("num_years", self.num_years, 1, 81)
        _bounded_int("num_scenarios", self.num_scenarios, 0, 10)
        if type(self.constant_returns) is not bool:
            raise ValueError("constant_returns must be a bool")
        if self.path_year_evaluations > _MAX_WORK:
            raise ValueError("case exceeds the benchmark-only work budget")

    @property
    def path_year_evaluations(self) -> int:
        return self.num_paths * self.num_years * (1 + self.num_scenarios)


CASES = {
    "smoke_baseline": BenchmarkCase(10, 11, 0),
    "smoke_what_if": BenchmarkCase(10, 11, 1),
    "n100_y31_s1": BenchmarkCase(100, 31, 1),
    "n500_y31_s1": BenchmarkCase(500, 31, 1),
    "n1000_y31_s1": BenchmarkCase(1_000, 31, 1),
    "n5000_y31_s1": BenchmarkCase(5_000, 31, 1),
    "n1000_y11_s1": BenchmarkCase(1_000, 11, 1),
    "n1000_y51_s1": BenchmarkCase(1_000, 51, 1),
    "n1000_y81_s1": BenchmarkCase(1_000, 81, 1),
    "n1000_y31_s0": BenchmarkCase(1_000, 31, 0),
    "n1000_y31_s3": BenchmarkCase(1_000, 31, 3),
    "n1000_y31_s10": BenchmarkCase(1_000, 31, 10),
    "n1000_y51_s3": BenchmarkCase(1_000, 51, 3),
    "n1000_y51_s3_constant": BenchmarkCase(1_000, 51, 3, True),
}


def build_benchmark_request(case: BenchmarkCase) -> dict:
    """Fresh fixed-seed saved-shaped input, not production financial assumptions."""
    if type(case) is not BenchmarkCase:
        raise ValueError("case must be a BenchmarkCase")
    case.__post_init__()
    end_age = 40 + case.num_years - 1
    retirement_age = min(50, end_age)
    return {
        "saved_data": {
            "basic_info": {"age": 40, "retirement_age": retirement_age, "end_age": end_age},
            "financial_assets": [{"asset_type": "예금/적금", "current_value": 120_000_000,
                                  "monthly_savings": 750_000}],
            "pension_assets": [{"receive_method": "연금형", "pension_start_age": 65,
                                "pension_period": "종신", "expected_amount": 1_000_000}],
            "goal_periods": [{"phase": "after", "from_age": retirement_age, "to_age": end_age,
                              "items": [{"amount": 2_000_000}]}],
            "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
        },
        "assumption_values": {"annual_return_mean": 0.05,
                              "annual_return_volatility": 0 if case.constant_returns else 0.10,
                              "annual_inflation_rate": 0.02, "num_paths": case.num_paths, "seed": 42},
        "confirmed_empty_sections": (),
        "scenarios": [{"scenario_id": f"save_{index + 1}",
                       "changes": {"monthly_savings": 800_000 + index * 50_000}}
                      for index in range(case.num_scenarios)],
    }


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _proof(payload: dict, encoded: bytes, case: BenchmarkCase) -> dict:
    # Outside the timing/tracing window. No customer/result fields enter the report.
    if (json.loads(encoded) != payload or len(payload["scenarios"]) != case.num_scenarios
            or payload["metadata"]["num_paths"] != case.num_paths
            or len(payload["baseline"]["asset_percentiles"]) != case.num_years
            or payload["mode"] != ("what_if" if case.num_scenarios else "baseline")
            or not payload["notices"]):
        raise RuntimeError("benchmark output verification failed")
    return {"sha256": sha256(encoded).hexdigest(), "utf8_bytes": len(encoded)}


def _warm_up(request: dict) -> None:
    _encode(analysis_to_dict(analyze_retirement(**request)))
    gc.collect()


def measure_timing(case: BenchmarkCase, *, repeats: int = 3) -> dict:
    """One same-case warm-up, then independent samples; GC stays enabled."""
    _bounded_int("repeats", repeats, 1, 5)
    if tracemalloc.is_tracing() or not gc.isenabled():
        raise RuntimeError("timing requires tracing off and garbage collection enabled")
    request = build_benchmark_request(case)
    _warm_up(request)
    samples = []
    expected_proof = None
    for _ in range(repeats):
        gc.collect()  # Excluded; ordinary GC during the pipeline remains enabled.
        start = perf_counter()
        analysis = analyze_retirement(**request)
        calculated = perf_counter()
        payload = analysis_to_dict(analysis)
        converted = perf_counter()
        encoded = _encode(payload)
        ended = perf_counter()
        proof = _proof(payload, encoded, case)
        if expected_proof is not None and proof != expected_proof:
            raise RuntimeError("benchmark samples produced different outputs")
        expected_proof = proof
        samples.append({"analysis_s": calculated - start, "conversion_s": converted - calculated,
                        "encoding_s": ended - converted, "total_s": ended - start})
        del analysis, payload, encoded
    return {"samples": samples, "summary": {
        phase: {"min": min(row[phase] for row in samples), "median": median(row[phase] for row in samples),
                "max": max(row[phase] for row in samples)} for phase in _PHASES
    }, "output": expected_proof, "process_peak_rss_bytes": _peak_rss()}


def _peak_rss() -> int | None:
    # Linux ru_maxrss is KiB. Other platforms report null, never guessed units.
    if sys.platform != "linux":
        return None
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _memory_mark(start: int) -> dict:
    current, peak = tracemalloc.get_traced_memory()
    return {"start_bytes": start, "end_bytes": current, "peak_bytes": peak,
            "incremental_peak_bytes": peak - start}


def measure_memory(case: BenchmarkCase) -> dict:
    """One traced run after warm-up; peaks retain upstream objects, not RSS deltas."""
    if tracemalloc.is_tracing() or not gc.isenabled():
        raise RuntimeError("memory measurement requires tracing off and garbage collection enabled")
    request = build_benchmark_request(case)
    _warm_up(request)
    tracemalloc.start(1)
    try:
        start = tracemalloc.get_traced_memory()[0]
        analysis = analyze_retirement(**request)
        analysis_memory = _memory_mark(start)
        tracemalloc.reset_peak()
        start = tracemalloc.get_traced_memory()[0]
        payload = analysis_to_dict(analysis)
        conversion_memory = _memory_mark(start)
        tracemalloc.reset_peak()
        start = tracemalloc.get_traced_memory()[0]
        encoded = _encode(payload)
        encoding_memory = _memory_mark(start)
    finally:
        tracemalloc.stop()
    return {"analysis": analysis_memory, "conversion": conversion_memory, "encoding": encoding_memory,
            "pipeline_peak_bytes": max(m["peak_bytes"] for m in (analysis_memory, conversion_memory, encoding_memory)),
            "output": _proof(payload, encoded, case), "process_peak_rss_bytes": _peak_rss()}


def _environment() -> dict:
    digest = sha256()
    for source in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(source.name.encode("utf-8") + b"\0" + source.read_bytes() + b"\0")
    return {"python_implementation": platform.python_implementation(), "python_version": platform.python_version(),
            "system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "affinity_cpu_count": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "runtime_sources_sha256": digest.hexdigest(), "gc_enabled": gc.isenabled(),
            "timer": "time.perf_counter", "tracemalloc_frames": 1}


def run_benchmark(name: str, *, repeats: int = 3, timeout: int = 45) -> dict:
    """Two sequential fresh workers with a per-worker timeout; no partial report."""
    if type(name) is not str or name not in CASES:
        raise ValueError("name must select a built-in benchmark case")
    _bounded_int("repeats", repeats, 1, 5)
    _bounded_int("timeout", timeout, 1, 60)
    case = CASES[name]
    workers = {}
    for phase in ("timing", "memory"):
        command = [sys.executable, "-S", "-B", "-m", "retirement_simulation.analysis_benchmark",
                   "--case", name, "--repeats", str(repeats), "--_worker", phase]
        try:
            completed = subprocess.run(command, cwd=_PACKAGE_PARENT, capture_output=True, text=True,
                                       timeout=timeout, check=True)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{phase} worker timed out; no complete benchmark report") from None
        except (subprocess.CalledProcessError, OSError):
            raise RuntimeError(f"{phase} worker failed; no complete benchmark report") from None
        workers[phase] = json.loads(completed.stdout)
    timing, memory = workers["timing"], workers["memory"]
    if (timing["environment"] != memory["environment"]
            or timing["measurement"]["output"] != memory["measurement"]["output"]):
        raise RuntimeError("benchmark workers differ in code, environment or output")
    return {"benchmark_version": BENCHMARK_VERSION,
            "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "case": {"name": name, "num_paths": case.num_paths, "num_years": case.num_years,
                     "num_scenarios": case.num_scenarios, "constant_returns": case.constant_returns,
                     "path_year_evaluations": case.path_year_evaluations},
            "method": {"warmups_per_worker": 1, "timing_repeats": repeats, "memory_repeats": 1,
                       "worker_timeout_s": timeout, "isolated_sequential_workers": True},
            "environment": timing["environment"],
            "timing": timing["measurement"], "memory": memory["measurement"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m retirement_simulation.analysis_benchmark", allow_abbrev=False,
        description="Bounded synthetic performance measurements, not analysis/service output.",
        epilog="One case only; start with smoke cases. Two sequential workers, each with a timeout. No customer files or network.",
    )
    parser.add_argument("--case", choices=tuple(CASES), default="smoke_baseline")
    parser.add_argument("--repeats", type=int, default=3, help="timing samples after warm-up, 1..5")
    parser.add_argument("--timeout", type=int, default=45, help="seconds per worker, 1..60 (default: 45)")
    parser.add_argument("--_worker", choices=("timing", "memory"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        _bounded_int("repeats", args.repeats, 1, 5)
        _bounded_int("timeout", args.timeout, 1, 60)
    except ValueError as error:
        parser.error(str(error))
    try:
        if args._worker:
            case = CASES[args.case]
            measurement = measure_timing(case, repeats=args.repeats) if args._worker == "timing" else measure_memory(case)
            report = {"environment": _environment(), "measurement": measurement}
        else:
            report = run_benchmark(args.case, repeats=args.repeats, timeout=args.timeout)
        print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    except (RuntimeError, ValueError) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
