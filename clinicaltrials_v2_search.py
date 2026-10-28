"""
title: ClinicalTrials.gov REST API v2 Search and NCT Study Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries the official ClinicalTrials.gov REST API v2 (clinicaltrials.gov/api/v2/studies) by condition, intervention, phase, recruitment status, results availability, or exact NCT ID.
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


def _clean(text: str, max_len: int = 600) -> str:
    if not text:
        return ""
    s = " ".join(str(text).replace("\u2014", " - ").replace("\u2013", "-").split())
    if len(s) > max_len:
        return s[:max_len] + "... [truncated]"
    return s


def _sync_get_json(url: str, params: Dict[str, str], timeout: float) -> Tuple[int, Optional[dict]]:
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


async def _async_get_json(url: str, params: Dict[str, str], timeout: float) -> Tuple[int, Optional[dict]]:
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
        if not any([condition.strip(), intervention.strip(), query_term.strip()]):
            return "Error: Provide at least one of condition, intervention, or query_term."

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
        status_code, data = await _async_get_json(
            self.base_url, params, float(self.valves.TIMEOUT_SECONDS)
        )
        if status_code != 200 or not data:
            return f"Error querying ClinicalTrials.gov API v2 (HTTP {status_code}): {full_query_url}"

        studies = data.get("studies") or []
        total_count = data.get("totalCount", len(studies))
        if not studies:
            return f"No trials found matching parameters. API URL: {full_query_url}"

        lines = [
            "# ClinicalTrials.gov API v2 Search Results",
            f"- **Total Matching Studies:** {total_count} (showing top {len(studies)})",
            f"- **API v2 Endpoint Called:** {full_query_url}",
            "",
        ]

        for idx, st in enumerate(studies, start=1):
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
            lines.append(f"- **Primary Outcome:** {prim_measure}")
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
            return "Error: nct_id must start with 'NCT' (for example, 'NCT03819153')."

        url = f"{self.base_url}/{clean_id}"
        status_code, st = await _async_get_json(url, {}, float(self.valves.TIMEOUT_SECONDS))
        if status_code == 404:
            return f"No ClinicalTrials.gov study found for NCT ID `{clean_id}`."
        if status_code != 200 or not st:
            return f"Error fetching study `{clean_id}` (HTTP {status_code})."

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

        lines = [
            f"# ClinicalTrials.gov Study Record: {clean_id}" + (f" ({acronym})" if acronym else ""),
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
