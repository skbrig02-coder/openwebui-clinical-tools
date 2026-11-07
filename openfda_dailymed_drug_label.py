"""
title: openFDA and DailyMed Drug Label Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries official FDA drug labels via openFDA (api.fda.gov/drug/label.json) and NLM DailyMed SPLs (dailymed.nlm.nih.gov/dailymed/services/v2/). This tool does not call NCBI E-utilities or PubMed. Each report states retrieval time in UTC and separates a successful match count of 0 from timeouts, HTTP errors, and malformed responses.
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
from typing import Any, Dict, Optional

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


def _clean_text(text: str, max_chars: int = 1800) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.replace("\u2014", " - ").replace("\u2013", "-").split())
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + "... [truncated]"
    return cleaned


def _extract_section(record: dict, key: str, max_chars: int = 1800) -> str:
    val = record.get(key)
    if not val:
        return "Not listed in this label section."
    if isinstance(val, list):
        joined = " ".join(str(x) for x in val if x)
    else:
        joined = str(val)
    return _clean_text(joined, max_chars=max_chars)


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
        OPENFDA_API_KEY: str = Field(
            default="",
            description="Optional openFDA API key for higher rate limits (leave blank for free public tier).",
        )
        TIMEOUT_SECONDS: int = Field(
            default=15,
            description="HTTP request timeout in seconds.",
            ge=5,
            le=60,
        )
        MAX_SECTION_CHARS: int = Field(
            default=1800,
            description="Maximum characters returned per label section to preserve context window.",
            ge=400,
            le=8000,
        )

    def __init__(self):
        self.valves = self.Valves()

    async def lookup_drug_label(
        self,
        drug_name: str,
        prefer_monotherapy: bool = True,
        include_dailymed_spls: bool = True,
    ) -> str:
        """
        Look up official FDA drug labeling from openFDA and DailyMed SPL metadata.
        Returns boxed warnings, indications, dosage and administration, contraindications,
        warnings and precautions, adverse reactions, drug interactions, and DailyMed SetID links.

        :param drug_name: Generic or brand drug name (for example, 'semaglutide', 'apixaban', 'metformin').
        :param prefer_monotherapy: If True, prefers single-ingredient labels over combination products.
        :param include_dailymed_spls: If True, also fetches NLM DailyMed SPL SetIDs and direct label links.
        :return: Structured Markdown summary of the official FDA drug label and DailyMed records.
        """
        query_drug = (drug_name or "").strip()
        if not query_drug:
            return _invalid_lookup("Error: drug_name is required.")

        needle = query_drug.upper()
        chosen_record: Optional[dict] = None
        query_url_used = ""
        timeout_s = float(self.valves.TIMEOUT_SECONDS)
        fda_error = ""
        fda_http = 0
        saw_fda_nomatch = False
        saw_fda_error = False

        for field in ("openfda.generic_name", "openfda.brand_name", "openfda.substance_name"):
            params = {
                "search": f'{field}:"{query_drug}"',
                "limit": "5" if prefer_monotherapy else "1",
            }
            if self.valves.OPENFDA_API_KEY.strip():
                params["api_key"] = self.valves.OPENFDA_API_KEY.strip()

            url = "https://api.fda.gov/drug/label.json"
            fetched = await _async_get_json(url, params, timeout_s)
            # openFDA documents HTTP 404 plus NOT_FOUND as an empty search, including for this endpoint.
            if fetched.status_code == 404:
                saw_fda_nomatch = True
                fda_http = 404
                continue
            if fetched.error_type or not isinstance(fetched.payload, dict):
                saw_fda_error = True
                if not fda_error:
                    fda_error = fetched.error_type or "malformed_response"
                    fda_http = fetched.status_code
                continue
            results = fetched.payload.get("results")
            if not isinstance(results, list):
                saw_fda_error = True
                if not fda_error:
                    fda_error = "malformed_response"
                    fda_http = fetched.status_code
                continue
            if not results:
                saw_fda_nomatch = True
                fda_http = fetched.status_code
                continue

            safe_q = urllib.parse.urlencode({"search": params["search"], "limit": params["limit"]})
            query_url_used = f"{url}?{safe_q}"
            if prefer_monotherapy:
                for rec in results:
                    if not isinstance(rec, dict):
                        continue
                    openfda_meta = rec.get("openfda") or {}
                    subs = openfda_meta.get("substance_name") or []
                    if len(subs) == 1 and needle in str(subs[0]).upper():
                        chosen_record = rec
                        break
            if chosen_record is None and isinstance(results[0], dict):
                chosen_record = results[0]
            break

        if chosen_record is not None:
            fda_state, fda_count = "ok", 1
        elif saw_fda_error:
            fda_state, fda_count = "error", None
        else:
            fda_state, fda_count = "no_match", 0

        dailymed_items = []
        dailymed_url_used = ""
        dm_state = "not_requested"
        dm_error = ""
        dm_http = 0
        dm_count = None
        if include_dailymed_spls:
            dm_url = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
            dm_params = {"drug_name": query_drug, "pagesize": "5"}
            dailymed_url_used = f"{dm_url}?{urllib.parse.urlencode(dm_params)}"
            dm = await _async_get_json(dm_url, dm_params, timeout_s)
            dm_http = dm.status_code
            data_rows = None
            if (
                dm.error_type == ""
                and dm.status_code == 200
                and isinstance(dm.payload, dict)
                and isinstance(dm.payload.get("data"), list)
            ):
                data_rows = dm.payload.get("data") or []
            if data_rows is None:
                dm_state = "error"
                dm_error = dm.error_type or ("malformed_response" if dm.status_code == 200 else "http_error")
            else:
                for item in data_rows[:5]:
                    if not isinstance(item, dict):
                        continue
                    setid = item.get("setid", "")
                    dailymed_items.append(
                        {
                            "title": _clean_text(item.get("title", ""), 300),
                            "setid": setid,
                            "published_date": item.get("published_date", "") or "",
                            "spl_version": item.get("spl_version", ""),
                            "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}" if setid else "",
                        }
                    )
                dm_count = len(dailymed_items)
                dm_state = "ok" if dailymed_items else "no_match"

        states = [fda_state] + ([dm_state] if include_dailymed_spls else [])
        record_count = (1 if chosen_record is not None else 0) + len(dailymed_items)
        if any(state == "error" for state in states):
            overall = "partial" if record_count else "error"
        elif record_count:
            overall = "ok"
        else:
            overall = "no_match"

        if overall == "no_match":
            note = "Each requested source responded successfully and the match count is 0."
            match_display = "0"
        elif overall in ("error", "partial"):
            note = _UPSTREAM_FAILURE_NOTE
            match_display = "unavailable" if overall == "error" else str(record_count)
        else:
            note = "Requested sources responded successfully. Retrieval time is when this tool called the source, separate from dates inside the label."
            match_display = str(record_count)

        lines = [
            f"# Official FDA / DailyMed Drug Label Report: {query_drug}",
            "",
            "## Retrieval record",
            f"- status: {overall}",
            f"- match_count: {match_display}",
            f"- retrieved_at_utc: {_utc_now_iso()}",
            "- provenance: live_http",
            "- ncbi_eutilities: not_used",
            "- source: openFDA drug/label and NLM DailyMed SPL. Static PMID citations are not used here.",
        ]
        fda_bits = f"- openfda: status={fda_state}"
        if fda_count is not None:
            fda_bits += f" match_count={fda_count}"
        if fda_state == "no_match":
            fda_bits += f" http_status={fda_http or 404}"
        if fda_state == "error":
            fda_bits += f" error_type: {fda_error} http_status={fda_http}"
        lines.append(fda_bits)
        if include_dailymed_spls:
            dm_bits = f"- dailymed: status={dm_state}"
            if dm_count is not None:
                dm_bits += f" match_count={dm_count}"
            if dm_state == "error":
                dm_bits += f" error_type: {dm_error} http_status={dm_http}"
            lines.append(dm_bits)
            for item in dailymed_items:
                if item["published_date"]:
                    lines.append(f"- published_date: {item['published_date']}")
        if chosen_record is not None:
            eff_time = chosen_record.get("effective_time") or ""
            lines.append(f"- source_content_date: {eff_time or 'not_provided_by_response'}")
        lines.append(f"- note: {note}")
        lines.append("")

        if overall in ("error", "no_match"):
            if overall == "no_match":
                lines.append(
                    f"Match count is 0 for '{query_drug}'. The queried sources responded and returned no label records."
                )
            return "\n".join(lines)

        if chosen_record is not None:
            openfda_meta = chosen_record.get("openfda") or {}
            brands = ", ".join((openfda_meta.get("brand_name") or [])[:5]) or "N/A"
            generics = ", ".join((openfda_meta.get("generic_name") or [])[:5]) or "N/A"
            substances = ", ".join((openfda_meta.get("substance_name") or [])[:5]) or "N/A"
            manufacturers = ", ".join((openfda_meta.get("manufacturer_name") or [])[:3]) or "N/A"
            routes = ", ".join((openfda_meta.get("route") or [])[:5]) or "N/A"
            rxcuis = ", ".join((openfda_meta.get("rxcui") or [])[:8]) or "N/A"
            set_ids = openfda_meta.get("spl_set_id") or ["N/A"]
            set_id = chosen_record.get("set_id") or (set_ids[0] if set_ids else "N/A")
            eff_time = chosen_record.get("effective_time") or "N/A"

            lines.append("## 1. Product Identification (openFDA)")
            lines.append(f"- **Generic Name(s):** {generics}")
            lines.append(f"- **Brand Name(s):** {brands}")
            lines.append(f"- **Active Substance(s):** {substances}")
            lines.append(f"- **Route(s):** {routes}")
            lines.append(f"- **Manufacturer(s):** {manufacturers}")
            lines.append(f"- **SPL Set ID:** `{set_id}` (Effective: `{eff_time}`)")
            lines.append(f"- **Linked RxCUIs:** {rxcuis}")
            if set_id and set_id != "N/A":
                lines.append(
                    f"- **Direct DailyMed Label View:** https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}"
                )
            lines.append(f"- **openFDA Query URL:** {query_url_used}")
            lines.append("")

            max_c = int(self.valves.MAX_SECTION_CHARS)
            boxed = chosen_record.get("boxed_warning")
            lines.append("## 2. Boxed Warning")
            if boxed:
                lines.append(
                    f"**BOXED WARNING PRESENT:** {_extract_section(chosen_record, 'boxed_warning', max_c)}"
                )
            else:
                lines.append("No boxed warning section present in this SPL record.")
            lines.append("")

            lines.append("## 3. Indications and Usage")
            lines.append(_extract_section(chosen_record, "indications_and_usage", max_c))
            lines.append("")

            lines.append("## 4. Dosage and Administration")
            lines.append(_extract_section(chosen_record, "dosage_and_administration", max_c))
            lines.append("")

            lines.append("## 5. Contraindications")
            lines.append(_extract_section(chosen_record, "contraindications", max_c))
            lines.append("")

            lines.append("## 6. Warnings and Precautions")
            warn_key = (
                "warnings_and_cautions"
                if chosen_record.get("warnings_and_cautions")
                else "warnings_and_precautions"
                if chosen_record.get("warnings_and_precautions")
                else "warnings"
            )
            lines.append(_extract_section(chosen_record, warn_key, max_c))
            lines.append("")

            lines.append("## 7. Adverse Reactions")
            lines.append(_extract_section(chosen_record, "adverse_reactions", max_c))
            lines.append("")

            lines.append("## 8. Drug Interactions (Label Section)")
            lines.append(_extract_section(chosen_record, "drug_interactions", max_c))
            lines.append("")

            lines.append("## 9. Use in Specific Populations / Renal or Hepatic Dosing")
            lines.append(_extract_section(chosen_record, "use_in_specific_populations", max_c))
            lines.append("")

        if dailymed_items:
            lines.append("## 10. NLM DailyMed SPL Registry Matches")
            lines.append(f"- **DailyMed REST Endpoint:** {dailymed_url_used}")
            for idx, item in enumerate(dailymed_items, start=1):
                lines.append(
                    f"  {idx}. **{item['title']}** | SetID: `{item['setid']}` | Published: {item['published_date']} | Version: {item['spl_version']} | Link: {item['url']}"
                )
            lines.append("")

        lines.append(
            "*Source: U.S. Food and Drug Administration (openFDA) and NLM DailyMed Structured Product Labeling (SPL). For clinical reference only. PMID strings are not retrieved by this tool.*"
        )
        return "\n".join(lines)
