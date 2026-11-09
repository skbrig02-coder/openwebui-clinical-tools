"""
title: NLM RxNorm Drug Normalization and Concept Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries active NLM RxNorm REST endpoints (rxnav.nlm.nih.gov/REST/) for RxCUI normalization, active ingredients, brand/generic mappings, clinical drug dose forms/strengths, and FDA-label drug interaction prose (replacing the retired Jan 2024 RxNav interaction endpoint). This tool does not call NCBI E-utilities or PubMed. Reports use a UTC retrieval timestamp and keep a successful match count of 0 separate from timeouts, HTTP errors, and malformed responses.
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
from typing import Any, Dict, List, Optional, Tuple

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


def _clean_str(val: str) -> str:
    if not val:
        return ""
    return " ".join(str(val).replace("\u2014", " - ").replace("\u2013", "-").split())


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
            description="HTTP request timeout in seconds for NLM RxNav and openFDA requests.",
            ge=5,
            le=60,
        )

    def __init__(self):
        self.valves = self.Valves()
        self.rxnav_base = "https://rxnav.nlm.nih.gov/REST"

    async def lookup_rxnorm_drug(
        self,
        drug_name_or_rxcui: str,
        include_dose_forms: bool = True,
    ) -> str:
        """
        Normalize a drug name (brand or generic) or RxCUI using the NLM RxNorm REST API.
        Returns canonical RxCUI, term type (TTY), active ingredient(s) (IN/PIN/MIN),
        brand names (BN), clinical drug strengths/dose forms (SCD/SBD), and approximate candidates.
        Note: Does NOT call the retired NLM RxNav Drug Interaction API (discontinued Jan 2, 2024).

        :param drug_name_or_rxcui: Drug name (e.g., 'Eliquis', 'apixaban', 'metformin') or numeric RxCUI (e.g., '1364430').
        :param include_dose_forms: If True, includes semantic clinical drugs (strength + dose form) and branded forms.
        :return: Structured Markdown report with RxNorm identifiers, ingredients, brands, and dose forms.
        """
        query = (drug_name_or_rxcui or "").strip()
        if not query:
            return _invalid_lookup("Error: drug_name_or_rxcui is required.")

        timeout_s = float(self.valves.TIMEOUT_SECONDS)
        rxcui = None
        approx_candidates = []
        call_rows = []
        errors = []

        def remember(name, fetched, state):
            call_rows.append((name, fetched, state))
            if state == "error":
                errors.append(fetched.error_type or ("malformed_response" if fetched.status_code == 200 else "http_error"))

        if query.isdigit():
            rxcui = query
            call_rows.append(("name_lookup", None, "not_requested"))
        else:
            exact = await _async_get_json(
                f"{self.rxnav_base}/rxcui.json",
                {"name": query, "search": "2"},
                timeout_s,
            )
            if exact.error_type or exact.status_code != 200 or not isinstance(exact.payload, dict):
                remember("rxcui.json", exact, "error")
            else:
                ids = (exact.payload.get("idGroup") or {}).get("rxnormId") or []
                if ids:
                    rxcui = str(ids[0])
                    remember("rxcui.json", exact, "ok")
                else:
                    remember("rxcui.json", exact, "no_match")

            approx = await _async_get_json(
                f"{self.rxnav_base}/approximateTerm.json",
                {"term": query, "maxEntries": "5"},
                timeout_s,
            )
            if approx.error_type or approx.status_code != 200 or not isinstance(approx.payload, dict):
                remember("approximateTerm.json", approx, "error")
            else:
                cands = (approx.payload.get("approximateGroup") or {}).get("candidate") or []
                seen_c = set()
                if isinstance(cands, list):
                    for cand in cands:
                        if not isinstance(cand, dict):
                            continue
                        cid = cand.get("rxcui")
                        if cid and cid not in seen_c:
                            seen_c.add(cid)
                            approx_candidates.append(
                                {"rxcui": cid, "name": cand.get("name") or "", "score": cand.get("score", "")}
                            )
                remember("approximateTerm.json", approx, "ok" if approx_candidates else "no_match")
                if not rxcui and approx_candidates:
                    rxcui = approx_candidates[0]["rxcui"]

        if not rxcui:
            failed = [row for row in call_rows if row[2] == "error"]
            if failed:
                lines = [
                    f"# NLM RxNorm Concept Normalization: {query}",
                    "",
                    "## Retrieval record",
                    "- status: error",
                    "- match_count: unavailable",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- source: NLM RxNorm REST API (https://rxnav.nlm.nih.gov/REST)",
                ]
                for name, fetched, state in call_rows:
                    if fetched is None:
                        continue
                    err = fetched.error_type or ("malformed_response" if fetched.status_code == 200 else "http_error")
                    if state == "error":
                        lines.append(f"- {name}: status=error error_type: {err} http_status={fetched.status_code}")
                    else:
                        lines.append(f"- {name}: status={state}")
                lines.append(f"- note: {_UPSTREAM_FAILURE_NOTE}")
                return "\n".join(lines)
            return "\n".join(
                [
                    f"# NLM RxNorm Concept Normalization: {query}",
                    "",
                    "## Retrieval record",
                    "- status: no_match",
                    "- match_count: 0",
                    f"- retrieved_at_utc: {_utc_now_iso()}",
                    "- provenance: live_http",
                    "- ncbi_eutilities: not_used",
                    "- source: NLM RxNorm REST API (https://rxnav.nlm.nih.gov/REST)",
                    "- note: RxNorm responded successfully and the match count is 0.",
                ]
            )

        props_fetch = await _async_get_json(f"{self.rxnav_base}/rxcui/{rxcui}/properties.json", {}, timeout_s)
        if props_fetch.error_type or props_fetch.status_code != 200 or not isinstance(props_fetch.payload, dict):
            remember("properties.json", props_fetch, "error")
            props = {}
        else:
            remember("properties.json", props_fetch, "ok")
            props = props_fetch.payload.get("properties") or {}

        tty_map = {"IN": [], "PIN": [], "MIN": [], "BN": [], "DF": [], "SCD": [], "SBD": []}
        related = await _async_get_json(f"{self.rxnav_base}/rxcui/{rxcui}/allrelated.json", {}, timeout_s)
        related_ok = not related.error_type and related.status_code == 200 and isinstance(related.payload, dict)
        if not related_ok:
            remember("allrelated.json", related, "error")
        else:
            remember("allrelated.json", related, "ok")
            groups = (related.payload.get("allRelatedGroup") or {}).get("conceptGroup") or []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                tty = group.get("tty")
                if tty in tty_map:
                    for cp in group.get("conceptProperties") or []:
                        if isinstance(cp, dict):
                            tty_map[tty].append({"rxcui": cp.get("rxcui", ""), "name": _clean_str(cp.get("name", ""))})

        overall = "partial" if errors else "ok"
        lines = [
            f"# NLM RxNorm Concept Normalization: {query}",
            "",
            "## Retrieval record",
            f"- status: {overall}",
            "- match_count: 1",
            f"- retrieved_at_utc: {_utc_now_iso()}",
            "- provenance: live_http",
            "- ncbi_eutilities: not_used",
            "- source: NLM RxNorm REST API (https://rxnav.nlm.nih.gov/REST)",
        ]
        for name, fetched, state in call_rows:
            if fetched is None:
                lines.append(f"- {name}: status={state}")
                continue
            if state == "error":
                err = fetched.error_type or ("malformed_response" if fetched.status_code == 200 else "http_error")
                lines.append(f"- {name}: status=error error_type: {err} http_status={fetched.status_code}")
            else:
                lines.append(f"- {name}: status={state}")
        if overall == "partial":
            lines.append(f"- note: {_UPSTREAM_FAILURE_NOTE}")
        else:
            lines.append("- note: RxNorm responded for the calls in this report. retrieved_at_utc is when this tool called RxNorm.")
        lines.extend(
            [
                "",
                "## 1. Primary Resolved RxNorm Concept",
                f"- **Resolved RxCUI:** `{rxcui}`",
                f"- **Canonical Name:** {_clean_str(props.get('name', query))}",
                f"- **Term Type (TTY):** `{props.get('tty', 'N/A')}`",
                f"- **Synonym:** {_clean_str(props.get('synonym', 'None')) or 'None'}",
                f"- **RxNav Properties URL:** {self.rxnav_base}/rxcui/{rxcui}/properties.json",
                f"- **RxNav AllRelated URL:** {self.rxnav_base}/rxcui/{rxcui}/allrelated.json",
                "",
                "## 2. Active Ingredient(s) and Salt/Multi-Ingredient Forms",
            ]
        )
        if not related_ok:
            lines.append("- **Base Ingredient (IN):** not retrieved. Upstream failure is not a finding that none exist.")
        elif tty_map["IN"]:
            lines.append("- **Base Ingredient (IN):** " + ", ".join(f"{x['name']} (RxCUI `{x['rxcui']}`)" for x in tty_map["IN"][:8]))
        else:
            lines.append("- **Base Ingredient (IN):** None listed")
        if related_ok and tty_map["PIN"]:
            lines.append("- **Precise Ingredient / Salt Form (PIN):** " + ", ".join(f"{x['name']} (RxCUI `{x['rxcui']}`)" for x in tty_map["PIN"][:8]))
        if related_ok and tty_map["MIN"]:
            lines.append("- **Multiple Ingredients (MIN):** " + ", ".join(f"{x['name']} (RxCUI `{x['rxcui']}`)" for x in tty_map["MIN"][:8]))

        lines.append("")
        lines.append("## 3. Brand Names (BN) and Dose Forms (DF)")
        if not related_ok:
            lines.append("- **Brand Names (BN):** not retrieved. Upstream failure is not a finding that none exist.")
        elif tty_map["BN"]:
            lines.append("- **Brand Names (BN):** " + ", ".join(f"{x['name']} (`{x['rxcui']}`)" for x in tty_map["BN"][:12]))
        else:
            lines.append("- **Brand Names (BN):** None linked")
        if related_ok and tty_map["DF"]:
            lines.append("- **Dose Forms (DF):** " + ", ".join(x["name"] for x in tty_map["DF"][:10]))

        if include_dose_forms:
            lines.append("")
            lines.append("## 4. Clinical Drug Strengths (SCD) and Branded Strengths (SBD)")
            if not related_ok:
                lines.append("- **SCD / SBD:** not retrieved. Upstream failure is not a finding that none exist.")
            else:
                if tty_map["SCD"]:
                    for item in tty_map["SCD"][:12]:
                        lines.append(f"- **SCD:** {item['name']} (RxCUI `{item['rxcui']}`)")
                else:
                    lines.append("- **SCD:** None listed")
                for item in tty_map["SBD"][:8]:
                    lines.append(f"- **SBD:** {item['name']} (RxCUI `{item['rxcui']}`)")

        if approx_candidates:
            lines.append("")
            lines.append("## 5. Top Approximate Term Candidates")
            for cand in approx_candidates[:5]:
                lines.append(f"- `{cand['rxcui']}`: {_clean_str(cand['name'])} (match score: {cand['score']})")

        lines.append("")
        lines.append(
            "*Note: NLM permanently retired the legacy RxNav Drug Interaction API (/REST/interaction/*) on January 2, 2024. Use `check_fda_label_interactions` in this toolkit or the openFDA Drug Label tool for official FDA drug interaction sections.*"
        )
        return "\n".join(lines)

    async def check_fda_label_interactions(self, drug_names: List[str]) -> str:
        """
        Replacement for the retired NLM RxNav Drug Interaction API (discontinued Jan 2, 2024).
        Normalizes each drug via NLM RxNorm to its generic ingredient name, then cross-checks
        the official FDA drug label `drug_interactions` section (`api.fda.gov/drug/label.json`)
        for pairwise mentions across the provided medication list.

        :param drug_names: List of 2 to 8 drug names to cross-check (e.g., ['apixaban', 'clarithromycin', 'aspirin']).
        :return: Structured Markdown report of FDA label interaction sections and matched drug mentions.
        """
        cleaned_drugs = [d.strip() for d in (drug_names or []) if d and str(d).strip()]
        if len(cleaned_drugs) < 2:
            return _invalid_lookup("Error: Please provide at least 2 drug names in drug_names.")
        cleaned_drugs = cleaned_drugs[:8]
        timeout_s = float(self.valves.TIMEOUT_SECONDS)
        resolved = []
        for drug in cleaned_drugs:
            item = {
                "input": drug,
                "ingredient": drug,
                "rxcui": "N/A",
                "interaction_text": None,
                "set_id": "",
                "rx_state": "ok",
                "fda_state": "ok",
                "rx_error": "",
                "fda_error": "",
                "fda_http": 0,
            }
            exact = await _async_get_json(f"{self.rxnav_base}/rxcui.json", {"name": drug, "search": "2"}, timeout_s)
            if exact.error_type or exact.status_code != 200 or not isinstance(exact.payload, dict):
                item["rx_state"] = "error"
                item["rx_error"] = exact.error_type or ("malformed_response" if exact.status_code == 200 else "http_error")
            else:
                ids = (exact.payload.get("idGroup") or {}).get("rxnormId") or []
                if not ids:
                    item["rx_state"] = "no_match"
                else:
                    item["rxcui"] = ids[0]
                    related = await _async_get_json(
                        f"{self.rxnav_base}/rxcui/{item['rxcui']}/related.json",
                        {"tty": "IN"},
                        timeout_s,
                    )
                    if related.error_type or related.status_code != 200 or not isinstance(related.payload, dict):
                        item["rx_state"] = "error"
                        item["rx_error"] = related.error_type or ("malformed_response" if related.status_code == 200 else "http_error")
                    else:
                        groups = (related.payload.get("relatedGroup") or {}).get("conceptGroup") or []
                        for group in groups:
                            if not isinstance(group, dict):
                                continue
                            props = group.get("conceptProperties") or []
                            if props and isinstance(props[0], dict) and props[0].get("name"):
                                item["ingredient"] = props[0]["name"]
                                item["rxcui"] = props[0].get("rxcui", item["rxcui"])
                                break
            fda = await _async_get_json(
                "https://api.fda.gov/drug/label.json",
                {"search": f'openfda.generic_name:"{item["ingredient"]}"', "limit": "3"},
                timeout_s,
            )
            item["fda_http"] = fda.status_code
            if fda.status_code == 404:
                item["fda_state"] = "no_match"
                item["interaction_text"] = ""
            elif fda.error_type or fda.status_code != 200 or not isinstance(fda.payload, dict):
                item["fda_state"] = "error"
                item["fda_error"] = fda.error_type or ("malformed_response" if fda.status_code == 200 else "http_error")
            else:
                results = fda.payload.get("results")
                if not isinstance(results, list):
                    item["fda_state"] = "error"
                    item["fda_error"] = "malformed_response"
                else:
                    item["interaction_text"] = ""
                    item["fda_state"] = "no_match" if not results else "ok"
                    for rec in results:
                        if not isinstance(rec, dict):
                            continue
                        interactions = rec.get("drug_interactions")
                        if interactions:
                            item["interaction_text"] = _clean_str(" ".join(str(x) for x in interactions))
                            item["set_id"] = rec.get("set_id", "")
                            item["fda_state"] = "ok"
                            break
                    if results and item["fda_state"] != "ok":
                        item["fda_state"] = "ok"
                        item["set_id"] = results[0].get("set_id", "") if isinstance(results[0], dict) else ""
            resolved.append(item)

        fda_failed = [item for item in resolved if item["fda_state"] == "error"]
        any_text = any(item["interaction_text"] is not None and item["fda_state"] != "error" for item in resolved)
        if fda_failed and not any_text:
            overall = "error"
        elif fda_failed or any(item["rx_state"] == "error" for item in resolved):
            overall = "partial"
        else:
            overall = "ok"

        lines = [
            "# RxNorm + openFDA Label Drug Interaction Cross-Check",
            "",
            "## Retrieval record",
            f"- status: {overall}",
            "- match_count: unavailable" if overall == "error" else "- match_count: pending",
            f"- retrieved_at_utc: {_utc_now_iso()}",
            "- provenance: live_http",
            "- ncbi_eutilities: not_used",
            "- source: NLM RxNorm REST API and openFDA drug/label. Static PMID citations are not used.",
        ]
        for item in resolved:
            if item["rx_state"] == "error":
                lines.append(f"- rxnorm[{item['input']}]: status=error error_type: {item['rx_error']}")
            else:
                lines.append(f"- rxnorm[{item['input']}]: status={item['rx_state']}")
            if item["fda_state"] == "error":
                lines.append(
                    f"- openfda[{item['ingredient']}]: status=error error_type: {item['fda_error']} http_status={item['fda_http']}"
                )
            else:
                lines.append(f"- openfda[{item['ingredient']}]: status={item['fda_state']} http_status={item['fda_http']}")
        if overall in ("error", "partial"):
            lines.append(f"- note: {_UPSTREAM_FAILURE_NOTE}")
        lines.extend(["", "## 1. Normalized Medications (NLM RxNorm)"])
        for item in resolved:
            lines.append(
                f"- **{item['input']}** -> Ingredient: **{item['ingredient']}** (RxCUI `{item['rxcui']}`, SPL SetID `{item['set_id'] or 'N/A'}`)"
            )
        lines.append("")
        lines.append("## 2. Pairwise Mentions in Official FDA Label `drug_interactions` Sections")
        found_any = False
        comparable = [item for item in resolved if item["fda_state"] != "error" and item["interaction_text"] is not None]
        if len(comparable) == len(resolved):
            for primary in comparable:
                text_low = (primary["interaction_text"] or "").lower()
                if not text_low:
                    continue
                for other in comparable:
                    if other is primary:
                        continue
                    terms = {other["input"].lower(), other["ingredient"].lower()}
                    for term in terms:
                        if len(term) >= 3 and term in text_low:
                            idx = text_low.find(term)
                            start = max(0, idx - 140)
                            end = min(len(primary["interaction_text"]), idx + 360)
                            snippet = primary["interaction_text"][start:end]
                            lines.append(
                                f"- **Label for {primary['ingredient']}** mentions **{other['ingredient']}** (`{term}`): \"...{snippet}...\""
                            )
                            found_any = True
                            break
            pair_count = sum(1 for line in lines if line.startswith("- **Label for "))
            lines.append(f"- pairwise_literal_match_count: {pair_count}")
            if not found_any:
                lines.append("- note: The FDA labels were retrieved. Literal pairwise name matches were counted and the count is 0.")
        else:
            lines.append("- Pairwise literal matching was not completed for every drug because at least one label request failed.")
        lines.append("")
        lines.append("## 3. Summary of FDA `drug_interactions` Sections per Drug")
        for item in resolved:
            lines.append(f"### {item['ingredient']} (RxCUI `{item['rxcui']}`)")
            if item["fda_state"] == "error":
                lines.append("The label section was not retrieved. This is not a finding of zero matching records.")
            else:
                text = item["interaction_text"] or ""
                if len(text) > 1000:
                    text = text[:1000] + "... [truncated]"
                lines.append(text or "The retrieved label response did not include a drug_interactions section.")
            lines.append("")
        # Replace the pending match count once pairwise counting is known.
        if overall != "error":
            for i, line in enumerate(lines):
                if line == "- match_count: pending":
                    shown = "0"
                    for candidate in lines:
                        if candidate.startswith("- pairwise_literal_match_count: "):
                            shown = candidate.split(": ", 1)[1]
                    lines[i] = f"- match_count: {shown}"
                    break
        return "\n".join(lines)
