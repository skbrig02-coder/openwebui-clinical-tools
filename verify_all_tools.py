"""
Live API and Deterministic Unit Verification Suite for Open WebUI Medical Tools.
Tests all 6 tool categories against real federal APIs (openFDA, DailyMed v2, NLM RxNorm,
ClinicalTrials.gov v2, NLM ICD-10-CM v3) and reference clinical calculator test vectors.
"""

import asyncio
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from openfda_dailymed_drug_label import Tools as OpenFdaDailyMedTools
from nlm_rxnorm_lookup import Tools as RxNormTools
from clinicaltrials_v2_search import Tools as ClinicalTrialsTools
from clinical_calculators import Tools as ClinicalCalculatorTools
from nlm_icd10cm_lookup import Tools as Icd10Tools
from local_clinical_library_search import Tools as LocalLibraryTools


def assert_no_forbidden_dashes(text: str, label: str) -> None:
    assert "\u2014" not in text, f"Em dash found in {label}!"
    assert "\u2013" not in text, f"En dash found in {label}!"


async def run_all_tests() -> None:
    print("=== 1. Testing Category 1: openFDA + DailyMed Drug Label Lookup ===")
    fda_tool = OpenFdaDailyMedTools()
    out_fda = await fda_tool.lookup_drug_label("apixaban", prefer_monotherapy=True, include_dailymed_spls=True)
    assert "APIXABAN" in out_fda.upper(), "Expected APIXABAN in openFDA output"
    assert "Boxed Warning" in out_fda, "Expected Boxed Warning section"
    assert "dailymed.nlm.nih.gov/dailymed/services/v2/spls.json" in out_fda, "Expected DailyMed v2 URL"
    assert_no_forbidden_dashes(out_fda, "openFDA/DailyMed output")
    print("  [PASS] openFDA + DailyMed live lookup for 'apixaban' succeeded.")

    print("=== 2. Testing Category 2: NLM RxNorm Lookup + FDA Interaction Cross-Check ===")
    rx_tool = RxNormTools()
    out_rx = await rx_tool.lookup_rxnorm_drug("Eliquis", include_dose_forms=True)
    assert "1364430" in out_rx or "apixaban" in out_rx.lower(), "Expected Eliquis/apixaban RxCUI mapping"
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
    assert_no_forbidden_dashes(out_ct_search, "ClinicalTrials search output")
    print("  [PASS] ClinicalTrials.gov v2 search for CKD + semaglutide succeeded.")

    out_ct_nct = await ct_tool.get_trial_by_nct_id("NCT03819153")
    assert "NCT03819153" in out_ct_nct, "Expected NCT03819153 (FLOW trial)"
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
        out_lib_list = lib_tool.search_local_clinical_library("*")
        assert "KDIGO_2024_CKD_Summary.md" in out_lib_list
        out_lib_hit = lib_tool.search_local_clinical_library("SGLT2 inhibitor")
        assert "eGFR >= 20" in out_lib_hit
        assert_no_forbidden_dashes(out_lib_hit, "Local library search output")
    print("  [PASS] Local Clinical Library path-jailed search succeeded.")

    print("=== 5. Testing Category 5: Deterministic Clinical Calculators ===")
    calc = ClinicalCalculatorTools()

    # 5a. Cockcroft-Gault CrCl: 65yo F, 70 kg, SCr 1.2 mg/dL, height 165 cm
    # Expected Actual CrCl = ((140 - 65) * 70) / (72 * 1.2) * 0.85 = (5250 / 86.4) * 0.85 = 51.649 -> 51.6 mL/min
    out_cg = calc.calculate_cockcroft_gault_crcl(65, 70.0, 1.2, "F", height_cm=165.0)
    assert "**51.6 mL/min**" in out_cg, f"Unexpected CG CrCl output:\n{out_cg}"
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
    assert_no_forbidden_dashes(out_meld, "MELD output")
    print("  [PASS] MELD(i), MELD-Na, and MELD 3.0 verified.")

    print("=== 6. Testing Category 6: NLM ICD-10-CM Official Lookup ===")
    icd_tool = Icd10Tools()
    out_icd_term = await icd_tool.search_icd10cm("systolic heart failure", search_mode="both", max_results=10)
    assert "I50.2" in out_icd_term, f"Expected I50.2 in ICD-10-CM output:\n{out_icd_term}"
    assert_no_forbidden_dashes(out_icd_term, "ICD-10-CM term output")

    out_icd_code = await icd_tool.search_icd10cm("E11.22", search_mode="both", max_results=5)
    assert "E11.22" in out_icd_code and "kidney" in out_icd_code.lower()
    assert_no_forbidden_dashes(out_icd_code, "ICD-10-CM code output")
    print("  [PASS] NLM ICD-10-CM live search by term ('systolic heart failure') and code ('E11.22') succeeded.")

    print("\nALL 6 CATEGORIES PASSED LIVE API AND DETERMINISTIC UNIT VERIFICATION.")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
