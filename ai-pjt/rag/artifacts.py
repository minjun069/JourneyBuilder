from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rag.pipeline.store import PostgresIndexStore


SCHEMA_VERSION = "rag-db-artifact/v1"
QUALIFICATION_SCHEMA_VERSION = "rag-qualification/v1"
ARTIFACT_MEMBERS = (
    "manifest.json",
    "qualification.json",
    "tables/rag_index_manifests.jsonl",
    "tables/rag_index_chunks.jsonl",
    "tables/rag_active_indexes.jsonl",
)
MAX_MEMBER_SIZE = 512 * 1024 * 1024


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return "".join(_canonical_json(row) + "\n" for row in rows).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_safe_identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if not all(character.isalnum() or character in "_-" for character in value):
        raise ValueError(f"{name} contains an unsafe character: {value!r}")
    return value


def _validate_qualification(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("qualification report root must be an object")
    if payload.get("schema_version") != QUALIFICATION_SCHEMA_VERSION:
        raise ValueError("unsupported qualification report schema")
    kind = payload.get("artifact_kind")
    if kind not in {"official", "experiment"}:
        raise ValueError("qualification report has an invalid artifact_kind")
    collections = payload.get("collections")
    if not isinstance(collections, list) or not collections:
        raise ValueError("qualification report must contain collection results")
    if payload.get("overall_decision") != "PASS" or not payload.get("activatable"):
        raise ValueError("only a fully passing qualification can be exported")
    for result in collections:
        if not isinstance(result, dict) or result.get("decision") != "PASS":
            raise ValueError("every artifact collection must have a PASS decision")
        _require_safe_identifier(
            "logical_collection", result.get("logical_collection")
        )
        physical = _require_safe_identifier(
            "physical_collection", result.get("physical_collection")
        )
        if f"__{kind}-" not in physical:
            raise ValueError("physical collection does not encode artifact_kind")
    if kind == "official":
        logical_collections = {
            result["logical_collection"] for result in collections
        }
        if logical_collections != {"interface-guides", "financial-knowledge"}:
            raise ValueError("official artifact must contain both operational collections")
        embedding = payload.get("embedding")
        if embedding != {
            "provider": "gemini",
            "model": "gemini-embedding-2",
            "dimensions": 3072,
            "endpoint": "gms",
        }:
            raise ValueError(
                "official artifact requires gemini-embedding-2 with 3072 dimensions"
            )
        query_counts = {
            result["logical_collection"]: result.get("evaluation", {}).get(
                "query_count"
            )
            for result in collections
        }
        if query_counts != {"interface-guides": 20, "financial-knowledge": 38}:
            raise ValueError("official artifact must contain all 58 qualification queries")
        if not payload.get("deployable"):
            raise ValueError("official artifact must be marked deployable")
        if not payload.get("operator_id"):
            raise ValueError("official artifact must identify its RAG owner")
        activation = payload.get("activation")
        if not isinstance(activation, dict) or not activation.get("performed"):
            raise ValueError("official artifact requires a completed producer activation")
    return payload


def _read_qualification(path: Path) -> dict[str, Any]:
    return _validate_qualification(json.loads(path.read_text(encoding="utf-8")))


def _validate_manifest_binding(
    manifest: dict[str, Any], qualification: dict[str, Any]
) -> None:
    if qualification.get("qualification_id") != manifest.get("artifact_id"):
        raise ValueError("qualification and artifact IDs differ")
    if qualification.get("artifact_kind") != manifest.get("artifact_kind"):
        raise ValueError("qualification and artifact kinds differ")
    if manifest.get("catalog") != qualification.get("catalog"):
        raise ValueError("qualification and artifact catalog metadata differ")
    if manifest.get("calculation_policy") != qualification.get(
        "calculation_policy"
    ):
        raise ValueError("qualification and artifact calculation policies differ")
    if manifest.get("embedding") != qualification.get("embedding"):
        raise ValueError("qualification and artifact embedding metadata differ")
    expected_physical = [
        result["physical_collection"] for result in qualification["collections"]
    ]
    database_scope = manifest.get("database_scope")
    if not isinstance(database_scope, dict) or database_scope.get(
        "physical_collections"
    ) != expected_physical:
        raise ValueError("qualification and artifact collection scopes differ")
    expected_qualification = {
        "overall_decision": qualification["overall_decision"],
        "activatable": qualification["activatable"],
        "deployable": qualification["deployable"],
    }
    if manifest.get("qualification") != expected_qualification:
        raise ValueError("qualification and artifact decisions differ")


def _connect(database_url: str) -> Any:
    if not database_url.strip():
        raise ValueError("DATABASE_URL is required")
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError("psycopg is required for RAG artifact operations") from error
    return psycopg.connect(database_url)


def _export_rows(
    database_url: str, physical_collections: list[str], logical_collections: list[str]
) -> dict[str, list[dict[str, Any]]]:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT physical_collection, logical_collection, manifest,
                       created_at::text, retired_at::text
                FROM rag_index_manifests
                WHERE physical_collection = ANY(%s)
                ORDER BY physical_collection
                """,
                (physical_collections,),
            )
            manifests = [
                {
                    "physical_collection": row[0],
                    "logical_collection": row[1],
                    "manifest": row[2] if isinstance(row[2], dict) else json.loads(row[2]),
                    "created_at": row[3],
                    "retired_at": row[4],
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT physical_collection, chunk_id, source_id, chunk,
                       embedding::text
                FROM rag_index_chunks
                WHERE physical_collection = ANY(%s)
                ORDER BY physical_collection, chunk_id
                """,
                (physical_collections,),
            )
            chunks = [
                {
                    "physical_collection": row[0],
                    "chunk_id": row[1],
                    "source_id": row[2],
                    "chunk": row[3] if isinstance(row[3], dict) else json.loads(row[3]),
                    "embedding": row[4],
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT logical_collection, physical_collection, activated_at::text
                FROM rag_active_indexes
                WHERE logical_collection = ANY(%s)
                ORDER BY logical_collection
                """,
                (logical_collections,),
            )
            active = [
                {
                    "logical_collection": row[0],
                    "physical_collection": row[1],
                    "activated_at": row[2],
                }
                for row in cursor.fetchall()
            ]
    return {
        "rag_index_manifests": manifests,
        "rag_index_chunks": chunks,
        "rag_active_indexes": active,
    }


def _validate_export_rows(
    qualification: dict[str, Any], rows: dict[str, list[dict[str, Any]]]
) -> None:
    expected = {
        result["physical_collection"]: result["manifest"]
        for result in qualification["collections"]
    }
    actual = {
        row["physical_collection"]: row["manifest"]
        for row in rows["rag_index_manifests"]
    }
    if actual != expected:
        raise ValueError("database manifests differ from the qualification report")
    counts: dict[str, int] = {physical: 0 for physical in expected}
    for row in rows["rag_index_chunks"]:
        physical = row["physical_collection"]
        if physical not in counts:
            raise ValueError("logical backup contains an out-of-scope physical index")
        counts[physical] += 1
    expected_counts = {
        physical: int(manifest["chunk_count"])
        for physical, manifest in expected.items()
    }
    if counts != expected_counts:
        raise ValueError("database chunk counts differ from the qualified manifests")
    active = {
        row["logical_collection"]: row["physical_collection"]
        for row in rows["rag_active_indexes"]
    }
    expected_active = {
        result["logical_collection"]: result["physical_collection"]
        for result in qualification["collections"]
    }
    if qualification["artifact_kind"] == "official" and active != expected_active:
        raise ValueError("producer active indexes differ from the official candidate")


def export_artifact(
    *, database_url: str, qualification_path: Path, output_dir: Path
) -> tuple[Path, dict[str, Any]]:
    qualification = _read_qualification(qualification_path)
    artifact_id = _require_safe_identifier(
        "qualification_id", qualification.get("qualification_id")
    )
    kind = qualification["artifact_kind"]
    collections = qualification["collections"]
    physical = [result["physical_collection"] for result in collections]
    logical = [result["logical_collection"] for result in collections]
    rows = _export_rows(database_url, physical, logical)
    _validate_export_rows(qualification, rows)

    qualification_bytes = _json_bytes(qualification)
    data_files = {
        "qualification.json": qualification_bytes,
        "tables/rag_index_manifests.jsonl": _jsonl_bytes(
            rows["rag_index_manifests"]
        ),
        "tables/rag_index_chunks.jsonl": _jsonl_bytes(rows["rag_index_chunks"]),
        "tables/rag_active_indexes.jsonl": _jsonl_bytes(
            rows["rag_active_indexes"]
        ),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "producer": {
            "operator_id": qualification.get("operator_id"),
            "environment_id": qualification["environment_id"],
            "source_revision": qualification["source_revision"],
        },
        "database_scope": {
            "mode": "logical-table-backup",
            "tables": [
                "rag_index_manifests",
                "rag_index_chunks",
                "rag_active_indexes",
            ],
            "physical_collections": physical,
        },
        "catalog": qualification["catalog"],
        "calculation_policy": qualification["calculation_policy"],
        "embedding": qualification["embedding"],
        "qualification": {
            "overall_decision": qualification["overall_decision"],
            "activatable": qualification["activatable"],
            "deployable": qualification["deployable"],
        },
        "active_snapshot": rows["rag_active_indexes"],
        "producer_smoke": {
            "manifest_match": True,
            "chunk_count_match": True,
            "active_pointer_match": (
                qualification["artifact_kind"] != "official"
                or {
                    row["logical_collection"]: row["physical_collection"]
                    for row in rows["rag_active_indexes"]
                }
                == {
                    result["logical_collection"]: result["physical_collection"]
                    for result in qualification["collections"]
                }
            ),
        },
        "files": {
            name: {"sha256": _sha256(content), "size_bytes": len(content)}
            for name, content in data_files.items()
        },
    }
    manifest_bytes = _json_bytes(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{kind}__{artifact_id}.tar.gz"
    if output_path.exists():
        raise ValueError(f"artifact already exists: {output_path}")
    with tarfile.open(output_path, "w:gz") as archive:
        for name, content in {"manifest.json": manifest_bytes, **data_files}.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    result = {
        "artifact": str(output_path.resolve()),
        "size_bytes": output_path.stat().st_size,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "git_tracking_candidate": output_path.stat().st_size <= 20 * 1024 * 1024,
    }
    return output_path, result


def _load_archive(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = tuple(member.name for member in members)
        if set(names) != set(ARTIFACT_MEMBERS) or len(names) != len(ARTIFACT_MEMBERS):
            raise ValueError("artifact archive has unexpected members")
        if any(
            not member.isfile() or member.size > MAX_MEMBER_SIZE for member in members
        ):
            raise ValueError("artifact archive contains an invalid member")
        files = {}
        for member in members:
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"cannot read artifact member: {member.name}")
            files[member.name] = extracted.read()
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported artifact manifest schema")
    expected_files = manifest.get("files")
    if not isinstance(expected_files, dict):
        raise ValueError("artifact manifest does not declare file checksums")
    for name, content in files.items():
        if name == "manifest.json":
            continue
        metadata = expected_files.get(name)
        if not isinstance(metadata, dict):
            raise ValueError(f"artifact manifest omits {name}")
        if metadata.get("sha256") != _sha256(content) or metadata.get(
            "size_bytes"
        ) != len(content):
            raise ValueError(f"artifact member checksum differs: {name}")
    return manifest, files


def _parse_jsonl(content: bytes, name: str) -> list[dict[str, Any]]:
    rows = []
    for line_number, raw in enumerate(content.decode("utf-8").splitlines(), start=1):
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"{name}:{line_number} must contain an object")
        rows.append(payload)
    return rows


def inspect_artifact(path: Path) -> dict[str, Any]:
    manifest, files = _load_archive(path)
    qualification = _validate_qualification(
        json.loads(files["qualification.json"].decode("utf-8"))
    )
    _validate_manifest_binding(manifest, qualification)
    return {
        "artifact": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "manifest": manifest,
    }


def _current_physical_rows(cursor: Any, physical: str) -> tuple[dict[str, Any] | None, bytes]:
    cursor.execute(
        """
        SELECT logical_collection, manifest, created_at::text, retired_at::text
        FROM rag_index_manifests WHERE physical_collection = %s
        """,
        (physical,),
    )
    manifest_row = cursor.fetchone()
    cursor.execute(
        """
        SELECT physical_collection, chunk_id, source_id, chunk, embedding::text
        FROM rag_index_chunks WHERE physical_collection = %s
        ORDER BY chunk_id
        """,
        (physical,),
    )
    chunks = [
        {
            "physical_collection": row[0],
            "chunk_id": row[1],
            "source_id": row[2],
            "chunk": row[3] if isinstance(row[3], dict) else json.loads(row[3]),
            "embedding": row[4],
        }
        for row in cursor.fetchall()
    ]
    if manifest_row is None:
        return None, _jsonl_bytes(chunks)
    return (
        {
            "physical_collection": physical,
            "logical_collection": manifest_row[0],
            "manifest": (
                manifest_row[1]
                if isinstance(manifest_row[1], dict)
                else json.loads(manifest_row[1])
            ),
            "created_at": manifest_row[2],
            "retired_at": manifest_row[3],
        },
        _jsonl_bytes(chunks),
    )


def import_artifact(
    *, database_url: str, artifact_path: Path, activate: bool = False
) -> dict[str, Any]:
    manifest, files = _load_archive(artifact_path)
    qualification = _validate_qualification(
        json.loads(files["qualification.json"].decode("utf-8"))
    )
    _validate_manifest_binding(manifest, qualification)
    qualification_summary = manifest.get("qualification")
    if activate and (
        manifest.get("artifact_kind") != "official"
        or not isinstance(qualification_summary, dict)
        or not qualification_summary.get("deployable")
    ):
        raise ValueError("only a deployable official artifact may be activated")
    manifests = _parse_jsonl(
        files["tables/rag_index_manifests.jsonl"], "rag_index_manifests"
    )
    chunks = _parse_jsonl(
        files["tables/rag_index_chunks.jsonl"], "rag_index_chunks"
    )
    active_rows = _parse_jsonl(
        files["tables/rag_active_indexes.jsonl"], "rag_active_indexes"
    )
    _validate_export_rows(
        qualification,
        {
            "rag_index_manifests": manifests,
            "rag_index_chunks": chunks,
            "rag_active_indexes": active_rows,
        },
    )
    expected_physical = manifest.get("database_scope", {}).get(
        "physical_collections"
    )
    if not isinstance(expected_physical, list) or set(expected_physical) != {
        row.get("physical_collection") for row in manifests
    }:
        raise ValueError("artifact physical collection scope is inconsistent")
    chunks_by_physical = {
        physical: [
            row for row in chunks if row.get("physical_collection") == physical
        ]
        for physical in expected_physical
    }
    if sum(len(rows) for rows in chunks_by_physical.values()) != len(chunks):
        raise ValueError("artifact contains out-of-scope chunks")

    store = PostgresIndexStore(database_url)
    store.ensure_schema()
    inserted = []
    reused = []
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            for row in manifests:
                physical = row["physical_collection"]
                current_manifest, current_chunks = _current_physical_rows(
                    cursor, physical
                )
                expected_chunks = _jsonl_bytes(chunks_by_physical[physical])
                if current_manifest is not None:
                    if (
                        current_manifest["logical_collection"]
                        != row["logical_collection"]
                        or current_manifest["manifest"] != row["manifest"]
                        or current_chunks != expected_chunks
                    ):
                        raise ValueError(
                            f"existing physical index conflicts with artifact: {physical}"
                        )
                    reused.append(physical)
                    continue
                if current_chunks:
                    raise ValueError(
                        f"orphan chunks conflict with artifact: {physical}"
                    )
                cursor.execute(
                    """
                    INSERT INTO rag_index_manifests (
                        physical_collection, logical_collection, manifest,
                        created_at, retired_at
                    ) VALUES (%s, %s, %s::jsonb, %s::timestamptz, %s::timestamptz)
                    """,
                    (
                        physical,
                        row["logical_collection"],
                        _canonical_json(row["manifest"]),
                        row["created_at"],
                        row["retired_at"],
                    ),
                )
                cursor.executemany(
                    """
                    INSERT INTO rag_index_chunks (
                        physical_collection, chunk_id, source_id, chunk, embedding
                    ) VALUES (%s, %s, %s, %s::jsonb, %s::vector)
                    """,
                    [
                        (
                            chunk["physical_collection"],
                            chunk["chunk_id"],
                            chunk["source_id"],
                            _canonical_json(chunk["chunk"]),
                            chunk["embedding"],
                        )
                        for chunk in chunks_by_physical[physical]
                    ],
                )
                inserted.append(physical)
    activations = {
        row["logical_collection"]: row["physical_collection"]
        for row in manifests
    }
    if activate:
        store.activate_many(activations)
    return {
        "artifact": str(artifact_path.resolve()),
        "artifact_id": manifest["artifact_id"],
        "inserted_physical_collections": inserted,
        "reused_physical_collections": reused,
        "activation_performed": activate,
        "active_indexes": {
            logical: store.active_collection(logical) for logical in activations
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export, inspect, and import table-scoped RAG DB artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--qualification", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--artifact", type=Path, required=True)
    restore = subparsers.add_parser("import")
    restore.add_argument("--artifact", type=Path, required=True)
    restore.add_argument("--activate", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_artifact(args.artifact)
        else:
            database_url = os.environ.get("DATABASE_URL", "")
            if args.command == "export":
                _, result = export_artifact(
                    database_url=database_url,
                    qualification_path=args.qualification,
                    output_dir=args.output_dir,
                )
            elif args.command == "import":
                result = import_artifact(
                    database_url=database_url,
                    artifact_path=args.artifact,
                    activate=args.activate,
                )
            else:
                raise ValueError(f"unsupported command: {args.command}")
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
