#!/usr/bin/env python3
"""Fail when Compose, application readers, and committed env examples drift."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENV_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
ENV_SECTION = re.compile(r"^# env-contract: (required|optional)$")
COMPOSE_INTERPOLATION = re.compile(
    r"(?<!\$)\$\{(?P<name>[A-Z][A-Z0-9_]*)"
    r"(?=:\?|\?|:-|-|\})"
    r"(?P<operator>:\?|\?|:-|-)?(?P<argument>[^}]*)\}"
)
YAML_KEY = re.compile(r"^([A-Z][A-Z0-9_]*):(?:\s|$)")
FRONT_ENV_ACCESS = re.compile(
    r"(?:process\.env|import\.meta\.env)(?:\?\.|\.)(VITE_[A-Z0-9_]+)"
)

ROOT_COMPOSE_FILES = (Path("docker-compose.yml"), Path("docker-compose.prod.yml"))
SERVICE_COMPOSE_FILES = (*ROOT_COMPOSE_FILES, Path("docker-compose.e2e.yml"))

# monitoring.py intentionally iterates over these names instead of calling
# os.environ.get with a literal, so the AST reader needs the explicit pair.
DYNAMIC_PYTHON_ENV = {
    "ai-pjt": {"LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"},
}


def read_env_example(
    path: Path,
    *,
    require_sections: bool = False,
) -> tuple[dict[str, str | None], list[str]]:
    """Return assignment names and optional required/optional section labels."""

    entries: dict[str, str | None] = {}
    errors: list[str] = []
    section: str | None = None
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        section_match = ENV_SECTION.fullmatch(line)
        if section_match:
            section = section_match.group(1)
            continue
        assignment = ENV_ASSIGNMENT.fullmatch(line)
        if not assignment:
            continue
        name = assignment.group(1)
        if name in entries:
            errors.append(f"{path}: {name} is assigned more than once")
            continue
        if require_sections and section is None:
            errors.append(
                f"{path}:{line_number}: {name} must be under an env-contract section"
            )
        entries[name] = section
    return entries, errors


def parse_compose_variables(
    text: str,
    *,
    source: str,
) -> tuple[dict[str, str], list[str]]:
    """Classify Compose interpolation as required or defaulted/optional."""

    variables: dict[str, str] = {}
    errors: list[str] = []
    for match in COMPOSE_INTERPOLATION.finditer(text):
        name = match.group("name")
        operator = match.group("operator")
        if operator in {":?", "?"}:
            category = "required"
        elif operator in {":-", "-"}:
            category = "optional"
        else:
            errors.append(
                f"{source}: ${{{name}}} must declare :? for required or :- for optional"
            )
            continue
        previous = variables.get(name)
        if previous is not None and previous != category:
            errors.append(
                f"{source}: {name} is both {previous} and {category} across Compose"
            )
        variables[name] = category
    return variables, errors


def collect_compose_variables(
    repository_root: Path,
    paths: tuple[Path, ...],
) -> tuple[dict[str, str], list[str]]:
    variables: dict[str, str] = {}
    errors: list[str] = []
    for relative_path in paths:
        path = repository_root / relative_path
        parsed, parse_errors = parse_compose_variables(
            path.read_text(encoding="utf-8"),
            source=relative_path.as_posix(),
        )
        errors.extend(parse_errors)
        for name, category in parsed.items():
            previous = variables.get(name)
            if previous is not None and previous != category:
                errors.append(
                    f"{relative_path}: {name} conflicts with another Compose file "
                    f"({previous} vs {category})"
                )
            variables[name] = category
    return variables, errors


def _is_environment_get(function: ast.expr) -> bool:
    if isinstance(function, ast.Name):
        normalized = function.id.lstrip("_")
        return normalized.startswith("read_") or function.id == "require_secret"
    if not isinstance(function, ast.Attribute):
        return False
    if function.attr == "getenv":
        return isinstance(function.value, ast.Name) and function.value.id == "os"
    if function.attr != "get":
        return False
    if isinstance(function.value, ast.Name):
        return function.value.id in {"environ", "active_environ"}
    return (
        isinstance(function.value, ast.Attribute)
        and function.value.attr == "environ"
        and isinstance(function.value.value, ast.Name)
        and function.value.value.id == "os"
    )


def scan_python_environment(repository_root: Path, service_dir: str) -> set[str]:
    """Find literal environment reads in production Python sources."""

    keys: set[str] = set(DYNAMIC_PYTHON_ENV.get(service_dir, set()))
    ignored_parts = {"tests", "contract_tests", "migrations", "__pycache__", ".venv"}
    for path in (repository_root / service_dir).rglob("*.py"):
        if ignored_parts.intersection(path.parts) or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first_argument = node.args[0]
            if not (
                isinstance(first_argument, ast.Constant)
                and isinstance(first_argument.value, str)
                and ENV_ASSIGNMENT.fullmatch(f"{first_argument.value}=")
            ):
                continue
            if _is_environment_get(node.func):
                keys.add(first_argument.value)
    return keys


def scan_front_environment(repository_root: Path) -> set[str]:
    keys: set[str] = set()
    front_root = repository_root / "front-pjt"
    for suffix in ("*.js", "*.mjs", "*.vue", "*.ts"):
        for path in front_root.rglob(suffix):
            if {"node_modules", "dist"}.intersection(path.parts):
                continue
            keys.update(FRONT_ENV_ACCESS.findall(path.read_text(encoding="utf-8")))
    return keys


def collect_service_compose_environment(
    repository_root: Path,
    paths: tuple[Path, ...],
) -> dict[str, set[str]]:
    """Read explicit environment mapping keys for application services."""

    result = {"backend-pjt": set(), "ai-pjt": set(), "front-pjt": set()}
    service_targets = {
        "backend": "backend-pjt",
        "migrate": "backend-pjt",
        "rag-bootstrap": "ai-pjt",
        "ai-pjt": "ai-pjt",
        "front": "front-pjt",
    }

    for relative_path in paths:
        lines = (repository_root / relative_path).read_text(encoding="utf-8").splitlines()
        in_services = False
        in_backend_anchor = False
        current_service: str | None = None
        environment_indent: int | None = None

        for raw_line in lines:
            content = raw_line.strip()
            if not content or content.startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))

            if indent == 0:
                in_services = content == "services:"
                in_backend_anchor = content.startswith("x-backend-environment:")
                current_service = None
                environment_indent = None
                continue

            if in_backend_anchor:
                if indent == 2:
                    key_match = YAML_KEY.match(content)
                    if key_match:
                        result["backend-pjt"].add(key_match.group(1))
                continue

            if not in_services:
                continue
            if indent == 2 and content.endswith(":"):
                current_service = content[:-1]
                environment_indent = None
                continue
            if indent == 4 and content.startswith("environment:"):
                environment_indent = 4 if content == "environment:" else None
                continue
            if environment_indent is None or indent <= environment_indent:
                if indent <= 4:
                    environment_indent = None
                continue
            if indent != environment_indent + 2:
                continue

            target = service_targets.get(current_service or "")
            key_match = YAML_KEY.match(content)
            if target and key_match:
                result[target].add(key_match.group(1))

    return result


def _compare_exact(
    *,
    label: str,
    actual: set[str],
    expected: set[str],
) -> list[str]:
    errors: list[str] = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"{label}: missing {', '.join(missing)}")
    if extra:
        errors.append(f"{label}: not recognized {', '.join(extra)}")
    return errors


def validate_repository(repository_root: Path = REPOSITORY_ROOT) -> list[str]:
    errors: list[str] = []

    root_example, example_errors = read_env_example(
        repository_root / ".env.example",
        require_sections=True,
    )
    root_compose, compose_errors = collect_compose_variables(
        repository_root,
        ROOT_COMPOSE_FILES,
    )
    errors.extend(example_errors)
    errors.extend(compose_errors)
    errors.extend(
        _compare_exact(
            label=".env.example vs production Compose",
            actual=set(root_example),
            expected=set(root_compose),
        )
    )
    for name in sorted(set(root_example) & set(root_compose)):
        if root_example[name] != root_compose[name]:
            errors.append(
                f".env.example: {name} is {root_example[name]}, "
                f"but Compose declares it {root_compose[name]}"
            )

    compose_service_keys = collect_service_compose_environment(
        repository_root,
        SERVICE_COMPOSE_FILES,
    )
    expected_service_keys = {
        "backend-pjt": scan_python_environment(repository_root, "backend-pjt")
        | compose_service_keys["backend-pjt"],
        "ai-pjt": scan_python_environment(repository_root, "ai-pjt")
        | compose_service_keys["ai-pjt"],
        "front-pjt": scan_front_environment(repository_root)
        | compose_service_keys["front-pjt"],
    }
    for service_dir, expected in expected_service_keys.items():
        path = repository_root / service_dir / ".env.example"
        example, service_errors = read_env_example(path)
        errors.extend(service_errors)
        errors.extend(
            _compare_exact(
                label=f"{service_dir}/.env.example vs recognized variables",
                actual=set(example),
                expected=expected,
            )
        )

    ops_example, ops_example_errors = read_env_example(
        repository_root / "ai-pjt/rag/ops.env.example",
        require_sections=True,
    )
    ops_compose, ops_compose_errors = collect_compose_variables(
        repository_root,
        (Path("ai-pjt/rag/docker-compose.yml"),),
    )
    errors.extend(ops_example_errors)
    errors.extend(ops_compose_errors)
    missing_ops = sorted(set(ops_compose) - set(ops_example))
    if missing_ops:
        errors.append(
            "ai-pjt/rag/ops.env.example vs RAG Compose: missing "
            + ", ".join(missing_ops)
        )
    for name in sorted(set(ops_example) & set(ops_compose)):
        if ops_example[name] != ops_compose[name]:
            errors.append(
                f"ai-pjt/rag/ops.env.example: {name} is {ops_example[name]}, "
                f"but RAG Compose declares it {ops_compose[name]}"
            )

    return errors


def main() -> int:
    errors = validate_repository()
    if errors:
        print("environment contract: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        "environment contract: PASS "
        "(root Compose, service readers, examples, RAG operator)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
