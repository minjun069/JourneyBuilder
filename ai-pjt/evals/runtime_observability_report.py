"""Write the deterministic F10 runtime baseline artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.runtime_observability_baseline import build_runtime_observability_baseline
from evals.runtime_observability_fixture import reference_runtime_events


DEFAULT_OUTPUT = Path(__file__).with_name("artifacts") / "f10-runtime-observability-baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_runtime_observability_baseline(reference_runtime_events()).to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "requests": report["requests"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
