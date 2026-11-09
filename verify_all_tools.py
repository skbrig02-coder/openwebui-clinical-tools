"""
Live API and Deterministic Unit Verification Suite for Open WebUI Medical Tools.
Tests all 6 tool categories against real federal APIs (openFDA, DailyMed v2, NLM RxNorm,
ClinicalTrials.gov v2, NLM ICD-10-CM v3) and reference clinical calculator test vectors.
"""

import asyncio
import contextlib
import io
import json
import math
import sys
import tempfile
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import openfda_dailymed_drug_label
import nlm_rxnorm_lookup
import clinicaltrials_v2_search
import clinical_calculators
import nlm_icd10cm_lookup
import local_clinical_library_search
from openfda_dailymed_drug_label import Tools as OpenFdaDailyMedTools
from nlm_rxnorm_lookup import Tools as RxNormTools
from clinicaltrials_v2_search import Tools as ClinicalTrialsTools
from clinical_calculators import Tools as ClinicalCalculatorTools
from nlm_icd10cm_lookup import Tools as Icd10Tools
from local_clinical_library_search import Tools as LocalLibraryTools

FROZEN_UTC = "2026-11-07T15:04:05Z"
OPENFDA_KEY_SENTINEL = "SENTINEL_OPENFDA_KEY_9f3c"


_CLOCK_RESTORE = []


def _freeze_clock(module) -> None:
    _CLOCK_RESTORE.append((module, module._utc_now_iso))
    module._utc_now_iso = lambda: FROZEN_UTC


def _restore_clocks() -> None:
    while _CLOCK_RESTORE:
        module, original = _CLOCK_RESTORE.pop()
        module._utc_now_iso = original


class _HttpBody:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.status_code = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


@contextlib.contextmanager
def patched_urlopen(router):
    """Deterministic stand-in for upstream HTTP. `router(url)` returns (status, body) or raises."""
    original = urllib.request.urlopen

    def _open(req, timeout=15):
        url = getattr(req, "full_url", str(req))
        outcome = router(url)
        if isinstance(outcome, BaseException):
            raise outcome
        status, body = outcome
        if isinstance(body, str):
            body = body.encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(
                url,
                status,
                "status",
                EmailMessage(),
                io.BytesIO(body),
            )
        return _HttpBody(status, body)

    urllib.request.urlopen = _open
    try:
        yield
    finally:
        urllib.request.urlopen = original


