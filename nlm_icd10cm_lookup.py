"""
title: NLM ICD-10-CM Official Code and Diagnosis Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries the official NLM Clinical Tables ICD-10-CM API v3 (clinicaltables.nlm.nih.gov/api/icd10cm/v3/search) by diagnosis description or code prefix.
required_open_webui_version: 0.4.0
requirements: httpx, pydantic
version: 1.0.0
license: MIT
"""

import asyncio
import json
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


def _sync_get_json(url: str, params: Dict[str, str], timeout: float) -> Tuple[int, Optional[ Any]]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": "OpenWebUI-Clinical-Tools/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


async def _async_get_json(url: str, params: Dict[str, str], timeout: float) -> Tuple[int, Optional[Any]]:
    if httpx is not None:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={"User-Agent": "OpenWebUI-Clinical-Tools/1.0", "Accept": "application/json"},
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    return 200, resp.json()
                return resp.status_code, None
        except Exception:
            return 0, None
    return await asyncio.to_thread(_sync_get_json, url, params, timeout)


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
            return "Error: query is required."

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

        status_code, data = await _async_get_json(
            self.base_url, params, float(self.valves.TIMEOUT_SECONDS)
        )
        if status_code != 200 or not isinstance(data, list) or len(data) < 4:
            return f"Error querying NLM Clinical Tables ICD-10-CM API (HTTP {status_code}): {full_url}"

        total_count = data[0]
        rows = data[3] or []
        if not rows:
            return f"No ICD-10-CM codes found for '{q}' (search_mode='{sf}'). API URL: {full_url}"

        lines = [
            f"# NLM ICD-10-CM Official Code Lookup: {q}",
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
