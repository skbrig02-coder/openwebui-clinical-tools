"""
Local retrieval manifest, cache, and export.

Backward compatible: new module. Existing tools keep their string reports.
This library records requests for later tools. It does not call a network API,
does not retry rate limits, and does not draw a clinical conclusion.

The owner has not chosen a cache expiry policy. Records are not deleted by age.
A cache hit is labeled served_from_cache and keeps the original retrieved_at_utc.

Do not put patient identifiers in query or parameters. Those keys are rejected.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUSES = frozenset({"ok", "no_match", "error", "partial", "invalid_input"})
_FORBIDDEN_KEYS = frozenset(
    {"patient", "patient_name", "mrn", "ssn", "dob", "phi", "medical_record", "clinical_conclusion", "diagnosis"}
)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_keys(payload: dict[str, Any], where: str) -> None:
    for key in payload:
        if str(key).lower() in _FORBIDDEN_KEYS:
            raise ValueError(f"{where} contains a forbidden field: {key}")


def build_record(
    *,
    status: str,
    retrieved_at_utc: str,
    source: str,
    endpoint: str,
    parameters: dict[str, Any],
    query: str = "",
    match_count: Any = None,
    error_type: str = "",
    source_urls: list[str] | None = None,
    unresolved: bool = False,
    body: str | None = None,
    store_body: bool = False,
    max_body_chars: int = 4000,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if not _TIME_RE.match(retrieved_at_utc or ""):
        raise ValueError("retrieved_at_utc must be YYYY-MM-DDTHH:MM:SSZ")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    _reject_keys(parameters, "parameters")
    record: dict[str, Any] = {
        "status": status,
        "retrieved_at_utc": retrieved_at_utc,
        "source": source,
        "endpoint": endpoint,
        "parameters": parameters,
        "query": query,
        "match_count": match_count,
        "error_type": error_type,
        "source_urls": list(source_urls or []),
        "unresolved": bool(unresolved or status == "error"),
        "served_from_cache": False,
        "clinical_conclusion": None,
    }
    if store_body:
        text = body or ""
        if len(text) > max_body_chars:
            raise ValueError("body exceeds max_body_chars and was not stored")
        record["body"] = text
        record["body_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return record


def request_key(record: dict[str, Any]) -> str:
    payload = {
        "endpoint": record.get("endpoint"),
        "parameters": record.get("parameters"),
        "source": record.get("source"),
        "query": record.get("query"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def cache_put(cache_dir: Path, record: dict[str, Any]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{request_key(record)}.json"
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def cache_get(cache_dir: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    target = cache_dir / f"{request_key(record)}.json"
    if not target.is_file():
        return None
    stored = json.loads(target.read_text(encoding="utf-8"))
    stored["served_from_cache"] = True
    stored["cached_read_at_utc"] = _utc_now_iso()
    return stored


def export_manifest(jsonl_path: Path) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(STATUSES)}
    unresolved = []
    by_doi: dict[str, list[str]] = {}
    records = []
    if jsonl_path.is_file():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            records.append(record)
            status = record.get("status")
            if status in counts:
                counts[status] += 1
            if record.get("unresolved"):
                unresolved.append(
                    {
                        "source": record.get("source"),
                        "endpoint": record.get("endpoint"),
                        "query": record.get("query"),
                        "status": status,
                        "error_type": record.get("error_type"),
                        "retrieved_at_utc": record.get("retrieved_at_utc"),
                    }
                )
            doi = (record.get("parameters") or {}).get("doi")
            if doi:
                by_doi.setdefault(str(doi), []).append(record.get("retrieved_at_utc"))
    duplicates = {doi: times for doi, times in by_doi.items() if len(times) > 1}
    return {
        "exported_at_utc": _utc_now_iso(),
        "counts": counts,
        "record_count": len(records),
        "unresolved": unresolved,
        "duplicate_dois_kept": duplicates,
        "clinical_conclusion": None,
        "note": "Counts are retrieval outcomes. An error is not a no_match. Duplicate DOIs are listed, not deleted.",
    }


def ingest_tool_report(markdown: str, *, source: str, endpoint: str) -> dict[str, Any]:
    """Build a record from a tool report that already prints status and retrieved_at_utc."""
    status_match = re.search(r"^-\s*status:\s*(\S+)\s*$", markdown, re.MULTILINE)
    time_match = re.search(r"^-\s*retrieved_at_utc:\s*(\S+)\s*$", markdown, re.MULTILINE)
    error_match = re.search(r"^-\s*error_type:\s*(\S+)\s*$", markdown, re.MULTILINE)
    if not status_match or not time_match:
        raise ValueError("report is missing status or retrieved_at_utc")
    urls = re.findall(r"https://[^\s)>\"]+", markdown)
    error_type = ""
    if error_match and error_match.group(1) not in {"none", ""}:
        error_type = error_match.group(1)
    return build_record(
        status=status_match.group(1),
        retrieved_at_utc=time_match.group(1),
        source=source,
        endpoint=endpoint,
        parameters={},
        source_urls=urls,
        error_type=error_type,
        match_count=0 if status_match.group(1) == "no_match" else None,
    )