def _json_bytes(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _assert_machine_time(text: str, label: str) -> None:
    assert f"retrieved_at_utc: {FROZEN_UTC}" in text or f"computed_at_utc: {FROZEN_UTC}" in text or f"searched_at_utc: {FROZEN_UTC}" in text or f"checked_at_utc: {FROZEN_UTC}" in text, (
        f"{label} missing an explicit UTC timestamp:\n{text}"
    )
    assert "ncbi_eutilities: not_used" in text, f"{label} must state that NCBI E-utilities were not called:\n{text}"


async def run_deterministic_tests() -> int:
    """Offline regressions for mortality mapping, static citations, and lookup status."""
    try:
        return await _run_deterministic_tests()
    finally:
        _restore_clocks()


async def _run_deterministic_tests() -> int:
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        if not condition:
            raise AssertionError(message)
        checks += 1

    print("=== Deterministic regressions (mocked upstream, no network) ===")
    _restore_clocks()
    _freeze_clock(clinical_calculators)
    _freeze_clock(openfda_dailymed_drug_label)
    _freeze_clock(nlm_rxnorm_lookup)
    _freeze_clock(clinicaltrials_v2_search)
    _freeze_clock(nlm_icd10cm_lookup)
    _freeze_clock(local_clinical_library_search)

    calc = ClinicalCalculatorTools()

    # Wiesner 2003 Table 4 is original MELD only. This vector's MELD 3.0 (32) sits in a
    # higher historical band than MELD(i) (25); mapping the percentage to MELD 3.0 is the bug.
    out_meld = calc.calculate_meld_scores(3.2, 1.8, 2.1, 128.0, sex="F", albumin_g_dl=2.6)
    check("**25**" in out_meld and "**29**" in out_meld and "**32**" in out_meld, out_meld)
    check("52.6%" not in out_meld, f"MELD 3.0 must not receive the 30-39 Wiesner band:\n{out_meld}")
    check("71.3%" not in out_meld, out_meld)
    check("19.6%" in out_meld, out_meld)
    check("MELD(i) 25" in out_meld and "band MELD 20-29" in out_meld, out_meld)
    check("MELD-Na mortality percentage: unavailable" in out_meld, out_meld)
    check("MELD 3.0 mortality percentage: unavailable" in out_meld, out_meld)
    check("12512033" in out_meld, out_meld)
    check("clinically validated" not in out_meld.lower(), out_meld)
    check("provenance: local_deterministic_calculation" in out_meld, out_meld)
    check("citation_mode: static_pmid" in out_meld, out_meld)
    _assert_machine_time(out_meld, "MELD")
    assert_no_forbidden_dashes(out_meld, "MELD mortality output")

    # Low sodium moves MELD-Na into 20-29 while original MELD(i) stays in 10-19.
    out_na_shift = calc.calculate_meld_scores(2.5, 1.5, 1.2, 125.0)
    check("**16**" in out_na_shift and "**26**" in out_na_shift, out_na_shift)
    check("6.0%" in out_na_shift and "MELD(i) 16" in out_na_shift, out_na_shift)
    check("19.6%" not in out_na_shift, f"MELD-Na 26 must not receive the 20-29 Wiesner band:\n{out_na_shift}")
    check("MELD-Na mortality percentage: unavailable" in out_na_shift, out_na_shift)

    # Table 4 has no integer-9 column (<9, then 10-19). Do not assign either neighboring rate.
    out_nine = calc.calculate_meld_scores(1.8, 1.0, 1.0, 137.0)
    check("**9**" in out_nine, out_nine)
    check("Original MELD 3-month mortality: unavailable" in out_nine, out_nine)
    check("1.9%" not in out_nine and "6.0%" not in out_nine, out_nine)

    out_low = calc.calculate_meld_scores(1.5, 1.0, 1.0, 137.0)
    check("MELD(i) 8" in out_low and "1.9%" in out_low and "band MELD <9" in out_low, out_low)

    out_ten = calc.calculate_meld_scores(2.5, 1.0, 1.0, 137.0)
    check("MELD(i) 10" in out_ten and "6.0%" in out_ten and "band MELD 10-19" in out_ten, out_ten)

    out_cap = calc.calculate_meld_scores(
        60.0, 20.0, 25.0, 100.0, sex="M", albumin_g_dl=0.5, dialysis_twice_in_past_week=True
    )
    check("**40**" in out_cap and "71.3%" in out_cap and "MELD(i) 40" in out_cap, out_cap)
    check("MELD 3.0 mortality percentage: unavailable" in out_cap, out_cap)
    check("MELD-Na Cr = **4.00 mg/dL**" in out_cap and "MELD 3.0 Cr = **3.00 mg/dL**" in out_cap, out_cap)

    # Formula lock for the published reference vector. Coefficients are recomputed here.
    cr, bili, inr, na = 2.1, 3.2, 1.8, 128.0
    meld_i_raw = 10.0 * (
        0.957 * math.log(cr) + 0.378 * math.log(bili) + 1.120 * math.log(inr) + 0.643
    )
    meld_i_tenth = round(meld_i_raw, 1)
    meld_na_raw = (
        meld_i_tenth
        + 1.32 * (137.0 - na)
        - (0.033 * meld_i_tenth * (137.0 - na))
    )
    alb = 2.6
    meld3_raw = (
        1.33
        + 4.56 * math.log(bili)
        + 0.82 * (137.0 - na)
        - 0.24 * (137.0 - na) * math.log(bili)
        + 9.09 * math.log(inr)
        + 11.14 * math.log(cr)
        + 1.85 * (3.5 - alb)
        - 1.83 * (3.5 - alb) * math.log(cr)
        + 6.0
    )
    check(f"unrounded: {meld_i_raw:.2f}" in out_meld, f"MELD(i) raw {meld_i_raw:.4f}\n{out_meld}")
    check(f"unrounded: {meld_na_raw:.2f}" in out_meld, f"MELD-Na raw {meld_na_raw:.4f}\n{out_meld}")
    check(f"unrounded: {meld3_raw:.2f}" in out_meld, f"MELD 3.0 raw {meld3_raw:.4f}\n{out_meld}")

    for bad in (
        dict(bilirubin_mg_dl=0.09, inr=1.0, creatinine_mg_dl=1.0, sodium_meq_l=137.0),
        dict(bilirubin_mg_dl=60.01, inr=1.0, creatinine_mg_dl=1.0, sodium_meq_l=137.0),
        dict(bilirubin_mg_dl=1.0, inr=0.49, creatinine_mg_dl=1.0, sodium_meq_l=137.0),
        dict(bilirubin_mg_dl=1.0, inr=20.01, creatinine_mg_dl=1.0, sodium_meq_l=137.0),
        dict(bilirubin_mg_dl=1.0, inr=1.0, creatinine_mg_dl=0.09, sodium_meq_l=137.0),
        dict(bilirubin_mg_dl=1.0, inr=1.0, creatinine_mg_dl=25.01, sodium_meq_l=137.0),
        dict(bilirubin_mg_dl=1.0, inr=1.0, creatinine_mg_dl=1.0, sodium_meq_l=99.9),
        dict(bilirubin_mg_dl=1.0, inr=1.0, creatinine_mg_dl=1.0, sodium_meq_l=170.1),
    ):
        rejected = calc.calculate_meld_scores(**bad)
        check(rejected.startswith("Input Validation Error:"), rejected)
        check("status: invalid_input" in rejected, rejected)
        check("1.9%" not in rejected and "unavailable" not in rejected.split("Input Validation Error:")[0], rejected)

    rejected_alb = calc.calculate_meld_scores(1.0, 1.0, 1.0, 137.0, sex="F", albumin_g_dl=0.49)
    check("albumin_g_dl" in rejected_alb and "status: invalid_input" in rejected_alb, rejected_alb)
    rejected_sex = calc.calculate_meld_scores(1.0, 1.0, 1.0, 137.0, sex="X", albumin_g_dl=3.0)
    check("status: invalid_input" in rejected_sex, rejected_sex)

    clamped = calc.calculate_meld_scores(0.1, 0.5, 0.1, 170.0, sex="F", albumin_g_dl=6.0)
    check("clamped to **1.00 mg/dL**" in clamped, clamped)
    check("clamped to **1.00**" in clamped, clamped)
    check("clamped to **137.0 mEq/L**" in clamped, clamped)
    check("clamped to **3.50 g/dL**" in clamped, clamped)
    low_na = calc.calculate_meld_scores(1.0, 1.0, 1.0, 100.0, sex="M", albumin_g_dl=0.5)
    check("clamped to **125.0 mEq/L**" in low_na and "clamped to **1.50 g/dL**" in low_na, low_na)

    calc.valves.INCLUDE_FORMULAS_AND_PMIDS = False
    out_no_pmid_section = calc.calculate_meld_scores(3.2, 1.8, 2.1, 128.0, sex="F", albumin_g_dl=2.6)
    check("## 3. Formulas and Primary Citations" not in out_no_pmid_section, out_no_pmid_section)
    check("MELD-Na mortality percentage: unavailable" in out_no_pmid_section, out_no_pmid_section)
    check("ncbi_eutilities: not_used" in out_no_pmid_section, out_no_pmid_section)
    calc.valves.INCLUDE_FORMULAS_AND_PMIDS = True

    out_crcl = calc.calculate_cockcroft_gault_crcl(65, 70.0, 1.2, "F", height_cm=165.0)
    check("**51.6 mL/min**" in out_crcl, out_crcl)
    check("provenance: local_deterministic_calculation" in out_crcl, out_crcl)
    check("Retrieved from PubMed" not in out_crcl and "E-utilities" not in out_crcl, out_crcl)
    _assert_machine_time(out_crcl, "CrCl")

    label = {
        "results": [
            {
                "set_id": "set-apixaban",
                "effective_time": "20240315",
                "boxed_warning": ["Bleeding risk."],
                "indications_and_usage": ["Reduce stroke risk."],
                "openfda": {
                    "generic_name": ["APIXABAN"],
                    "brand_name": ["ELIQUIS"],
                    "substance_name": ["APIXABAN"],
                },
            }
        ]
    }
    spls = {"data": [{"setid": "set-apixaban", "title": "Eliquis", "published_date": "Mar 15, 2024", "spl_version": "5"}]}

    def route_label_ok(url: str):
        if "api.fda.gov" in url:
            return 200, _json_bytes(label)
        if "dailymed.nlm.nih.gov" in url:
            return 200, _json_bytes(spls)
        raise AssertionError(url)

    fda = OpenFdaDailyMedTools()
    fda.valves.OPENFDA_API_KEY = OPENFDA_KEY_SENTINEL
    with patched_urlopen(route_label_ok):
        out_label = await fda.lookup_drug_label("apixaban")
    check("status: ok" in out_label, out_label)
    check("APIXABAN" in out_label.upper() and "Boxed Warning" in out_label, out_label)
    check("source_content_date: 20240315" in out_label, out_label)
    check("published_date: Mar 15, 2024" in out_label, out_label)
    check(f"retrieved_at_utc: {FROZEN_UTC}" in out_label, out_label)
    check("provenance: live_http" in out_label, out_label)
    check(OPENFDA_KEY_SENTINEL not in out_label, "API key leaked into the report")
    check("NCBI E-utilities" not in out_label or "not_used" in out_label, out_label)
    _assert_machine_time(out_label, "drug label")
    assert_no_forbidden_dashes(out_label, "mocked label")

    def route_no_match(url: str):
        if "api.fda.gov" in url:
            return 404, b'{"error":{"code":"NOT_FOUND","message":"No matches found!"}}'
        if "dailymed.nlm.nih.gov" in url:
            return 200, b'{"data":[]}'
        raise AssertionError(url)

    with patched_urlopen(route_no_match):
        out_zero = await fda.lookup_drug_label("notadrugzzzz")
    check("status: no_match" in out_zero, out_zero)
    check("match_count: 0" in out_zero, out_zero)
    check("No FDA label or DailyMed SPL records found" not in out_zero, out_zero)
    check("error_type:" not in out_zero, out_zero)
    _assert_machine_time(out_zero, "drug no-match")

    def route_down(_url: str):
        raise TimeoutError("timed out")

    with patched_urlopen(route_down):
        out_down = await fda.lookup_drug_label("apixaban")
    check("status: error" in out_down, out_down)
    check("error_type: timeout" in out_down, out_down)
    check("No FDA label or DailyMed SPL records found" not in out_down, out_down)
    check("not a finding of zero matching records" in out_down, out_down)
    check(OPENFDA_KEY_SENTINEL not in out_down, out_down)
    _assert_machine_time(out_down, "drug timeout")

    def route_limited(url: str):
        if "api.fda.gov" in url or "dailymed.nlm.nih.gov" in url:
            return 429, b'{"error":{"code":"TOO_MANY_REQUESTS"}}'
        raise AssertionError(url)

    with patched_urlopen(route_limited):
        out_limited = await fda.lookup_drug_label("apixaban")
    check("status: error" in out_limited and "error_type: http_error" in out_limited and "http_status=429" in out_limited, out_limited)
    check("status: no_match" not in out_limited, out_limited)
    check("No FDA label or DailyMed SPL records found" not in out_limited, out_limited)
    check("not a finding of zero matching records" in out_limited, out_limited)

    filler = "General administration guidance. " * 80
    long_label = {
        "results": [
            {
                "set_id": "set-long",
                "effective_time": "20240101",
                "boxed_warning": ["y" * 2500 + " BOXED WARNING SENTINEL: fatal bleeding."],
                "contraindications": ["x" * 2500 + " CONTRAINDICATION SENTINEL: active bleeding."],
                "dosage_and_administration": [filler + "RENAL DOSING SENTINEL: reduce dose when CrCl is low."],
                "use_in_specific_populations": [filler + "HEPATIC DOSING SENTINEL: avoid in Child-Pugh C."],
                "indications_and_usage": ["Indication."],
                "openfda": {"generic_name": ["APIXABAN"], "substance_name": ["APIXABAN"]},
            }
        ]
    }

    def route_long(url: str):
        if "api.fda.gov" in url:
            return 200, _json_bytes(long_label)
        if "dailymed.nlm.nih.gov" in url:
            return 200, b'{"data":[]}'
        raise AssertionError(url)

    with patched_urlopen(route_long):
        out_long = await fda.lookup_drug_label("apixaban", include_dailymed_spls=False)
    check("RENAL DOSING SENTINEL" in out_long, out_long[-800:])
    check("HEPATIC DOSING SENTINEL" in out_long, out_long[-800:])
    check("BOXED WARNING SENTINEL" in out_long, out_long[:1200])
    check("CONTRAINDICATION SENTINEL" in out_long, out_long)
    check("truncation:" in out_long, out_long)
    assert_no_forbidden_dashes(out_long, "truncated label")

    def route_partial(url: str):
        if "api.fda.gov" in url:
            return 200, _json_bytes(label)
        if "dailymed.nlm.nih.gov" in url:
            raise TimeoutError("timed out")
        raise AssertionError(url)

    with patched_urlopen(route_partial):
        out_partial = await fda.lookup_drug_label("apixaban")
    check("status: partial" in out_partial, out_partial)
    check("APIXABAN" in out_partial.upper(), out_partial)
    check("error_type: timeout" in out_partial, out_partial)
    check("not a finding of zero matching records" in out_partial, out_partial)
    _assert_machine_time(out_partial, "drug partial")

    def route_malformed(url: str):
        if "api.fda.gov" in url:
            return 200, b"not-json"
        if "dailymed.nlm.nih.gov" in url:
            return 503, b""
        raise AssertionError(url)

    with patched_urlopen(route_malformed):
        out_bad = await fda.lookup_drug_label("apixaban")
    check("status: error" in out_bad, out_bad)
    check("error_type: malformed_response" in out_bad, out_bad)
    check("error_type: http_error" in out_bad, out_bad)
    check("No FDA label or DailyMed SPL records found" not in out_bad, out_bad)
    check("not a finding of zero matching records" in out_bad, out_bad)

    out_blank = await fda.lookup_drug_label("  ")
    check("status: invalid_input" in out_blank, out_blank)
    check("match_count: 0" not in out_blank, out_blank)

    rx = RxNormTools()

    def route_rx_miss(url: str):
        if url.endswith("/rxcui.json") or "/rxcui.json?" in url:
            return 200, b'{"idGroup":{}}'
        if "approximateTerm.json" in url:
            return 200, b'{"approximateGroup":{"candidate":[]}}'
        raise AssertionError(url)

    with patched_urlopen(route_rx_miss):
        out_rx_miss = await rx.lookup_rxnorm_drug("notadrugzzzz")
    check("status: no_match" in out_rx_miss and "match_count: 0" in out_rx_miss, out_rx_miss)
    check("No RxNorm concept found" not in out_rx_miss, out_rx_miss)
    _assert_machine_time(out_rx_miss, "rxnorm miss")

    with patched_urlopen(route_down):
        out_rx_down = await rx.lookup_rxnorm_drug("apixaban")
    check("status: error" in out_rx_down and "error_type: timeout" in out_rx_down, out_rx_down)
    check("No RxNorm concept found" not in out_rx_down, out_rx_down)
    check("not a finding of zero matching records" in out_rx_down, out_rx_down)

    def route_rx_partial(url: str):
        if "/rxcui.json" in url:
            return 200, b'{"idGroup":{"rxnormId":["1364430"]}}'
        if "approximateTerm.json" in url:
            return 200, b'{"approximateGroup":{"candidate":[{"rxcui":"1364430","name":"Eliquis","score":"90"}]}}'
        if url.rstrip("/").endswith("/properties.json") or "/properties.json" in url:
            return 200, b'{"properties":{"name":"Eliquis","tty":"BN","synonym":"apixaban"}}'
        if "allrelated.json" in url:
            raise TimeoutError("timed out")
        raise AssertionError(url)

    with patched_urlopen(route_rx_partial):
        out_rx_partial = await rx.lookup_rxnorm_drug("Eliquis")
    check("status: partial" in out_rx_partial, out_rx_partial)
    check("1364430" in out_rx_partial, out_rx_partial)
    check("None listed" not in out_rx_partial, out_rx_partial)
    check("not a finding that none exist" in out_rx_partial, out_rx_partial)
    _assert_machine_time(out_rx_partial, "rxnorm partial")

    with patched_urlopen(route_down):
        out_ddi_down = await rx.check_fda_label_interactions(["apixaban", "clarithromycin"])
    check("status: error" in out_ddi_down, out_ddi_down)
    check("No direct literal name match" not in out_ddi_down, out_ddi_down)
    check("No drug_interactions section found" not in out_ddi_down, out_ddi_down)
    check("not a finding of zero matching records" in out_ddi_down, out_ddi_down)

    def route_ddi_empty(url: str):
        if "/rxcui.json" in url:
            return 200, b'{"idGroup":{"rxnormId":["1"]}}'
        if "/related.json" in url:
            return 200, b'{"relatedGroup":{"conceptGroup":[{"tty":"IN","conceptProperties":[{"rxcui":"1","name":"apixaban"}]}]}}'
        if "api.fda.gov" in url:
            return 200, _json_bytes({"results": [{"set_id": "s", "drug_interactions": ["Avoid strong CYP3A4 inhibitors."]}]})
        raise AssertionError(url)

    with patched_urlopen(route_ddi_empty):
        out_ddi_zero = await rx.check_fda_label_interactions(["apixaban", "clarithromycin"])
    check("status: ok" in out_ddi_zero, out_ddi_zero)
    check("pairwise_literal_match_count: 0" in out_ddi_zero, out_ddi_zero)
    check("labels were retrieved" in out_ddi_zero, out_ddi_zero)
    _assert_machine_time(out_ddi_zero, "ddi zero")

    ct = ClinicalTrialsTools()
    with patched_urlopen(lambda url: (200, b'{"studies":[],"totalCount":0}')):
        out_ct_zero = await ct.search_clinical_trials(condition="notaconditionzzzz")
    check("status: no_match" in out_ct_zero and "match_count: 0" in out_ct_zero, out_ct_zero)
    check("No trials found" not in out_ct_zero, out_ct_zero)
    _assert_machine_time(out_ct_zero, "trials zero")

    with patched_urlopen(route_down):
        out_ct_down = await ct.search_clinical_trials(condition="heart failure")
    check("status: error" in out_ct_down and "error_type: timeout" in out_ct_down, out_ct_down)
    check("No trials found" not in out_ct_down, out_ct_down)
    check("not a finding of zero matching records" in out_ct_down, out_ct_down)

    with patched_urlopen(lambda url: (200, b"not-json")):
        out_ct_bad = await ct.search_clinical_trials(condition="heart failure")
    check("status: error" in out_ct_bad and "error_type: malformed_response" in out_ct_bad, out_ct_bad)
    check("No trials found" not in out_ct_bad, out_ct_bad)

    study = {
        "studies": [
            {
                "hasResults": False,
                "protocolSection": {
                    "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Example Trial"},
                    "statusModule": {
                        "overallStatus": "RECRUITING",
                        "lastUpdatePostDateStruct": {"date": "2024-01-15"},
                    },
                    "designModule": {"phases": ["PHASE3"], "studyType": "INTERVENTIONAL", "enrollmentInfo": {"count": 10}},
                    "conditionsModule": {"conditions": ["Example"]},
                    "armsInterventionsModule": {"interventions": []},
                    "outcomesModule": {"primaryOutcomes": []},
                    "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example"}},
                },
            }
        ],
        "totalCount": 1,
    }
    with patched_urlopen(lambda url: (200, _json_bytes(study))):
        out_ct_hit = await ct.search_clinical_trials(condition="Example")
    check("status: ok" in out_ct_hit and "NCT00000001" in out_ct_hit, out_ct_hit)
    check("source_content_date: 2024-01-15" in out_ct_hit, out_ct_hit)
    check(f"retrieved_at_utc: {FROZEN_UTC}" in out_ct_hit, out_ct_hit)
    check("2024-01-15" != FROZEN_UTC, "fixture dates must differ from retrieval time")

    out_nct_bad = await ct.get_trial_by_nct_id("ABC123")
    check("status: invalid_input" in out_nct_bad, out_nct_bad)

    with patched_urlopen(lambda url: (404, b'{"message":"not found"}')):
        out_nct_miss = await ct.get_trial_by_nct_id("NCT99999999")
    check("status: no_match" in out_nct_miss and "match_count: 0" in out_nct_miss, out_nct_miss)
    check("status: error" not in out_nct_miss, out_nct_miss)

    with patched_urlopen(route_down):
        out_nct_down = await ct.get_trial_by_nct_id("NCT03819153")
    check("status: error" in out_nct_down and "error_type: timeout" in out_nct_down, out_nct_down)
    check("No ClinicalTrials.gov study found" not in out_nct_down, out_nct_down)
    check("not a finding of zero matching records" in out_nct_down, out_nct_down)

    icd = Icd10Tools()
    with patched_urlopen(lambda url: (200, b'[0,[],null,[]]')):
        out_icd_zero = await icd.search_icd10cm("notadiagnosiszzzz")
    check("status: no_match" in out_icd_zero and "match_count: 0" in out_icd_zero, out_icd_zero)
    check("No ICD-10-CM codes found" not in out_icd_zero, out_icd_zero)
    check("source_content_date: not_provided_by_response" in out_icd_zero, out_icd_zero)
    _assert_machine_time(out_icd_zero, "icd zero")

    with patched_urlopen(route_down):
        out_icd_down = await icd.search_icd10cm("heart failure")
    check("status: error" in out_icd_down and "error_type: timeout" in out_icd_down, out_icd_down)
    check("No ICD-10-CM codes found" not in out_icd_down, out_icd_down)
    check("not a finding of zero matching records" in out_icd_down, out_icd_down)

    with patched_urlopen(lambda url: (200, b'{"unexpected": true}')):
        out_icd_bad = await icd.search_icd10cm("I50")
    check("status: error" in out_icd_bad and "error_type: malformed_response" in out_icd_bad, out_icd_bad)
    check("No ICD-10-CM codes found" not in out_icd_bad, out_icd_bad)

    with patched_urlopen(lambda url: (200, b'[1,["I50.2"],null,[["I50.2","Chronic systolic (congestive) heart failure"]]]')):
        out_icd_hit = await icd.search_icd10cm("I50.2")
    check("status: ok" in out_icd_hit and "I50.2" in out_icd_hit, out_icd_hit)
    check(f"retrieved_at_utc: {FROZEN_UTC}" in out_icd_hit, out_icd_hit)
    check("provenance: live_http" in out_icd_hit, out_icd_hit)

    out_icd_blank = await icd.search_icd10cm(" ")
    check("status: invalid_input" in out_icd_blank, out_icd_blank)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "alpha.md").write_text("alpha only\n", encoding="utf-8")
        (tmp_path / "zeta.md").write_text("uniquezeta term\n", encoding="utf-8")
        lib = LocalLibraryTools()
        lib.valves.CLINICAL_LIBRARY_DIR = str(tmp_path)
        out_star = await lib.search_local_clinical_library("*")
        check("status: ok" in out_star, out_star)
        check("provenance: local_filesystem" in out_star, out_star)
        check("not a live federal or NCBI lookup" in out_star, out_star)
        check(f"searched_at_utc: {FROZEN_UTC}" in out_star, out_star)
        out_hit = await lib.search_local_clinical_library("uniquezeta")
        check("status: ok" in out_hit and "uniquezeta term" in out_hit, out_hit)
        out_lib_miss = await lib.search_local_clinical_library("absent-phrase")
        check("status: no_match" in out_lib_miss and "match_count: 0" in out_lib_miss, out_lib_miss)
        check("provenance: local_filesystem" in out_lib_miss, out_lib_miss)
        lib.valves.MAX_FILES_SCANNED = 1
        out_capped = await lib.search_local_clinical_library("uniquezeta")
        check("status: partial" in out_capped, out_capped)
        check("No matches found" not in out_capped, out_capped)
        check("scan_capped: true" in out_capped, out_capped)

    lib_missing = LocalLibraryTools()
    lib_missing.valves.CLINICAL_LIBRARY_DIR = "/tmp/clinical-library-does-not-exist-aee9"
    out_missing = await lib_missing.search_local_clinical_library("kdigo")
    check("status: error" in out_missing and "error_type: configuration" in out_missing, out_missing)
    check("No matches found" not in out_missing, out_missing)
    check("not a zero-match result" in out_missing, out_missing)

    out_lib_blank = await lib_missing.search_local_clinical_library("  ")
    check("status: invalid_input" in out_lib_blank, out_lib_blank)
    check("match_count: 0" not in out_lib_blank, out_lib_blank)

    original_read = Path.read_text

    def _boom(self, *args, **kwargs):
        raise OSError("unreadable")

    Path.read_text = _boom
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "guide.md").write_text("secret term\n", encoding="utf-8")
            lib = LocalLibraryTools()
            lib.valves.CLINICAL_LIBRARY_DIR = str(tmp_path)
            out_unreadable = await lib.search_local_clinical_library("secret")
    finally:
        Path.read_text = original_read
    check("status: partial" in out_unreadable, out_unreadable)
    check("No matches found" not in out_unreadable, out_unreadable)
    check("files_unreadable: 1" in out_unreadable, out_unreadable)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "guide.md").write_text("kept term\n", encoding="utf-8")
        (tmp_path / "broken.md").symlink_to(tmp_path / "missing.md")
        (tmp_path / "loop").symlink_to(tmp_path)
        lib = LocalLibraryTools()
        lib.valves.CLINICAL_LIBRARY_DIR = str(tmp_path)
        out_link = await lib.search_local_clinical_library("kept")
    check("kept term" in out_link, out_link)
    check("files_skipped_symlink:" in out_link, out_link)
    check("status: partial" in out_link or "status: ok" in out_link, out_link)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "slow.md").write_text("alpha\n", encoding="utf-8")
        lib = LocalLibraryTools()
        lib.valves.CLINICAL_LIBRARY_DIR = str(tmp_path)
        lib.valves.TIMEOUT_SECONDS = 0
        out_time = await lib.search_local_clinical_library("alpha")
    check("time_capped: true" in out_time, out_time)
    check("status: partial" in out_time, out_time)
    check("No matches found" not in out_time, out_time)

    order = []

    def _slow_search(self, query, filename_filter="", max_matches=15):
        import time as _time

        _time.sleep(0.25)
        order.append("search")
        return "slept"

    original_search = LocalLibraryTools._search_local_clinical_library_sync
    LocalLibraryTools._search_local_clinical_library_sync = _slow_search
    try:
        lib = LocalLibraryTools()

        async def _tick():
            await asyncio.sleep(0.05)
            order.append("tick")

        await asyncio.gather(lib.search_local_clinical_library("x"), _tick())
    finally:
        LocalLibraryTools._search_local_clinical_library_sync = original_search
    check(order[:1] == ["tick"], order)

    print(f"  [PASS] {checks} deterministic checks passed.")
    return checks


