"""
title: NLM RxNorm Drug Normalization and Concept Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries active NLM RxNorm REST endpoints (rxnav.nlm.nih.gov/REST/) for RxCUI normalization, active ingredients, brand/generic mappings, clinical drug dose forms/strengths, and FDA-label drug interaction prose (replacing the retired Jan 2024 RxNav interaction endpoint).
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
            return "Error: drug_name_or_rxcui is required."

        timeout_s = float(self.valves.TIMEOUT_SECONDS)
        rxcui = None
        approx_candidates = []

        if query.isdigit():
            rxcui = query
        else:
            st1, d1 = await _async_get_json(
                f"{self.rxnav_base}/rxcui.json",
                {"name": query, "search": "2"},
                timeout_s,
            )
            if st1 == 200 and d1:
                ids = (d1.get("idGroup") or {}).get("rxnormId") or []
                if ids:
                    rxcui = ids[0]

            st_ap, d_ap = await _async_get_json(
                f"{self.rxnav_base}/approximateTerm.json",
                {"term": query, "maxEntries": "5"},
                timeout_s,
            )
            if st_ap == 200 and d_ap:
                cands = (d_ap.get("approximateGroup") or {}).get("candidate") or []
                seen_c = set()
                for c in cands:
                    cid = c.get("rxcui")
                    cname = c.get("name")
                    if cid and cid not in seen_c:
                        seen_c.add(cid)
                        approx_candidates.append(
                            {"rxcui": cid, "name": cname or "", "score": c.get("score", "")}
                        )
                if not rxcui and approx_candidates:
                    rxcui = approx_candidates[0]["rxcui"]

        if not rxcui:
            return f"No RxNorm concept found for '{query}'."

        props = {}
        st_pr, d_pr = await _async_get_json(
            f"{self.rxnav_base}/rxcui/{rxcui}/properties.json", {}, timeout_s
        )
        if st_pr == 200 and d_pr:
            props = d_pr.get("properties") or {}

        tty_map: Dict[str, List[Dict[str, str]]] = {
            "IN": [],
            "PIN": [],
            "MIN": [],
            "BN": [],
            "DF": [],
            "SCD": [],
            "SBD": [],
        }
        st_all, d_all = await _async_get_json(
            f"{self.rxnav_base}/rxcui/{rxcui}/allrelated.json", {}, timeout_s
        )
        if st_all == 200 and d_all:
            groups = (d_all.get("allRelatedGroup") or {}).get("conceptGroup") or []
            for g in groups:
                tty = g.get("tty")
                if tty in tty_map:
                    for cp in g.get("conceptProperties") or []:
                        tty_map[tty].append(
                            {"rxcui": cp.get("rxcui", ""), "name": _clean_str(cp.get("name", ""))}
                        )

        lines = [
            f"# NLM RxNorm Concept Normalization: {query}",
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

        if tty_map["IN"]:
            lines.append(
                "- **Base Ingredient (IN):** "
                + ", ".join(f"{x['name']} (RxCUI `{x['rxcui']}`)" for x in tty_map["IN"][:8])
            )
        else:
            lines.append("- **Base Ingredient (IN):** None listed")

        if tty_map["PIN"]:
            lines.append(
                "- **Precise Ingredient / Salt Form (PIN):** "
                + ", ".join(f"{x['name']} (RxCUI `{x['rxcui']}`)" for x in tty_map["PIN"][:8])
            )
        if tty_map["MIN"]:
            lines.append(
                "- **Multiple Ingredients (MIN):** "
                + ", ".join(f"{x['name']} (RxCUI `{x['rxcui']}`)" for x in tty_map["MIN"][:8])
            )

        lines.append("")
        lines.append("## 3. Brand Names (BN) and Dose Forms (DF)")
        if tty_map["BN"]:
            lines.append(
                "- **Brand Names (BN):** "
                + ", ".join(f"{x['name']} (`{x['rxcui']}`)" for x in tty_map["BN"][:12])
            )
        else:
            lines.append("- **Brand Names (BN):** None linked")

        if tty_map["DF"]:
            lines.append(
                "- **Dose Forms (DF):** " + ", ".join(x["name"] for x in tty_map["DF"][:10])
            )

        if include_dose_forms:
            lines.append("")
            lines.append("## 4. Clinical Drug Strengths (SCD) and Branded Strengths (SBD)")
            if tty_map["SCD"]:
                for x in tty_map["SCD"][:12]:
                    lines.append(f"- **SCD:** {x['name']} (RxCUI `{x['rxcui']}`)")
            else:
                lines.append("- **SCD:** None listed")

            if tty_map["SBD"]:
                for x in tty_map["SBD"][:8]:
                    lines.append(f"- **SBD:** {x['name']} (RxCUI `{x['rxcui']}`)")

        if approx_candidates:
            lines.append("")
            lines.append("## 5. Top Approximate Term Candidates")
            for c in approx_candidates[:5]:
                lines.append(
                    f"- `{c['rxcui']}`: {_clean_str(c['name'])} (match score: {c['score']})"
                )

        lines.append("")
        lines.append(
            "*Note: NLM permanently retired the legacy RxNav Drug Interaction API (/REST/interaction/*) on January 2, 2024. Use `check_fda_label_interactions` in this toolkit or the openFDA Drug Label tool for official FDA drug interaction sections.*"
        )
        return "\n".join(lines)

    async def check_fda_label_interactions(
        self,
        drug_names: List[str],
    ) -> str:
        """
        Replacement for the retired NLM RxNav Drug Interaction API (discontinued Jan 2, 2024).
        Normalizes each drug via NLM RxNorm to its generic ingredient name, then cross-checks
        the official FDA drug label `drug_interactions` section (`api.fda.gov/drug/label.json`)
        for pairwise mentions across the provided medication list.

        :param drug_names: List of 2 to 8 drug names to cross-check (e.g., ['apixaban', 'clarithromycin', 'aspirin']).
        :return: Structured Markdown report of FDA label interaction sections and matched drug mentions.
        """
        cleaned_drugs = [d.strip() for d in (drug_names or []) if d and d.strip()]
        if len(cleaned_drugs) < 2:
            return "Error: Please provide at least 2 drug names in drug_names."
        cleaned_drugs = cleaned_drugs[:8]

        timeout_s = float(self.valves.TIMEOUT_SECONDS)
        resolved = []
        for d in cleaned_drugs:
            rxcui = None
            ing_name = d
            st1, d1 = await _async_get_json(
                f"{self.rxnav_base}/rxcui.json", {"name": d, "search": "2"}, timeout_s
            )
            if st1 == 200 and d1:
                ids = (d1.get("idGroup") or {}).get("rxnormId") or []
                if ids:
                    rxcui = ids[0]
                    st_rel, d_rel = await _async_get_json(
                        f"{self.rxnav_base}/rxcui/{rxcui}/related.json", {"tty": "IN"}, timeout_s
                    )
                    if st_rel == 200 and d_rel:
                        groups = (d_rel.get("relatedGroup") or {}).get("conceptGroup") or []
                        for g in groups:
                            cps = g.get("conceptProperties") or []
                            if cps and cps[0].get("name"):
                                ing_name = cps[0]["name"]
                                rxcui = cps[0].get("rxcui", rxcui)
                                break

            interaction_text = ""
            set_id = ""
            st_fda, d_fda = await _async_get_json(
                "https://api.fda.gov/drug/label.json",
                {"search": f'openfda.generic_name:"{ing_name}"', "limit": "3"},
                timeout_s,
            )
            if st_fda == 200 and d_fda:
                res = d_fda.get("results") or []
                for rec in res:
                    di = rec.get("drug_interactions")
                    if di:
                        interaction_text = _clean_str(" ".join(di))
                        set_id = rec.get("set_id", "")
                        break

            resolved.append(
                {
                    "input": d,
                    "ingredient": ing_name,
                    "rxcui": rxcui or "N/A",
                    "interaction_text": interaction_text,
                    "set_id": set_id,
                }
            )

        lines = [
            "# RxNorm + openFDA Label Drug Interaction Cross-Check",
            "",
            "## 1. Normalized Medications (NLM RxNorm)",
        ]
        for item in resolved:
            lines.append(
                f"- **{item['input']}** -> Ingredient: **{item['ingredient']}** (RxCUI `{item['rxcui']}`, SPL SetID `{item['set_id'] or 'N/A'}`)"
            )

        lines.append("")
        lines.append("## 2. Pairwise Mentions in Official FDA Label `drug_interactions` Sections")
        found_any = False
        for i, primary in enumerate(resolved):
            text_low = primary["interaction_text"].lower()
            if not text_low:
                continue
            for j, other in enumerate(resolved):
                if i == j:
                    continue
                terms = {other["input"].lower(), other["ingredient"].lower()}
                for t in terms:
                    if len(t) >= 3 and t in text_low:
                        idx = text_low.find(t)
                        start = max(0, idx - 140)
                        end = min(len(primary["interaction_text"]), idx + 360)
                        snippet = primary["interaction_text"][start:end]
                        lines.append(
                            f"- **Label for {primary['ingredient']}** mentions **{other['ingredient']}** (`{t}`): \"...{snippet}...\""
                        )
                        found_any = True
                        break

        if not found_any:
            lines.append(
                "- No direct literal name match found between pairs; review class-level interactions below (FDA labels frequently list drug classes such as CYP3A4 inhibitors, anticoagulants, NSAIDs, or ACE inhibitors rather than every individual drug name)."
            )

        lines.append("")
        lines.append("## 3. Summary of FDA `drug_interactions` Sections per Drug")
        for item in resolved:
            excerpt = (
                item["interaction_text"][:1000] + "... [truncated]"
                if len(item["interaction_text"]) > 1000
                else (item["interaction_text"] or "No drug_interactions section found in openFDA.")
            )
            lines.append(f"### {item['ingredient']} (RxCUI `{item['rxcui']}`)")
            lines.append(excerpt)
            lines.append("")

        return "\n".join(lines)
