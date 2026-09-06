"""Disposable clean-start and readiness exercise for the G1 gate."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent
SECRET_NAMES = {
    "DJANGO_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "AI_SERVICE_BEARER_TOKEN",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
}


class RuntimeGateFailure(RuntimeError):
    """Raised when the disposable runtime does not satisfy a G1 invariant."""


def _redact(text: str, secrets_to_mask: list[str]) -> str:
    redacted = text
    for value in sorted(secrets_to_mask, key=len, reverse=True):
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _run(
    command: list[str],
    *,
    environ: dict[str, str],
    secrets_to_mask: list[str],
    expected_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environ,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in expected_codes:
        output = _redact(result.stdout + result.stderr, secrets_to_mask)
        tail = "\n".join(output.splitlines()[-80:])
        raise RuntimeGateFailure(
            f"command failed with exit {result.returncode}: {command[0]} {command[1]}\n{tail}"
        )
    return result


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _check_no_resources(
    project_name: str,
    environ: dict[str, str],
    secrets_to_mask: list[str],
) -> None:
    filters = (
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project_name}"],
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project_name}"],
        ["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project_name}"],
    )
    for command in filters:
        result = _run(
            command,
            environ=environ,
            secrets_to_mask=secrets_to_mask,
        )
        if result.stdout.strip():
            raise RuntimeGateFailure("G1 cleanup left Docker resources behind")


def main() -> int:
    project_name = f"retirewise-g1-{os.getpid()}"
    postgres_password = secrets.token_urlsafe(24)
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "DJANGO_DEBUG": "false",
        "POSTGRES_DB": "retirewise_g1",
        "POSTGRES_USER": "retirewise_g1",
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            "postgresql://retirewise_g1:"
            f"{postgres_password}@postgres:5432/retirewise_g1"
        ),
        "AI_SERVICE_BEARER_TOKEN": secrets.token_urlsafe(32),
        "LLM_PROVIDER": "gemini",
        "EMBEDDING_PROVIDER": "gemini",
        "GOOGLE_API_KEY": "g1-disposable-provider-key",
        "LANGFUSE_ENABLED": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
    secrets_to_mask = [values[name] for name in SECRET_NAMES if name in values]
    environ = {
        key: value
        for key, value in os.environ.items()
        if key not in SECRET_NAMES
    }

    try:
        with tempfile.TemporaryDirectory(prefix="retirewise-g1-") as temp_dir:
            env_path = Path(temp_dir) / "g1.env"
            missing_env_path = Path(temp_dir) / "missing.env"
            _write_env(env_path, values)
            _write_env(missing_env_path, {})

            compose = [
                "docker",
                "compose",
                "--project-name",
                project_name,
                "--env-file",
                str(env_path),
            ]
            missing_compose = [
                "docker",
                "compose",
                "--project-name",
                project_name,
                "--env-file",
                str(missing_env_path),
            ]

            print(
                "G1 runtime: validating fail-closed Compose configuration",
                flush=True,
            )
            _run(
                [*missing_compose, "config", "--quiet"],
                environ=environ,
                secrets_to_mask=secrets_to_mask,
                expected_codes=tuple(range(1, 256)),
            )
            _run(
                [*compose, "config", "--quiet"],
                environ=environ,
                secrets_to_mask=secrets_to_mask,
            )

            try:
                print("G1 runtime: building clean images", flush=True)
                _run(
                    [*compose, "build", "--pull", "--no-cache"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )

                print("G1 runtime: starting disposable services", flush=True)
                _run(
                    [*compose, "up", "-d", "--wait", "--wait-timeout", "240"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )
                for service in ("ai-pjt", "backend"):
                    _run(
                        [*compose, "exec", "-T", service, "python", "readiness.py"],
                        environ=environ,
                        secrets_to_mask=secrets_to_mask,
                    )

                print(
                    "G1 runtime: exercising dependency failure semantics",
                    flush=True,
                )
                _run(
                    [*compose, "exec", "-T", "ai-pjt", "env", "READINESS_TIMEOUT_SECONDS=0", "python", "readiness.py"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                    expected_codes=(1,),
                )

                _run(
                    [*compose, "stop", "postgres"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )
                for service in ("ai-pjt", "backend"):
                    _run(
                        [*compose, "exec", "-T", service, "python", "readiness.py"],
                        environ=environ,
                        secrets_to_mask=secrets_to_mask,
                        expected_codes=(1,),
                    )
                _run(
                    [*compose, "up", "-d", "--wait", "--wait-timeout", "120", "postgres"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )
                for service in ("ai-pjt", "backend"):
                    _run(
                        [*compose, "exec", "-T", service, "python", "readiness.py"],
                        environ=environ,
                        secrets_to_mask=secrets_to_mask,
                    )

                _run(
                    [*compose, "stop", "redis"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )
                _run(
                    [*compose, "exec", "-T", "backend", "python", "readiness.py"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                    expected_codes=(1,),
                )
                _run(
                    [*compose, "exec", "-T", "ai-pjt", "python", "readiness.py"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )
                _run(
                    [*compose, "up", "-d", "--wait", "--wait-timeout", "120", "redis"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )
                _run(
                    [*compose, "exec", "-T", "backend", "python", "readiness.py"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )

                logs = _run(
                    [*compose, "logs", "--no-color"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                ).stdout
                leaked = [value for value in secrets_to_mask if value and value in logs]
                if leaked:
                    raise RuntimeGateFailure("runtime logs exposed a configured secret")
            except RuntimeGateFailure as exc:
                logs = _run(
                    [*compose, "logs", "--no-color"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                ).stdout
                log_tail = "\n".join(
                    _redact(logs, secrets_to_mask).splitlines()[-120:]
                )
                raise RuntimeGateFailure(f"{exc}\nservice logs:\n{log_tail}") from exc
            finally:
                print("G1 runtime: removing disposable resources", flush=True)
                _run(
                    [*compose, "down", "--volumes", "--remove-orphans"],
                    environ=environ,
                    secrets_to_mask=secrets_to_mask,
                )

        _check_no_resources(project_name, environ, secrets_to_mask)
    except RuntimeGateFailure as exc:
        print(f"G1 runtime: FAIL: {exc}", file=sys.stderr)
        return 1

    print("G1 runtime: PASS (clean start, readiness, failure semantics, cleanup)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
