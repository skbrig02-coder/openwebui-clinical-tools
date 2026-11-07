"""
title: NLM ICD-10-CM Official Code and Diagnosis Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries the official NLM Clinical Tables ICD-10-CM API v3 (clinicaltables.nlm.nih.gov/api/icd10cm/v3/search) by diagnosis description or code prefix. This tool does not call NCBI E-utilities or PubMed. Reports use a UTC retrieval timestamp and keep a successful match count of 0 separate from timeouts, HTTP errors, and malformed responses.
required_open_webui_version: 0.4.0
requirements: httpx, pydantic
version: 1.1.0
license: Copyright (c) 2026 Stuart Brigham, MD (Free to use with attribution to Stuart Brigham, MD)
"""

import asyncio
import datetime
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:  # type: ignore
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def Field(default: Any = None, **kwargs: Any) -> Any:  # type: ignore
        return default

try:
    import httpx  # type: ignore
except ImportError:
    httpx = None  # type: ignore


def _clean(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).replace("\u2014", " - ").replace("\u2013", "-").split())


class Upstream:
    def __init__(self, status_code: int = 0, payload: Any = None, error_type: str = "", detail: str = ""):
        self.status_code = int(status_code or 0)
        self.payload = payload
        self.error_type = error_type or ""
        self.detail = detail or ""


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classify_body(status_code: int, raw: str) -> Upstream:
    payload = None
    malformed = False
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            malformed = True
    else:
        malformed = True
    if status_code == 200:
        if malformed or payload is None:
            return Upstream(status_code, None, "malformed_response", "Response body was not valid JSON.")
        return Upstream(status_code, payload, "", "")
    return Upstream(status_code, None if malformed else payload, "http_error", f"HTTP {status_code}")


def _upstream_from_exception(exc: BaseException) -> Upstream:
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timeout" in type(exc).__name__.lower():
        return Upstream(0, None, "timeout", "Request timed out.")
    if isinstance(exc, json.JSONDecodeError) or "json" in type(exc).__name__.lower():
        return Upstream(0, None, "malformed_response", "Response body was not valid JSON.")
    return Upstream(0, None, "network_error", "Network request failed.")


def _sync_get_json(url: str, params: Dict[str, str], timeout: float) -> Upstream:
    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": "OpenWebUI-Clinical-Tools/1.1", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return _classify_body(getattr(resp, "status", 200) or 200, raw)
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return _classify_body(exc.code, raw)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return Upstream(0, None, "timeout", "Request timed out.")
        return Upstream(0, None, "network_error", "Network request failed.")
    except (TimeoutError, socket.timeout):
        return Upstream(0, None, "timeout", "Request timed out.")
    except Exception as exc:
        return _upstream_from_exception(exc)


async def _async_get_json(url: str, params: Dict[str, str], timeout: float) -> Upstream:
    if httpx is not None:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={"User-Agent": "OpenWebUI-Clinical-Tools/1.1", "Accept": "application/json"},
            ) as client:
                resp = await client.get(url, params=params)
                return _classify_body(resp.status_code, resp.text)
        except Exception as exc:
            return _upstream_from_exception(exc)
    return await asyncio.to_thread(_sync_get_json, url, params, timeout)


def _invalid_lookup(message: str) -> str:
    return "\n".join(
        [
            message,
            "- status: invalid_input",
            "- provenance: live_http",
            f"- checked_at_utc: {_utc_now_iso()}",
            "- ncbi_eutilities: not_used",
            "- note: No upstream request was sent.",
        ]
    )


_UPSTREAM_FAILURE_NOTE = "Upstream request failed. This result is not a finding of zero matching records."


