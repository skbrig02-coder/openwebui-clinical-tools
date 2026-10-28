"""
title: openFDA and DailyMed Drug Label Lookup
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Queries official FDA drug labels via openFDA (api.fda.gov/drug/label.json) and NLM DailyMed SPLs (dailymed.nlm.nih.gov/dailymed/services/v2/) for indications, boxed warnings, dosing, contraindications, adverse reactions, and drug interactions.
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
            return "Error: drug_name is required."

        needle = query_drug.upper()
        chosen_record: Optional[dict] = None
        query_url_used = ""
        timeout_s = float(self.valves.TIMEOUT_SECONDS)

        for field in ("openfda.generic_name", "openfda.brand_name", "openfda.substance_name"):
            params = {
                "search": f'{field}:"{query_drug}"',
                "limit": "5" if prefer_monotherapy else "1",
            }
            if self.valves.OPENFDA_API_KEY.strip():
                params["api_key"] = self.valves.OPENFDA_API_KEY.strip()

            url = "https://api.fda.gov/drug/label.json"
            status_code, data = await _async_get_json(url, params, timeout_s)
            if status_code != 200 or not data:
                continue

            results = data.get("results") or []
            if not results:
                continue

            safe_q = urllib.parse.urlencode({"search": params["search"], "limit": params["limit"]})
            query_url_used = f"{url}?{safe_q}"

            if prefer_monotherapy:
                for rec in results:
                    openfda_meta = rec.get("openfda") or {}
                    subs = openfda_meta.get("substance_name") or []
                    if len(subs) == 1 and needle in subs[0].upper():
                        chosen_record = rec
                        break
            if chosen_record is None:
                chosen_record = results[0]
            break

        dailymed_items = []
        dailymed_url_used = ""
        if include_dailymed_spls:
            dm_url = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
            dm_params = {"drug_name": query_drug, "pagesize": "5"}
            dailymed_url_used = f"{dm_url}?{urllib.parse.urlencode(dm_params)}"
            dm_status, dm_data = await _async_get_json(dm_url, dm_params, timeout_s)
            if dm_status == 200 and dm_data:
                for item in (dm_data.get("data") or [])[:5]:
                    setid = item.get("setid", "")
                    dailymed_items.append(
                        {
                            "title": _clean_text(item.get("title", ""), 300),
                            "setid": setid,
                            "published_date": item.get("published_date", ""),
                            "spl_version": item.get("spl_version", ""),
                            "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"
                            if setid
                            else "",
                        }
                    )

        if chosen_record is None and not dailymed_items:
            return f"No FDA label or DailyMed SPL records found for '{query_drug}'. Check spelling or try the generic ingredient name."

        lines = [f"# Official FDA / DailyMed Drug Label Report: {query_drug}", ""]

        if chosen_record is not None:
            openfda_meta = chosen_record.get("openfda") or {}
            brands = ", ".join((openfda_meta.get("brand_name") or [])[:5]) or "N/A"
            generics = ", ".join((openfda_meta.get("generic_name") or [])[:5]) or "N/A"
            substances = ", ".join((openfda_meta.get("substance_name") or [])[:5]) or "N/A"
            manufacturers = ", ".join((openfda_meta.get("manufacturer_name") or [])[:3]) or "N/A"
            routes = ", ".join((openfda_meta.get("route") or [])[:5]) or "N/A"
            rxcuis = ", ".join((openfda_meta.get("rxcui") or [])[:8]) or "N/A"
            set_id = chosen_record.get("set_id") or (openfda_meta.get("spl_set_id") or ["N/A"])[0]
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
            pop_text = _extract_section(chosen_record, "use_in_specific_populations", max_c)
            lines.append(pop_text)
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
            "*Source: U.S. Food and Drug Administration (openFDA) and NLM DailyMed Structured Product Labeling (SPL). For clinical reference only.*"
        )
        return "\n".join(lines)
