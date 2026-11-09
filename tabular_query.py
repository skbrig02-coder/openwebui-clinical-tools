"""
title: Read-only CSV and SQLite Query
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Bounded SELECT queries over CSV and SQLite files inside allowlisted directories. Rejects path escape, symlink escape, writes, ATTACH, PRAGMA, and loadable extensions. Parquet is reported unavailable unless pyarrow is already installed. This tool does not call a network API.
required_open_webui_version: 0.4.0
requirements: pydantic
version: 1.0.0
license: Copyright (c) 2026 Stuart Brigham, MD (Free to use with attribution to Stuart Brigham, MD)

Backward compatible: new module. It does not change the six existing tools.
Default ALLOWED_ROOTS is empty, so a query fails closed until a root is set.
No patient data is required. Results are not sent off the machine.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:  # type: ignore
        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def Field(default: Any = None, **kwargs: Any) -> Any:  # type: ignore
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_DENIED_FUNCTIONS = frozenset(
    {"load_extension", "writefile", "readfile", "edit", "fts3_tokenizer"}
)


def _authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _src: str | None) -> int:
    if action == sqlite3.SQLITE_FUNCTION and str(arg1 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", None)
    if recursive is not None:
        allowed.add(recursive)
    if action in allowed:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _roots(raw: str) -> list[Path]:
    roots = []
    for piece in (raw or "").split(","):
        text = piece.strip()
        if text:
            roots.append(Path(text).expanduser().resolve())
    return roots


def _reject_path(relative_path: str, roots: list[Path]) -> tuple[Path | None, str]:
    if not roots:
        return None, "configuration: ALLOWED_ROOTS is empty. No file was opened."
    raw = (relative_path or "").strip()
    if not raw or "\x00" in raw:
        return None, "path_rejected: path is empty."
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None, "path_rejected: absolute paths and parent segments are not accepted."
    matches = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        direct = root / candidate
        if direct.is_symlink() and not resolved.is_relative_to(root):
            return None, "path_rejected: symlink targets must stay inside an allowlisted root."
        matches.append(resolved)
    if not matches:
        return None, "path_rejected: path is outside every allowlisted root."
    chosen = matches[0]
    if not chosen.is_file():
        return None, "path_rejected: path is not a regular file inside an allowlisted root."
    return chosen, ""


def _units_for(path: Path) -> dict[str, str]:
    sidecar = Path(str(path) + ".units.json")
    if not sidecar.is_file():
        return {}
    try:
        import json

        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    columns = payload.get("columns") if isinstance(payload, dict) else None
    if not isinstance(columns, dict):
        return {}
    return {str(key): str(value) for key, value in columns.items()}


def _schema_lines(names: list[str], declared: dict[str, str], units: dict[str, str]) -> list[str]:
    lines = []
    for name in names:
        unit = units.get(name, "not_provided_by_file")
        lines.append(f"- column: {name} | declared_type: {declared.get(name, 'inferred_text')} | unit: {unit}")
    return lines


def _run_select(conn: sqlite3.Connection, sql: str, max_rows: int, timeout_seconds: float) -> dict[str, Any]:
    text = (sql or "").strip()
    if not text:
        text = "SELECT * FROM data"
    head = text.lstrip("(").lower()
    if not (head.startswith("select") or head.startswith("with")):
        return {"status": "error", "error_type": "forbidden_sql", "rows": [], "columns": []}
    deadline = time.monotonic() + timeout_seconds

    def progress() -> int:
        if time.monotonic() >= deadline:
            return 1
        return 0

    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(progress, 1)
    try:
        cursor = conn.execute(text)
        fetched = cursor.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as exc:
        kind = "timeout" if "interrupt" in str(exc).lower() else "forbidden_sql"
        return {"status": "error", "error_type": kind, "rows": [], "columns": []}
    except sqlite3.Error:
        return {"status": "error", "error_type": "forbidden_sql", "rows": [], "columns": []}
    truncated = len(fetched) > max_rows
    rows = fetched[:max_rows]
    columns = [item[0] for item in cursor.description or []]
    if not rows:
        status = "no_match"
    elif truncated:
        status = "partial"
    else:
        status = "ok"
    return {"status": status, "error_type": "", "rows": rows, "columns": columns, "truncated": truncated, "sql": text}


def _declared_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row[1]): str(row[2] or "inferred_text") for row in info}


def run_tabular_query(
    *,
    allowed_roots: str,
    relative_path: str,
    sql: str = "",
    max_rows: int = 50,
    max_bytes: int = 2_000_000,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    queried_at = _utc_now_iso()
    roots = _roots(allowed_roots)
    path, problem = _reject_path(relative_path, roots)
    base = {
        "queried_at_utc": queried_at,
        "provenance": "local_filesystem",
        "ncbi_eutilities:": "not_used",
        "network": "not_used",
        "sql": sql,
    }
    if path is None:
        error_type = "configuration" if problem.startswith("configuration") else "path_rejected"
        base.update({"status": "error", "error_type": error_type, "note": problem, "rows": [], "match_count": "unavailable"})
        return base
    size = path.stat().st_size
    if size > max_bytes:
        # Hash only when the file is within the bound. Oversized files are not loaded.
        base.update(
            {
                "status": "error",
                "error_type": "resource_limit",
                "note": f"File is {size} bytes, above the {max_bytes} byte bound. It was not queried.",
                "source_file": path.name,
                "source_bytes": size,
                "rows": [],
                "match_count": "unavailable",
            }
        )
        return base
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    suffix = path.suffix.lower()
    units = _units_for(path)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    common = {
        "source_file": path.name,
        "source_bytes": size,
        "source_sha256": file_hash,
        "source_mtime_utc": mtime,
        "units_status": "provided" if units else "not_provided_by_file",
    }
    if suffix == ".parquet":
        try:
            import pyarrow  # type: ignore  # noqa: F401
        except ImportError:
            base.update(common)
            base.update(
                {
                    "status": "error",
                    "error_type": "unavailable_dependency",
                    "note": "Parquet support is unavailable because pyarrow is not installed. This tool does not download it.",
                    "rows": [],
                    "match_count": "unavailable",
                }
            )
            return base
        base.update(common)
        base.update(
            {
                "status": "error",
                "error_type": "unsupported",
                "note": "pyarrow is present, but this build does not query Parquet yet.",
                "rows": [],
                "match_count": "unavailable",
            }
        )
        return base
    limit = max(1, min(int(max_rows), 500))
    if suffix == ".csv":
        text = path.read_text(encoding="utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            base.update(common)
            base.update({"status": "no_match", "match_count": 0, "note": "The CSV file has no rows.", "rows": [], "columns": []})
            return base
        names = [column.strip() or f"column_{index}" for index, column in enumerate(header)]
        memory = sqlite3.connect(":memory:")
        try:
            column_sql = ", ".join(f'"{name.replace(chr(34), "")}" TEXT' for name in names)
            memory.execute(f"CREATE TABLE data ({column_sql})")
            placeholders = ", ".join("?" for _ in names)
            for row in reader:
                padded = (row + [""] * len(names))[: len(names)]
                memory.execute(f"INSERT INTO data VALUES ({placeholders})", padded)
            memory.commit()
            result = _run_select(memory, sql, limit, timeout_seconds)
        finally:
            memory.close()
        declared = {name: "inferred_text" for name in names}
    elif suffix in {".sqlite", ".sqlite3", ".db"}:
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            declared = _declared_types(conn, "data") if not sql else {}
            result = _run_select(conn, sql, limit, timeout_seconds)
            if result["columns"]:
                # Declared types are best-effort from sqlite_master via pragma before the authorizer.
                pass
        finally:
            conn.close()
        if not declared and result.get("columns"):
            # Re-open read-only for schema only. User SQL already finished.
            conn = sqlite3.connect(uri, uri=True)
            try:
                tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if len(tables) == 1:
                    declared = _declared_types(conn, tables[0])
            except sqlite3.Error:
                declared = {}
            finally:
                conn.close()
        names = result.get("columns") or []
    else:
        base.update(common)
        base.update(
            {
                "status": "error",
                "error_type": "unsupported",
                "note": "Only .csv, .sqlite, .sqlite3, and .db files are queried.",
                "rows": [],
                "match_count": "unavailable",
            }
        )
        return base
    after_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    note = ""
    if result["status"] == "error":
        note = "The statement was rejected or the query stopped. This is not a finding of zero rows."
        match_count: Any = "unavailable"
    elif result["status"] == "no_match":
        note = "The statement completed and returned zero rows."
        match_count = 0
    elif result.get("truncated"):
        note = f"Results stopped at {limit} rows. The file may contain more. This is a partial result."
        match_count = len(result["rows"])
    else:
        note = "The statement completed inside the row bound."
        match_count = len(result["rows"])
    base.update(common)
    base.update(result)
    base["match_count"] = match_count
    base["note"] = note
    base["source_sha256_after"] = after_hash
    base["file_unchanged"] = after_hash == file_hash
    base["schema"] = _schema_lines(result.get("columns") or [], declared, units)
    base["queried_at_utc"] = queried_at
    return base


def render_tabular_report(result: dict[str, Any]) -> str:
    lines = [
        "# Read-only tabular query",
        "",
        "## Retrieval record",
        f"- status: {result.get('status')}",
        f"- match_count: {result.get('match_count')}",
        f"- queried_at_utc: {result.get('queried_at_utc')}",
        "- provenance: local_filesystem",
        "- ncbi_eutilities: not_used",
        "- network: not_used",
        f"- error_type: {result.get('error_type')}" if result.get("error_type") else "- error_type: none",
        f"- note: {result.get('note', '')}",
    ]
    if result.get("source_file"):
        lines.append(f"- source_file: {result['source_file']}")
        lines.append(f"- source_sha256: {result.get('source_sha256', '')}")
        lines.append(f"- source_mtime_utc: {result.get('source_mtime_utc', '')}")
        lines.append(f"- file_unchanged: {str(result.get('file_unchanged', '')).lower()}")
    lines.append("")
    lines.append("## Schema and units")
    schema = result.get("schema") or []
    if schema:
        lines.extend(schema)
    else:
        lines.append("- schema: not_available")
        lines.append("- unit: not_provided_by_file")
    lines.append("")
    lines.append("## Rows")
    columns = result.get("columns") or []
    if columns:
        lines.append("| " + " | ".join(str(column) for column in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in result.get("rows") or []:
        lines.append("| " + " | ".join("" if value is None else str(value) for value in row) + " |")
    if not result.get("rows"):
        lines.append("(no rows)")
    return "\n".join(lines)


class Tools:
    class Valves(BaseModel):
        ALLOWED_ROOTS: str = Field(
            default="",
            description="Comma-separated allowlisted directories. Empty refuses every query.",
        )
        MAX_ROWS: int = Field(default=50, description="Maximum rows returned (1 to 500).", ge=1, le=500)
        MAX_BYTES: int = Field(default=2_000_000, description="Maximum file size in bytes.", ge=1, le=50_000_000)
        TIMEOUT_SECONDS: float = Field(default=2.0, description="Query time bound in seconds.", ge=0.05, le=30)

    def __init__(self) -> None:
        self.valves = self.Valves()

    def query_tabular_file(self, relative_path: str, sql: str = "") -> str:
        """
        Run one read-only SELECT against a CSV or SQLite file inside ALLOWED_ROOTS.

        :param relative_path: File path relative to an allowlisted root. Absolute paths and '..' are rejected.
        :param sql: A single SELECT or WITH ... SELECT. Empty means SELECT * FROM data for CSV files.
        :return: Markdown with status, schema, units, and bounded rows.
        """
        result = run_tabular_query(
            allowed_roots=self.valves.ALLOWED_ROOTS,
            relative_path=relative_path,
            sql=sql,
            max_rows=int(self.valves.MAX_ROWS),
            max_bytes=int(self.valves.MAX_BYTES),
            timeout_seconds=float(self.valves.TIMEOUT_SECONDS),
        )
        return render_tabular_report(result)
