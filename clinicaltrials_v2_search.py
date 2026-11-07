"""
title: ClinicalTrials.gov REST API v2 Search and NCT Study Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries the official ClinicalTrials.gov REST API v2 (clinicaltrials.gov/api/v2/studies) by condition, intervention, phase, recruitment status, results availability, or exact NCT ID. This tool does not call NCBI E-utilities or PubMed. Reports use a UTC retrieval timestamp and keep a successful match count of 0 separate from timeouts, HTTP errors, and malformed responses.
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


def _clean(text: str, max_len: int = 600) -> str:
    if not text:
        return ""
    s = " ".join(str(text).replace("\u2014", " - ").replace("\u2013", "-").split())
    if len(s) > max_len:
        return s[:max_len] + "... [truncated]"
    return s


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
            description="HTTP request timeout in seconds for ClinicalTrials.gov REST API v2.",
            ge=5,
            le=60,
        )
        DEFAULT_PAGE_SIZE: int = Field(
            default=10,
            description="Default number of studies returned per search (1 to 50).",
            ge=1,
            le=50,
        )

    def __init__(self):
        self.valves = self.Valves()
        self.base_url = "https://clinicaltrials.gov/api/v2/studies"

    async def search_clinical_trials(
        self,
        condition: str = "",
        intervention: str = "",
        query_term: str = "",
        status: str = "ALL",
        phase: str = "",
        has_results_only: bool = False,
        max_results: int = 10,
    ) -> str:
        """
        Search ClinicalTrials.gov via the official REST API v2 (`https://clinicaltrials.gov/api/v2/studies`).
        Supports filtering by condition, drug/intervention, free-text term, overall recruitment status,
        study phase, and whether trial results have been posted.

        :param condition: Disease or condition (e.g., 'heart failure', 'chronic kidney disease', 'type 2 diabetes').
        :param intervention: Drug, device, or intervention (e.g., 'semaglutide', 'finerenone', 'tirzepatide').
        :param query_term: Additional search terms, trial acronym, or outcome measure (e.g., 'FLOW', 'SUMMIT').
        :param status: Recruitment status filter: 'ALL', 'RECRUITING', 'ACTIVE_NOT_RECRUITING', 'COMPLETED', 'NOT_YET_RECRUITING', or pipe/comma-separated v2 statuses.
        :param phase: Optional study phase filter: 'PHASE1', 'PHASE2', 'PHASE3', 'PHASE4' (or leave empty for all phases).
        :param has_results_only: If True, restricts to studies with posted results (`aggFilters=results:with`).
        :param max_results: Maximum number of trials to return (1 to 25).
        :return: Structured Markdown list of matching clinical trials with NCT links, phases, sample sizes, and endpoints.
        """
        condition = condition or ""
        intervention = intervention or ""
        query_term = query_term or ""
        status = status or "ALL"
        phase = phase or ""
        if not any([condition.strip(), intervention.strip(), query_term.strip()]):
            return _invalid_lookup("Error: Provide at least one of condition, intervention, or query_term.")

        limit = max(1, min(int(max_results or self.valves.DEFAULT_PAGE_SIZE), 25))
        params = {
            "pageSize": str(limit),
            "countTotal": "true",
            "format": "json",
        }
        if condition.strip():
            params["query.cond"] = condition.strip()
        if intervention.strip():
            params["query.intr"] = intervention.strip()

        term_parts = []
        if query_term.strip():
            term_parts.append(query_term.strip())
        if phase.strip():
            p_norm = phase.strip().upper().replace(" ", "")
            if p_norm in ("PHASE1", "PHASE2", "PHASE3", "PHASE4", "EARLY_PHASE1"):
                term_parts.append(f"AREA[Phase]{p_norm}")
        if term_parts:
            params["query.term"] = " AND ".join(term_parts)

        st_norm = (status or "ALL").strip().upper()
        if st_norm and st_norm != "ALL":
            params["filter.overallStatus"] = st_norm.replace("|", ",")

        if has_results_only:
            params["aggFilters"] = "results:with"

        full_query_url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        fetched = await _async_get_json(
            self.base_url, params, float(self.valves.TIMEOUT_SECONDS)
        )
        if (
            fetched.error_type
            or fetched.status_code != 200
            or not isinstance(fetched.payload, dict)
            or "studies" not in fetched.payload
            or not isinstance(fetched.payload.get("studies"), list)
        ):
            error_type = fetched.error_type or ("malformed_response" if fetched.status_code == 200 else "http_error")
            return "\n".join(
                [
                    "# ClinicalTrials.gov API v2 Search Results",
                    "",
                    "## Retrieval record",
                    "- status: error",
                    "- match_count: unavailable",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    f"- clinicaltrials: status=error error_type: {error_type} http_status={fetched.status_code}",
                    f"- note: {_UPSTREAM_FAILURE_NOTE}",
                    f"- endpoint: {self.base_url}",
                ]
            )

        data = fetched.payload
        studies = data.get("studies") or []
        total_count = data.get("totalCount", len(studies))
        if not studies:
            return "\n".join(
                [
                    "# ClinicalTrials.gov API v2 Search Results",
                    "",
                    "## Retrieval record",
                    "- status: no_match",
                    "- match_count: 0",
                    f"- total_count: {total_count}",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- note: ClinicalTrials.gov responded successfully and the match count is 0.",
                    f"- endpoint: {full_query_url}",
                ]
            )

        lines = [
            "# ClinicalTrials.gov API v2 Search Results",
            "",
            "## Retrieval record",
            "- status: ok",
            f"- match_count: {len(studies)}",
            f"- total_count: {total_count}",
            f"- retrieved_at_utc: {_utc_now_iso()}",
            "- provenance: live_http",
            "- ncbi_eutilities: not_used",
            "- note: retrieved_at_utc is when this tool called the API. source_content_date is the study record date when the API provided one.",
            f"- **Total Matching Studies:** {total_count} (showing top {len(studies)})",
            f"- **API v2 Endpoint Called:** {full_query_url}",
            "",
        ]

        for idx, st in enumerate(studies, start=1):
            if not isinstance(st, dict):
                continue
            proto = st.get("protocolSection") or {}
            ident = proto.get("identificationModule") or {}
            status_mod = proto.get("statusModule") or {}
            design = proto.get("designModule") or {}
            cond_mod = proto.get("conditionsModule") or {}
            arms_mod = proto.get("armsInterventionsModule") or {}
            outcomes_mod = proto.get("outcomesModule") or {}
            sponsor_mod = proto.get("sponsorCollaboratorsModule") or {}

            nct_id = ident.get("nctId", "N/A")
            acronym = ident.get("acronym", "")
            title = _clean(ident.get("briefTitle", "Untitled"), 300)
            overall_status = status_mod.get("overallStatus", "N/A")
            phases = ", ".join(design.get("phases") or []) or "Not Applicable / Observational"
            enrollment = (design.get("enrollmentInfo") or {}).get("count", "N/A")
            study_type = design.get("studyType", "N/A")
            conditions = ", ".join((cond_mod.get("conditions") or [])[:5]) or "N/A"
            interventions = (
                ", ".join(
                    f"{iv.get('type', '')}: {iv.get('name', '')}".strip(": ")
                    for iv in (arms_mod.get("interventions") or [])[:5]
                )
                or "N/A"
            )
            prim_outcomes = outcomes_mod.get("primaryOutcomes") or []
            prim_measure = (
                _clean(prim_outcomes[0].get("measure", ""), 250) if prim_outcomes else "N/A"
            )
            sponsor = (sponsor_mod.get("leadSponsor") or {}).get("name", "N/A")
            has_results = st.get("hasResults", False)
            study_url = f"https://clinicaltrials.gov/study/{nct_id}"

            header_acronym = f" ({acronym})" if acronym else ""
            lines.append(f"## {idx}. {nct_id}{header_acronym}: {title}")
            lines.append(
                f"- **Status:** `{overall_status}` | **Phase:** {phases} | **Study Type:** {study_type} | **Enrollment:** {enrollment} | **Posted Results:** {'Yes' if has_results else 'No'}"
            )
            lines.append(f"- **Sponsor:** {_clean(sponsor, 150)}")
            lines.append(f"- **Conditions:** {_clean(conditions, 250)}")
            lines.append(f"- **Interventions:** {_clean(interventions, 250)}")
            source_date = (status_mod.get("lastUpdatePostDateStruct") or {}).get("date") or "not_provided_by_response"
            lines.append(f"- **Primary Outcome:** {prim_measure}")
            lines.append(f"- source_content_date: {source_date}")
            lines.append(f"- **Study Record Link:** {study_url}")
            lines.append("")

        return "\n".join(lines)

    async def get_trial_by_nct_id(
        self,
        nct_id: str,
    ) -> str:
        """
        Fetch detailed protocol, eligibility, primary/secondary outcomes, and posted results
        for a specific ClinicalTrials.gov study by NCT ID (for example, 'NCT03819153' for FLOW
        or 'NCT04847557' for SUMMIT).

        :param nct_id: The NCT identifier (e.g., 'NCT03819153').
        :return: Detailed Markdown report of the study protocol and any posted primary outcome results.
        """
        clean_id = (nct_id or "").strip().upper()
        if not clean_id.startswith("NCT"):
            return _invalid_lookup("Error: nct_id must start with 'NCT' (for example, 'NCT03819153').")

        url = f"{self.base_url}/{clean_id}"
        fetched = await _async_get_json(url, {}, float(self.valves.TIMEOUT_SECONDS))
        if fetched.status_code == 404:
            return "\n".join(
                [
                    f"# ClinicalTrials.gov Study Record: {clean_id}",
                    "",
                    "## Retrieval record",
                    "- status: no_match",
                    "- match_count: 0",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- clinicaltrials: status=no_match http_status=404",
                    "- note: ClinicalTrials.gov responded and has no study for this NCT ID. The match count is 0.",
                ]
            )
        if fetched.error_type or fetched.status_code != 200 or not isinstance(fetched.payload, dict):
            error_type = fetched.error_type or ("malformed_response" if fetched.status_code == 200 else "http_error")
            return "\n".join(
                [
                    f"# ClinicalTrials.gov Study Record: {clean_id}",
                    "",
                    "## Retrieval record",
                    "- status: error",
                    "- match_count: unavailable",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    f"- clinicaltrials: status=error error_type: {error_type} http_status={fetched.status_code}",
                    f"- note: {_UPSTREAM_FAILURE_NOTE}",
                ]
            )
        st = fetched.payload
        if "protocolSection" not in st:
            return "\n".join(
                [
                    f"# ClinicalTrials.gov Study Record: {clean_id}",
                    "",
                    "## Retrieval record",
                    "- status: error",
                    "- match_count: unavailable",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- clinicaltrials: status=error error_type: malformed_response http_status=200",
                    f"- note: {_UPSTREAM_FAILURE_NOTE}",
                ]
            )

        proto = st.get("protocolSection") or {}
        ident = proto.get("identificationModule") or {}
        status_mod = proto.get("statusModule") or {}
        design = proto.get("designModule") or {}
        desc_mod = proto.get("descriptionModule") or {}
        elig_mod = proto.get("eligibilityModule") or {}
        outcomes_mod = proto.get("outcomesModule") or {}
        results_sec = st.get("resultsSection") or {}

        title = _clean(ident.get("officialTitle") or ident.get("briefTitle", ""), 400)
        acronym = ident.get("acronym", "")
        overall_status = status_mod.get("overallStatus", "N/A")
        start_date = (status_mod.get("startDateStruct") or {}).get("date", "N/A")
        comp_date = (status_mod.get("completionDateStruct") or {}).get("date", "N/A")
        results_date = (status_mod.get("resultsFirstPostDateStruct") or {}).get("date", "None")
        enrollment = (design.get("enrollmentInfo") or {}).get("count", "N/A")
        phases = ", ".join(design.get("phases") or []) or "N/A"

        source_date = (status_mod.get("lastUpdatePostDateStruct") or {}).get("date") or results_date
        lines = [
            f"# ClinicalTrials.gov Study Record: {clean_id}" + (f" ({acronym})" if acronym else ""),
            "",
            "## Retrieval record",
            "- status: ok",
            "- match_count: 1",
            f"- retrieved_at_utc: {_utc_now_iso()}",
            "- provenance: live_http",
            "- ncbi_eutilities: not_used",
            f"- source_content_date: {source_date}",
            "- note: retrieved_at_utc is when this tool called the API. Start, completion, and results dates are source dates.",
            f"- **Title:** {title}",
            f"- **Overall Status:** `{overall_status}` | **Phase:** {phases} | **Enrollment:** {enrollment}",
            f"- **Start Date:** {start_date} | **Completion Date:** {comp_date} | **Results First Posted:** {results_date}",
            f"- **Canonical URL:** https://clinicaltrials.gov/study/{clean_id}",
            f"- **REST API v2 URL:** {url}",
            "",
            "## 1. Brief Summary",
            _clean(desc_mod.get("briefSummary", "None provided."), 1500),
            "",
            "## 2. Eligibility Criteria",
            f"- **Sex:** {elig_mod.get('sex', 'ALL')} | **Min Age:** {elig_mod.get('minimumAge', 'N/A')} | **Max Age:** {elig_mod.get('maximumAge', 'N/A')}",
            _clean(elig_mod.get("eligibilityCriteria", "None listed."), 1500),
            "",
            "## 3. Primary Outcomes (Protocol)",
        ]

        for idx, po in enumerate((outcomes_mod.get("primaryOutcomes") or [])[:5], start=1):
            lines.append(
                f"- **{idx}. {_clean(po.get('measure', ''), 250)}** (Time Frame: {_clean(po.get('timeFrame', 'N/A'), 150)})"
            )

        if results_sec:
            lines.append("")
            lines.append("## 4. Posted Results Summary (`resultsSection`)")
            out_meas = (results_sec.get("outcomeMeasuresModule") or {}).get("outcomeMeasures") or []
            for idx, om in enumerate(out_meas[:3], start=1):
                lines.append(
                    f"### Outcome {idx}: {_clean(om.get('title', ''), 250)} ({om.get('type', '')})"
                )
                for analysis in (om.get("analyses") or [])[:3]:
                    stat_method = _clean(analysis.get("statisticalMethod", ""), 120)
                    p_val = analysis.get("pValue", "N/A")
                    param_type = _clean(analysis.get("paramType", ""), 120)
                    param_val = analysis.get("paramValue", "N/A")
                    ci_low = analysis.get("ciLowerLimit", "")
                    ci_high = analysis.get("ciUpperLimit", "")
                    ci_str = f" (95% CI {ci_low} to {ci_high})" if ci_low and ci_high else ""
                    lines.append(
                        f"- **Analysis ({stat_method}):** {param_type} = {param_val}{ci_str}, p = {p_val}"
                    )

        return "\n".join(lines)