class Tools:
    class Valves(BaseModel):
        TIMEOUT_SECONDS: int = Field(
            default=15,
            description="HTTP request timeout in seconds for the NLM Clinical Tables API.",
            ge=5,
            le=60,
        )
        DEFAULT_MAX_RESULTS: int = Field(
            default=20,
            description="Default maximum number of ICD-10-CM codes returned (1 to 100).",
            ge=1,
            le=100,
        )

    def __init__(self):
        self.valves = self.Valves()
        self.base_url = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"

    async def search_icd10cm(
        self,
        query: str,
        search_mode: str = "both",
        max_results: int = 20,
    ) -> str:
        """
        Search official ICD-10-CM diagnosis codes and descriptions via the NLM Clinical Tables REST API v3
        (`https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search`).
        Unlike flawed community wrappers that restrict `sf=name` (breaking code-prefix searches),
        this tool searches both code and description fields (`sf=code,name`) or lets you target `code` or `name` specifically.

        :param query: Clinical diagnosis term (e.g., 'systolic heart failure', 'type 2 diabetes with chronic kidney disease') or ICD-10-CM code/prefix (e.g., 'I50.2', 'E11.22').
        :param search_mode: Which fields to search: 'both' (`sf=code,name`), 'code' (`sf=code`), or 'name' (`sf=name`).
        :param max_results: Maximum number of matching ICD-10-CM codes to return (1 to 50).
        :return: Structured Markdown table of matching ICD-10-CM codes and official descriptions.
        """
        q = (query or "").strip()
        if not q:
            return _invalid_lookup("Error: query is required.")

        limit = max(1, min(int(max_results or self.valves.DEFAULT_MAX_RESULTS), 50))
        mode = (search_mode or "both").strip().lower()
        if mode == "code":
            sf = "code"
        elif mode == "name":
            sf = "name"
        else:
            sf = "code,name"

        params = {
            "terms": q,
            "sf": sf,
            "df": "code,name",
            "maxList": str(limit),
        }
        full_url = f"{self.base_url}?{urllib.parse.urlencode(params)}"

        fetched = await _async_get_json(
            self.base_url, params, float(self.valves.TIMEOUT_SECONDS)
        )
        if fetched.error_type or fetched.status_code != 200 or not isinstance(fetched.payload, list) or len(fetched.payload) < 4:
            error_type = fetched.error_type or ("malformed_response" if fetched.status_code == 200 else "http_error")
            return "\n".join(
                [
                    f"# NLM ICD-10-CM Official Code Lookup: {q}",
                    "",
                    "## Retrieval record",
                    "- status: error",
                    "- match_count: unavailable",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- source_content_date: not_provided_by_response",
                    f"- icd10cm: status=error error_type: {error_type} http_status={fetched.status_code}",
                    f"- note: {_UPSTREAM_FAILURE_NOTE}",
                    f"- endpoint: {self.base_url}",
                ]
            )

        data = fetched.payload
        total_count = data[0]
        rows = data[3] or []
        if not isinstance(rows, list):
            return "\n".join(
                [
                    f"# NLM ICD-10-CM Official Code Lookup: {q}",
                    "",
                    "## Retrieval record",
                    "- status: error",
                    "- match_count: unavailable",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- source_content_date: not_provided_by_response",
                    "- icd10cm: status=error error_type: malformed_response http_status=200",
                    f"- note: {_UPSTREAM_FAILURE_NOTE}",
                ]
            )
        if not rows:
            shown_total = total_count if isinstance(total_count, int) else 0
            overall = "partial" if shown_total else "no_match"
            note = (
                "The API reported a positive total with no display rows. This incomplete response is not a confirmed match count of 0."
                if overall == "partial"
                else "NLM Clinical Tables responded successfully and the match count is 0."
            )
            return "\n".join(
                [
                    f"# NLM ICD-10-CM Official Code Lookup: {q}",
                    "",
                    "## Retrieval record",
                    f"- status: {overall}",
                    "- match_count: 0" if overall == "no_match" else "- match_count: unavailable",
                    f"- total_count: {shown_total}",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- source_content_date: not_provided_by_response",
                    f"- note: {note}",
                    f"- endpoint: {full_url}",
                ]
            )

        lines = [
            f"# NLM ICD-10-CM Official Code Lookup: {q}",
            "",
            "## Retrieval record",
            "- status: ok",
            f"- match_count: {len(rows)}",
            f"- total_count: {total_count}",
            f"- retrieved_at_utc: {_utc_now_iso()}",
            "- provenance: live_http",
            "- ncbi_eutilities: not_used",
            "- source_content_date: not_provided_by_response",
            "- note: This API response does not include a source release date. retrieved_at_utc is when this tool called NLM Clinical Tables.",
            f"- **Total Matches in NLM ICD-10-CM Table:** {total_count} (showing top {len(rows)})",
            f"- **Search Fields (`sf`):** `{sf}`",
            f"- **NLM Clinical Tables API URL:** {full_url}",
            "",
            "| # | ICD-10-CM Code | Official NLM / CMS Description | Specificity Note |",
            "|---|---|---|---|",
        ]

        for idx, row in enumerate(rows, start=1):
            if not isinstance(row, list) or len(row) < 2:
                continue
            code = _clean(row[0])
            desc = _clean(row[1])
            spec_note = (
                "Category header (3-char); check sub-codes"
                if "." not in code and len(code) <= 3
                else "Subcategory / specific code"
            )
            lines.append(f"| {idx} | **`{code}`** | {desc} | {spec_note} |")

        lines.append("")
        lines.append(
            "*Source: U.S. National Library of Medicine (NLM) Clinical Tables ICD-10-CM REST API v3.*"
        )
        return "\n".join(lines)