def assert_no_forbidden_dashes(text: str, label: str) -> None:
    assert "\u2014" not in text, f"Em dash found in {label}!"
    assert "\u2013" not in text, f"En dash found in {label}!"


async def run_all_tests() -> None:
    await run_deterministic_tests()
    import verify_stage_queue

    verify_stage_queue.run_stage_queue_tests()

    print("=== 1. Testing Category 1: openFDA + DailyMed Drug Label Lookup ===")
    fda_tool = OpenFdaDailyMedTools()
    out_fda = await fda_tool.lookup_drug_label("apixaban", prefer_monotherapy=True, include_dailymed_spls=True)
    assert "APIXABAN" in out_fda.upper(), "Expected APIXABAN in openFDA output"
    assert "Boxed Warning" in out_fda, "Expected Boxed Warning section"
    assert "dailymed.nlm.nih.gov/dailymed/services/v2/spls.json" in out_fda, "Expected DailyMed v2 URL"
    assert "status: ok" in out_fda and "provenance: live_http" in out_fda and "retrieved_at_utc: " in out_fda
    assert FROZEN_UTC not in out_fda, "Live lookup reused the deterministic test clock"
    assert_no_forbidden_dashes(out_fda, "openFDA/DailyMed output")
    print("  [PASS] openFDA + DailyMed live lookup for 'apixaban' succeeded.")

    print("=== 2. Testing Category 2: NLM RxNorm Lookup + FDA Interaction Cross-Check ===")
    rx_tool = RxNormTools()
    out_rx = await rx_tool.lookup_rxnorm_drug("Eliquis", include_dose_forms=True)
    assert "1364430" in out_rx or "apixaban" in out_rx.lower(), "Expected Eliquis/apixaban RxCUI mapping"
    assert "status: ok" in out_rx and "retrieved_at_utc: " in out_rx and FROZEN_UTC not in out_rx
    assert_no_forbidden_dashes(out_rx, "RxNorm lookup output")
    print("  [PASS] NLM RxNorm live lookup for 'Eliquis' succeeded.")

    out_ddi = await rx_tool.check_fda_label_interactions(["apixaban", "clarithromycin"])
    assert "clarithromycin" in out_ddi.lower(), "Expected clarithromycin mention in apixaban FDA interaction check"
    assert_no_forbidden_dashes(out_ddi, "RxNorm + FDA DDI output")
    print("  [PASS] RxNorm + openFDA interaction cross-check for ['apixaban', 'clarithromycin'] succeeded.")

    print("=== 3. Testing Category 3: ClinicalTrials.gov REST API v2 Search & NCT Lookup ===")
    ct_tool = ClinicalTrialsTools()
    out_ct_search = await ct_tool.search_clinical_trials(
        condition="chronic kidney disease",
        intervention="semaglutide",
        phase="PHASE3",
        max_results=5,
    )
    assert "clinicaltrials.gov/api/v2/studies" in out_ct_search, "Expected v2 API URL"
    assert "NCT" in out_ct_search, "Expected NCT IDs in search output"
    assert "status: ok" in out_ct_search and "retrieved_at_utc: " in out_ct_search and FROZEN_UTC not in out_ct_search
    assert_no_forbidden_dashes(out_ct_search, "ClinicalTrials search output")
    print("  [PASS] ClinicalTrials.gov v2 search for CKD + semaglutide succeeded.")

    out_ct_nct = await ct_tool.get_trial_by_nct_id("NCT03819153")
    assert "NCT03819153" in out_ct_nct, "Expected NCT03819153 (FLOW trial)"
    assert "status: ok" in out_ct_nct and "retrieved_at_utc: " in out_ct_nct and FROZEN_UTC not in out_ct_nct
    assert_no_forbidden_dashes(out_ct_nct, "ClinicalTrials NCT lookup output")
    print("  [PASS] ClinicalTrials.gov v2 single-study lookup for NCT03819153 (FLOW trial) succeeded.")

    print("=== 4. Testing Category 4: Local Clinical Library Path-Jailed Search ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "KDIGO_2024_CKD_Summary.md").write_text(
            "# KDIGO 2024 CKD Guideline\nStart SGLT2 inhibitor when eGFR >= 20 mL/min/1.73m2.\n",
            encoding="utf-8",
        )
        lib_tool = LocalLibraryTools()
        lib_tool.valves.CLINICAL_LIBRARY_DIR = str(tmp_path)
        out_lib_list = await lib_tool.search_local_clinical_library("*")
        assert "KDIGO_2024_CKD_Summary.md" in out_lib_list
        assert "status: ok" in out_lib_list and "provenance: local_filesystem" in out_lib_list
        assert "searched_at_utc: " in out_lib_list and FROZEN_UTC not in out_lib_list
        out_lib_hit = await lib_tool.search_local_clinical_library("SGLT2 inhibitor")
        assert "eGFR >= 20" in out_lib_hit
        assert_no_forbidden_dashes(out_lib_hit, "Local library search output")
    print("  [PASS] Local Clinical Library path-jailed search succeeded.")

    print("=== 5. Testing Category 5: Deterministic Clinical Calculators ===")
    calc = ClinicalCalculatorTools()

    # 5a. Cockcroft-Gault CrCl: 65yo F, 70 kg, SCr 1.2 mg/dL, height 165 cm
    # Expected Actual CrCl = ((140 - 65) * 70) / (72 * 1.2) * 0.85 = (5250 / 86.4) * 0.85 = 51.649 -> 51.6 mL/min
    out_cg = calc.calculate_cockcroft_gault_crcl(65, 70.0, 1.2, "F", height_cm=165.0)
    assert "**51.6 mL/min**" in out_cg, f"Unexpected CG CrCl output:\n{out_cg}"
    assert "provenance: local_deterministic_calculation" in out_cg and "computed_at_utc: " in out_cg
    assert FROZEN_UTC not in out_cg and "ncbi_eutilities: not_used" in out_cg
    assert_no_forbidden_dashes(out_cg, "Cockcroft-Gault output")
    print("  [PASS] Cockcroft-Gault CrCl (Actual, IBW, AdjBW) verified: 51.6 mL/min.")

    # 5b. CKD-EPI 2021 Race-Free eGFR: 60yo M, SCr 1.5 mg/dL
    # Expected = 142 * (1.5/0.9)^(-1.200) * (0.9938^60) = 142 * 0.541759 * 0.688555 = 52.97 -> 53.0 mL/min/1.73m2 (G3a)
    out_egfr = calc.calculate_ckd_epi_2021_egfr(60, 1.5, "M", height_cm=178.0, weight_kg=82.0)
    assert "**53.0 mL/min/1.73m2**" in out_egfr, f"Unexpected CKD-EPI 2021 output:\n{out_egfr}"
    assert "G3a" in out_egfr
    assert_no_forbidden_dashes(out_egfr, "CKD-EPI 2021 output")
    print("  [PASS] CKD-EPI 2021 Race-Free eGFR verified: 53.0 mL/min/1.73m2 (Stage G3a).")

    # 5c. CHA2DS2-VASc: 76yo F (+2 age, +1 sex), HTN (+1), DM (+1) -> Total = 5, Non-sex = 4
    out_cha = calc.calculate_cha2ds2_vasc(
        age_years=76,
        sex="F",
        congestive_heart_failure=False,
        hypertension=True,
        diabetes_mellitus=True,
        prior_stroke_tia_or_thromboembolism=False,
        vascular_disease_mi_pad_or_aortic_plaque=False,
    )
    assert "**5 / 9 points**" in out_cha and "**4 / 8 points**" in out_cha
    assert_no_forbidden_dashes(out_cha, "CHA2DS2-VASc output")
    print("  [PASS] CHA2DS2-VASc verified: 5/9 total, 4/8 non-sex.")

    # 5d. Wells DVT: active cancer (+1), calf swelling (+1), pitting edema (+1), alt dx (-2) -> Total = +1
    out_dvt = calc.calculate_wells_dvt(
        active_cancer=True,
        calf_swelling_gt_3cm_vs_asymptomatic_side=True,
        pitting_edema_confined_to_symptomatic_leg=True,
        alternative_diagnosis_at_least_as_likely_as_dvt=True,
    )
    assert "**1 points**" in out_dvt and "DVT Unlikely" in out_dvt and "Moderate Pretest Probability" in out_dvt
    assert_no_forbidden_dashes(out_dvt, "Wells DVT output")
    print("  [PASS] Wells DVT 3-tier and 2-tier stratification verified.")

    # 5e. Wells PE: signs of DVT (+3.0), HR > 100 (+1.5), hemoptysis (+1.0) -> Total = 5.5
    out_pe = calc.calculate_wells_pe(
        clinical_signs_and_symptoms_of_dvt=True,
        heart_rate_gt_100_bpm=True,
        hemoptysis=True,
    )
    assert "**5.5 points**" in out_pe and "PE Likely" in out_pe and "Moderate Pretest Probability" in out_pe
    assert_no_forbidden_dashes(out_pe, "Wells PE output")
    print("  [PASS] Wells PE 3-tier and 2-tier stratification verified.")

    # 5f. MELD(i), MELD-Na, and MELD 3.0: Bili 3.2, INR 1.8, Cr 2.1, Na 128, Sex F, Albumin 2.6
    out_meld = calc.calculate_meld_scores(
        bilirubin_mg_dl=3.2,
        inr=1.8,
        creatinine_mg_dl=2.1,
        sodium_meq_l=128.0,
        sex="F",
        albumin_g_dl=2.6,
        dialysis_twice_in_past_week=False,
    )
    assert "MELD(i)" in out_meld and "MELD-Na" in out_meld and "MELD 3.0" in out_meld
    assert "**25**" in out_meld and "**29**" in out_meld and "**32**" in out_meld
    assert "MELD-Na mortality percentage: unavailable" in out_meld
    assert "MELD 3.0 mortality percentage: unavailable" in out_meld
    assert "52.6%" not in out_meld
    assert_no_forbidden_dashes(out_meld, "MELD output")
    print("  [PASS] MELD(i), MELD-Na, and MELD 3.0 verified.")

    print("=== 6. Testing Category 6: NLM ICD-10-CM Official Lookup ===")
    icd_tool = Icd10Tools()
    out_icd_term = await icd_tool.search_icd10cm("systolic heart failure", search_mode="both", max_results=10)
    assert "I50.2" in out_icd_term, f"Expected I50.2 in ICD-10-CM output:\n{out_icd_term}"
    assert "status: ok" in out_icd_term and "retrieved_at_utc: " in out_icd_term and FROZEN_UTC not in out_icd_term
    assert_no_forbidden_dashes(out_icd_term, "ICD-10-CM term output")

    out_icd_code = await icd_tool.search_icd10cm("E11.22", search_mode="both", max_results=5)
    assert "E11.22" in out_icd_code and "kidney" in out_icd_code.lower()
    assert_no_forbidden_dashes(out_icd_code, "ICD-10-CM code output")
    print("  [PASS] NLM ICD-10-CM live search by term ('systolic heart failure') and code ('E11.22') succeeded.")

    print("\nALL 6 CATEGORIES PASSED LIVE API AND DETERMINISTIC UNIT VERIFICATION.")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
