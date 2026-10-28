"""
title: Deterministic Clinical Calculators (CrCl, CKD-EPI 2021 eGFR, CHA2DS2-VASc, Wells DVT/PE, MELD-Na / MELD 3.0)
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Zero-hallucination, deterministic Python implementations of Cockcroft-Gault CrCl, CKD-EPI 2021 race-free eGFR, CHA2DS2-VASc, Wells Score for DVT, Wells Score for PE, and MELD / MELD-Na / MELD 3.0 with primary literature citations (PMIDs) and step-by-step breakdowns.
required_open_webui_version: 0.4.0
requirements: pydantic
version: 1.0.0
license: MIT
"""

import math
from typing import Optional
try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def Field(default=None, **kwargs):  # type: ignore
        return default


def _norm_sex(sex: str) -> str:
    s = (sex or "").strip().upper()
    if s in ("F", "FEMALE", "WOMAN", "W"):
        return "F"
    if s in ("M", "MALE", "MAN"):
        return "M"
    raise ValueError("sex must be 'M' (male) or 'F' (female).")


class Tools:
    class Valves(BaseModel):
        INCLUDE_FORMULAS_AND_PMIDS: bool = Field(
            default=True,
            description="Include exact mathematical formulas and PubMed IDs (PMIDs) in every calculator output.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def calculate_cockcroft_gault_crcl(
        self,
        age_years: float,
        weight_kg: float,
        serum_creatinine_mg_dl: float,
        sex: str,
        height_cm: Optional[float] = None,
    ) -> str:
        """
        Calculate deterministic Cockcroft-Gault Creatinine Clearance (CrCl, mL/min) for medication renal dosing.
        If height_cm is provided, also computes Devine Ideal Body Weight (IBW), Adjusted Body Weight (AdjBW = IBW + 0.4*(TBW - IBW)),
        and CrCl across all three weights (Actual, Ideal, and Adjusted) per Winter 2012 dosing guidance.

        :param age_years: Patient age in years (18 to 120).
        :param weight_kg: Actual body weight in kilograms (20 to 400).
        :param serum_creatinine_mg_dl: Serum creatinine in mg/dL (0.1 to 30.0).
        :param sex: Biological sex ('M' or 'F').
        :param height_cm: Optional patient height in centimeters (100 to 250) to compute IBW and Adjusted Body Weight CrCl.
        :return: Markdown report with exact CrCl values, weight adjustments, and PMID citations.
        """
        try:
            sex_norm = _norm_sex(sex)
        except ValueError as e:
            return f"Input Validation Error: {e}"

        if not (18 <= age_years <= 120):
            return "Input Validation Error: age_years must be between 18 and 120."
        if not (20.0 <= weight_kg <= 400.0):
            return "Input Validation Error: weight_kg must be between 20 and 400 kg."
        if not (0.1 <= serum_creatinine_mg_dl <= 30.0):
            return "Input Validation Error: serum_creatinine_mg_dl must be between 0.1 and 30.0 mg/dL."

        sex_factor = 0.85 if sex_norm == "F" else 1.0
        crcl_actual = ((140.0 - age_years) * weight_kg) / (72.0 * serum_creatinine_mg_dl) * sex_factor

        lines = [
            "# Cockcroft-Gault Creatinine Clearance (CrCl)",
            "",
            "## 1. Inputs",
            f"- **Age:** {age_years:g} years | **Sex:** {sex_norm} (factor = {sex_factor})",
            f"- **Actual Body Weight (TBW):** {weight_kg:.1f} kg | **Serum Creatinine (SCr):** {serum_creatinine_mg_dl:.2f} mg/dL",
        ]

        if height_cm is not None:
            if not (100.0 <= height_cm <= 250.0):
                return "Input Validation Error: height_cm must be between 100 and 250 cm."
            height_in = height_cm / 2.54
            base_ibw = 45.5 if sex_norm == "F" else 50.0
            ibw_kg = max(20.0, base_ibw + 2.3 * (height_in - 60.0))
            adj_bw_kg = ibw_kg + 0.4 * (weight_kg - ibw_kg)
            bmi = weight_kg / ((height_cm / 100.0) ** 2)

            crcl_ibw = ((140.0 - age_years) * ibw_kg) / (72.0 * serum_creatinine_mg_dl) * sex_factor
            crcl_adj = ((140.0 - age_years) * adj_bw_kg) / (72.0 * serum_creatinine_mg_dl) * sex_factor

            if weight_kg < ibw_kg:
                recommended_label = f"Actual Body Weight (TBW < IBW): **{crcl_actual:.1f} mL/min**"
            elif weight_kg >= 1.2 * ibw_kg or bmi >= 30.0:
                recommended_label = f"Adjusted Body Weight (TBW >= 120% IBW or BMI >= 30): **{crcl_adj:.1f} mL/min**"
            else:
                recommended_label = f"Ideal Body Weight (Standard non-obese): **{crcl_ibw:.1f} mL/min** (Actual BW: {crcl_actual:.1f} mL/min)"

            lines.append(
                f"- **Height:** {height_cm:.1f} cm ({height_in:.1f} in) | **BMI:** {bmi:.1f} kg/m2"
            )
            lines.append(
                f"- **Devine Ideal Body Weight (IBW):** {ibw_kg:.1f} kg | **Adjusted Body Weight (AdjBW, 0.4 factor):** {adj_bw_kg:.1f} kg"
            )
            lines.append("")
            lines.append("## 2. Calculated Results")
            lines.append(f"- **CrCl (Actual Body Weight):** **{crcl_actual:.1f} mL/min**")
            lines.append(f"- **CrCl (Ideal Body Weight):** **{crcl_ibw:.1f} mL/min**")
            lines.append(f"- **CrCl (Adjusted Body Weight):** **{crcl_adj:.1f} mL/min**")
            lines.append(f"- **Winter (2012) Dosing Weight Selection:** {recommended_label}")
        else:
            lines.append("")
            lines.append("## 2. Calculated Result")
            lines.append(f"- **CrCl (Actual Body Weight):** **{crcl_actual:.1f} mL/min**")
            lines.append(
                "- *Tip: Pass `height_cm` to also compute Ideal Body Weight (IBW) and 40% Adjusted Body Weight (AdjBW) CrCl for overweight or obese patients.*"
            )

        if self.valves.INCLUDE_FORMULAS_AND_PMIDS:
            lines.extend(
                [
                    "",
                    "## 3. Formula and Primary Citations",
                    "- **Formula:** `CrCl = ((140 - Age) * Weight_kg) / (72 * SCr_mg_dL) * (0.85 if Female else 1.0)`",
                    "- **References:** Cockcroft DW, Gault MH. *Nephron*. 1976;16(1):31-41 (PMID: 1244564); Winter MA et al. *Pharmacotherapy*. 2012;32(7):604-612 (PMID: 22892266).",
                ]
            )
        return "\n".join(lines)

    def calculate_ckd_epi_2021_egfr(
        self,
        age_years: float,
        serum_creatinine_mg_dl: float,
        sex: str,
        height_cm: Optional[float] = None,
        weight_kg: Optional[float] = None,
    ) -> str:
        """
        Calculate deterministic 2021 CKD-EPI Creatinine Race-Free eGFR (mL/min/1.73m2) and KDIGO GFR category.
        If both height_cm and weight_kg are provided, also computes Mosteller Body Surface Area (BSA) and
        BSA-deindexed eGFR (mL/min) for drug dosing comparison.

        :param age_years: Patient age in years (18 to 120).
        :param serum_creatinine_mg_dl: Standardized serum creatinine in mg/dL (0.1 to 30.0).
        :param sex: Biological sex ('M' or 'F').
        :param height_cm: Optional height in cm (100 to 250) for BSA-deindexed eGFR (mL/min).
        :param weight_kg: Optional weight in kg (20 to 400) for BSA-deindexed eGFR (mL/min).
        :return: Markdown report with CKD-EPI 2021 eGFR, KDIGO stage (G1-G5), and PMID citations.
        """
        try:
            sex_norm = _norm_sex(sex)
        except ValueError as e:
            return f"Input Validation Error: {e}"

        if not (18 <= age_years <= 120):
            return "Input Validation Error: age_years must be between 18 and 120 (CKD-EPI 2021 is validated in adults >= 18)."
        if not (0.1 <= serum_creatinine_mg_dl <= 30.0):
            return "Input Validation Error: serum_creatinine_mg_dl must be between 0.1 and 30.0 mg/dL."

        if sex_norm == "F":
            kappa = 0.7
            alpha = -0.241
            sex_mult = 1.012
        else:
            kappa = 0.9
            alpha = -0.302
            sex_mult = 1.0

        ratio = serum_creatinine_mg_dl / kappa
        egfr = (
            142.0
            * (min(ratio, 1.0) ** alpha)
            * (max(ratio, 1.0) ** -1.200)
            * (0.9938 ** age_years)
            * sex_mult
        )

        if egfr >= 90.0:
            kdigo_stage = "G1 (Normal or high GFR, >= 90 mL/min/1.73m2; CKD only if markers of kidney damage present)"
        elif egfr >= 60.0:
            kdigo_stage = "G2 (Mildly decreased GFR, 60-89 mL/min/1.73m2; CKD only if markers of kidney damage present)"
        elif egfr >= 45.0:
            kdigo_stage = "G3a (Mildly to moderately decreased GFR, 45-59 mL/min/1.73m2)"
        elif egfr >= 30.0:
            kdigo_stage = "G3b (Moderately to severely decreased GFR, 30-44 mL/min/1.73m2)"
        elif egfr >= 15.0:
            kdigo_stage = "G4 (Severely decreased GFR, 15-29 mL/min/1.73m2; prepare for kidney replacement therapy / nephrology co-management)"
        else:
            kdigo_stage = "G5 (Kidney failure, < 15 mL/min/1.73m2)"

        lines = [
            "# CKD-EPI 2021 Race-Free Creatinine eGFR",
            "",
            "## 1. Inputs and Parameters",
            f"- **Age:** {age_years:g} years | **Sex:** {sex_norm} (`kappa = {kappa}`, `alpha = {alpha}`, `sex_factor = {sex_mult}`)",
            f"- **Serum Creatinine (SCr):** {serum_creatinine_mg_dl:.2f} mg/dL",
            "",
            "## 2. Calculated Result",
            f"- **eGFR (Indexed):** **{egfr:.1f} mL/min/1.73m2**",
            f"- **KDIGO 2024 GFR Category:** {kdigo_stage}",
        ]

        if height_cm is not None and weight_kg is not None:
            if 100.0 <= height_cm <= 250.0 and 20.0 <= weight_kg <= 400.0:
                bsa = math.sqrt((height_cm * weight_kg) / 3600.0)
                egfr_deindexed = egfr * (bsa / 1.73)
                lines.append(f"- **Mosteller BSA:** {bsa:.2f} m2")
                lines.append(
                    f"- **BSA-Deindexed eGFR (for drug dosing):** **{egfr_deindexed:.1f} mL/min** (`eGFR * BSA / 1.73`)"
                )

        if self.valves.INCLUDE_FORMULAS_AND_PMIDS:
            lines.extend(
                [
                    "",
                    "## 3. Formula and Primary Citations",
                    "- **Formula:** `eGFR = 142 * min(SCr/kappa, 1)^alpha * max(SCr/kappa, 1)^(-1.200) * 0.9938^Age * (1.012 if Female else 1.0)`",
                    "- **References:** Inker LA et al. *N Engl J Med*. 2021;385(19):1737-1749 (PMID: 34554658); KDIGO 2024 CKD Guideline *Kidney Int*. 2024;105(4S):S117-S314 (PMID: 38490803).",
                ]
            )
        return "\n".join(lines)

    def calculate_cha2ds2_vasc(
        self,
        age_years: float,
        sex: str,
        congestive_heart_failure: bool = False,
        hypertension: bool = False,
        diabetes_mellitus: bool = False,
        prior_stroke_tia_or_thromboembolism: bool = False,
        vascular_disease_mi_pad_or_aortic_plaque: bool = False,
    ) -> str:
        """
        Calculate deterministic CHA2DS2-VASc stroke risk score for non-valvular atrial fibrillation.

        :param age_years: Patient age in years (18 to 120).
        :param sex: Biological sex ('M' or 'F').
        :param congestive_heart_failure: Clinical HF, HFrEF, HFpEF, or moderate-to-severe LV systolic dysfunction (+1).
        :param hypertension: History of hypertension or on antihypertensive therapy (+1).
        :param diabetes_mellitus: Diabetes mellitus on treatment or fasting blood glucose >= 126 mg/dL (+1).
        :param prior_stroke_tia_or_thromboembolism: Prior ischemic stroke, TIA, or systemic thromboembolism (+2).
        :param vascular_disease_mi_pad_or_aortic_plaque: Prior myocardial infarction, peripheral artery disease, or complex aortic plaque (+1).
        :return: Markdown report with total CHA2DS2-VASc score, non-sex score, component breakdown, and 2023 ACC/AHA/HRS guideline guidance.
        """
        try:
            sex_norm = _norm_sex(sex)
        except ValueError as e:
            return f"Input Validation Error: {e}"

        if not (18 <= age_years <= 120):
            return "Input Validation Error: age_years must be between 18 and 120."

        if age_years >= 75:
            age_pts = 2
            age_label = "Age >= 75 years (+2)"
        elif age_years >= 65:
            age_pts = 1
            age_label = "Age 65 to 74 years (+1)"
        else:
            age_pts = 0
            age_label = "Age < 65 years (0)"

        chf_pts = 1 if congestive_heart_failure else 0
        htn_pts = 1 if hypertension else 0
        dm_pts = 1 if diabetes_mellitus else 0
        stroke_pts = 2 if prior_stroke_tia_or_thromboembolism else 0
        vasc_pts = 1 if vascular_disease_mi_pad_or_aortic_plaque else 0
        sex_pts = 1 if sex_norm == "F" else 0

        total_score = chf_pts + htn_pts + age_pts + dm_pts + stroke_pts + vasc_pts + sex_pts
        non_sex_score = total_score - sex_pts

        # Friberg et al. (Eur Heart J 2012, PMID 22246443) adjusted annual ischemic stroke rates (%)
        friberg_stroke_rate = {
            0: "0.2%",
            1: "0.6%",
            2: "2.2%",
            3: "3.2%",
            4: "4.8%",
            5: "7.2%",
            6: "9.7%",
            7: "11.2%",
            8: "10.8%",
            9: "12.2%",
        }.get(total_score, ">12%")

        if non_sex_score == 0:
            guidance = (
                "Low risk (0 non-sex risk factors; score 0 in males or 1 in females from sex alone): "
                "Oral anticoagulation (OAC) may be omitted (2023 ACC/AHA/HRS Class 2b/No Benefit)."
            )
        elif non_sex_score == 1:
            guidance = (
                "Intermediate risk (1 non-sex risk factor; score 1 in males or 2 in females): "
                "Oral anticoagulation (DOAC preferred over warfarin) is reasonable to reduce thromboembolic stroke risk (2023 ACC/AHA/HRS Class 2a)."
            )
        else:
            guidance = (
                "High risk (>= 2 non-sex risk factors; score >= 2 in males or >= 3 in females): "
                "Oral anticoagulation is recommended (DOAC preferred over warfarin except in mechanical heart valves or moderate-to-severe rheumatic mitral stenosis; 2023 ACC/AHA/HRS Class 1)."
            )

        lines = [
            "# CHA2DS2-VASc Score (Atrial Fibrillation Stroke Risk)",
            "",
            "## 1. Score Summary",
            f"- **Total CHA2DS2-VASc Score:** **{total_score} / 9 points**",
            f"- **Non-Sex Risk Score (CHA2DS2-VA):** **{non_sex_score} / 8 points**",
            f"- **Estimated Annual Ischemic Stroke Risk (untreated, Friberg 2012):** ~{friberg_stroke_rate}/year",
            f"- **2023 ACC/AHA/ACCP/HRS Guideline Interpretation:** {guidance}",
            "",
            "## 2. Point Breakdown",
            f"- **C** - Congestive heart failure / LV dysfunction: **+{chf_pts}**",
            f"- **H** - Hypertension: **+{htn_pts}**",
            f"- **A2** - {age_label}: **+{age_pts}** (Age: {age_years:g})",
            f"- **D** - Diabetes mellitus: **+{dm_pts}**",
            f"- **S2** - Prior Stroke / TIA / Thromboembolism: **+{stroke_pts}**",
            f"- **V** - Vascular disease (prior MI, PAD, or aortic plaque): **+{vasc_pts}**",
            f"- **Sc** - Sex category ({sex_norm}): **+{sex_pts}**",
        ]

        if self.valves.INCLUDE_FORMULAS_AND_PMIDS:
            lines.extend(
                [
                    "",
                    "## 3. Primary Citations",
                    "- **References:** Lip GY et al. *Chest*. 2010;137(2):263-272 (PMID: 19762550); Friberg L et al. *Eur Heart J*. 2012;33(12):1500-1510 (PMID: 22246443); Joglar JA et al. 2023 ACC/AHA/ACCP/HRS AF Guideline *Circulation*. 2024;149(1):e1-e156 (PMID: 38033089).",
                ]
            )
        return "\n".join(lines)

    def calculate_wells_dvt(
        self,
        active_cancer: bool = False,
        paralysis_paresis_or_plaster_cast: bool = False,
        bedridden_gt_3d_or_major_surgery_within_12w: bool = False,
        localized_tenderness_deep_vein_system: bool = False,
        entire_leg_swollen: bool = False,
        calf_swelling_gt_3cm_vs_asymptomatic_side: bool = False,
        pitting_edema_confined_to_symptomatic_leg: bool = False,
        collateral_superficial_veins_nonvaricose: bool = False,
        previously_documented_dvt: bool = False,
        alternative_diagnosis_at_least_as_likely_as_dvt: bool = False,
    ) -> str:
        """
        Calculate deterministic Wells Score for Deep Vein Thrombosis (DVT) with both 3-tier and 2-tier pretest probability stratifications.

        :param active_cancer: Active cancer (treatment ongoing, within 6 months, or palliative) (+1).
        :param paralysis_paresis_or_plaster_cast: Paralysis, paresis, or recent plaster immobilization of the lower extremities (+1).
        :param bedridden_gt_3d_or_major_surgery_within_12w: Recently bedridden > 3 days or major surgery within 12 weeks requiring general or regional anesthesia (+1).
        :param localized_tenderness_deep_vein_system: Localized tenderness along the distribution of the deep venous system (+1).
        :param entire_leg_swollen: Entire leg swollen (+1).
        :param calf_swelling_gt_3cm_vs_asymptomatic_side: Calf swelling > 3 cm larger than asymptomatic side (measured 10 cm below tibial tuberosity) (+1).
        :param pitting_edema_confined_to_symptomatic_leg: Pitting edema confined to the symptomatic leg (+1).
        :param collateral_superficial_veins_nonvaricose: Collateral superficial veins (nonvaricose) (+1).
        :param previously_documented_dvt: Previously documented DVT (+1).
        :param alternative_diagnosis_at_least_as_likely_as_dvt: Alternative diagnosis at least as likely as DVT (-2).
        :return: Markdown report with total Wells DVT score (-2 to +9), 3-tier and 2-tier interpretations, and diagnostic algorithm next steps.
        """
        items = [
            ("Active cancer (treatment within 6 mo or palliative)", 1 if active_cancer else 0),
            ("Paralysis, paresis, or recent lower-extremity plaster immobilization", 1 if paralysis_paresis_or_plaster_cast else 0),
            ("Bedridden > 3 days or major surgery within 12 weeks", 1 if bedridden_gt_3d_or_major_surgery_within_12w else 0),
            ("Localized tenderness along deep venous system", 1 if localized_tenderness_deep_vein_system else 0),
            ("Entire leg swollen", 1 if entire_leg_swollen else 0),
            ("Calf swelling > 3 cm vs. asymptomatic leg (10 cm below tibial tuberosity)", 1 if calf_swelling_gt_3cm_vs_asymptomatic_side else 0),
            ("Pitting edema confined to symptomatic leg", 1 if pitting_edema_confined_to_symptomatic_leg else 0),
            ("Collateral superficial veins (nonvaricose)", 1 if collateral_superficial_veins_nonvaricose else 0),
            ("Previously documented DVT", 1 if previously_documented_dvt else 0),
            ("Alternative diagnosis at least as likely as DVT", -2 if alternative_diagnosis_at_least_as_likely_as_dvt else 0),
        ]
        score = sum(v for _, v in items)

        if score <= 0:
            three_tier = f"Low Pretest Probability (score {score}; ~5% prevalence)"
        elif score <= 2:
            three_tier = f"Moderate Pretest Probability (score {score}; ~17% prevalence)"
        else:
            three_tier = f"High Pretest Probability (score {score}; ~53% prevalence)"

        if score <= 1:
            two_tier = (
                f"DVT Unlikely (score {score} <= 1): Order high-sensitivity D-dimer first; "
                "if negative, DVT is excluded without ultrasound. If positive, obtain proximal/whole-leg compression ultrasound."
            )
        else:
            two_tier = (
                f"DVT Likely (score {score} >= 2): Proceed directly to compression ultrasonography "
                "(and D-dimer per local serial-ultrasound protocol if initial proximal ultrasound is negative)."
            )

        lines = [
            "# Wells Criteria for Deep Vein Thrombosis (DVT)",
            "",
            "## 1. Score and Risk Stratification",
            f"- **Total Wells DVT Score:** **{score} points** (range: -2 to +9)",
            f"- **Three-Tier Model (Wells 1997):** {three_tier}",
            f"- **Two-Tier Model (Wells 2003 / ASH 2018):** {two_tier}",
            "",
            "## 2. Criterion Breakdown",
        ]
        for label, pts in items:
            sign = f"+{pts}" if pts > 0 else str(pts)
            lines.append(f"- {label}: **{sign}**")

        if self.valves.INCLUDE_FORMULAS_AND_PMIDS:
            lines.extend(
                [
                    "",
                    "## 3. Primary Citations",
                    "- **References:** Wells PS et al. *Lancet*. 1997;350(9094):1795-1798 (PMID: 9428249); Wells PS et al. *N Engl J Med*. 2003;349(13):1227-1235 (PMID: 14507948).",
                ]
            )
        return "\n".join(lines)

    def calculate_wells_pe(
        self,
        clinical_signs_and_symptoms_of_dvt: bool = False,
        pe_is_most_likely_or_equally_likely_diagnosis: bool = False,
        heart_rate_gt_100_bpm: bool = False,
        immobilization_ge_3d_or_surgery_in_past_4w: bool = False,
        previous_objectively_diagnosed_pe_or_dvt: bool = False,
        hemoptysis: bool = False,
        malignancy_treatment_within_6m_or_palliative: bool = False,
    ) -> str:
        """
        Calculate deterministic Wells Score for Pulmonary Embolism (PE) with both 3-tier and 2-tier pretest probability stratifications.

        :param clinical_signs_and_symptoms_of_dvt: Clinical signs and symptoms of DVT (minimum of leg swelling and pain with palpation of deep veins) (+3.0).
        :param pe_is_most_likely_or_equally_likely_diagnosis: An alternative diagnosis is less likely than PE (PE is #1 or equally likely diagnosis) (+3.0).
        :param heart_rate_gt_100_bpm: Heart rate > 100 beats per minute (+1.5).
        :param immobilization_ge_3d_or_surgery_in_past_4w: Immobilization for at least 3 consecutive days or surgery in the previous 4 weeks (+1.5).
        :param previous_objectively_diagnosed_pe_or_dvt: Previous, objectively diagnosed PE or DVT (+1.5).
        :param hemoptysis: Hemoptysis (+1.0).
        :param malignancy_treatment_within_6m_or_palliative: Malignancy (on treatment, treated in the last 6 months, or palliative) (+1.0).
        :return: Markdown report with total Wells PE score (0 to 12.5), 3-tier and 2-tier interpretations, and diagnostic algorithm next steps.
        """
        items = [
            ("Clinical signs and symptoms of DVT", 3.0 if clinical_signs_and_symptoms_of_dvt else 0.0),
            ("PE is #1 diagnosis or equally likely", 3.0 if pe_is_most_likely_or_equally_likely_diagnosis else 0.0),
            ("Heart rate > 100 bpm", 1.5 if heart_rate_gt_100_bpm else 0.0),
            ("Immobilization >= 3 days or surgery in previous 4 weeks", 1.5 if immobilization_ge_3d_or_surgery_in_past_4w else 0.0),
            ("Previous objectively diagnosed PE or DVT", 1.5 if previous_objectively_diagnosed_pe_or_dvt else 0.0),
            ("Hemoptysis", 1.0 if hemoptysis else 0.0),
            ("Malignancy (treatment within 6 mo or palliative)", 1.0 if malignancy_treatment_within_6m_or_palliative else 0.0),
        ]
        score = sum(v for _, v in items)

        if score < 2.0:
            three_tier = f"Low Pretest Probability (score {score:g} < 2.0): Consider PERC rule; if PERC positive, obtain high-sensitivity D-dimer (age-adjusted if age > 50)."
        elif score <= 6.0:
            three_tier = f"Moderate Pretest Probability (score {score:g}, 2.0 to 6.0): Obtain high-sensitivity D-dimer (age-adjusted if age > 50); if elevated, proceed to CTPA."
        else:
            three_tier = f"High Pretest Probability (score {score:g} > 6.0): Proceed directly to CT pulmonary angiography (CTPA); do not rely on D-dimer alone to exclude PE."

        if score <= 4.0:
            two_tier = f"PE Unlikely (score {score:g} <= 4.0): Obtain high-sensitivity D-dimer; if negative, PE is excluded. If positive, perform CTPA."
        else:
            two_tier = f"PE Likely (score {score:g} > 4.0): Proceed directly to diagnostic imaging (CTPA or V/Q scan)."

        lines = [
            "# Wells Criteria for Pulmonary Embolism (PE)",
            "",
            "## 1. Score and Risk Stratification",
            f"- **Total Wells PE Score:** **{score:g} points** (range: 0 to 12.5)",
            f"- **Three-Tier Model (Wells 2000):** {three_tier}",
            f"- **Two-Tier Model (Christopher Study 2006):** {two_tier}",
            "",
            "## 2. Criterion Breakdown",
        ]
        for label, pts in items:
            lines.append(f"- {label}: **+{pts:g}**")

        if self.valves.INCLUDE_FORMULAS_AND_PMIDS:
            lines.extend(
                [
                    "",
                    "## 3. Primary Citations",
                    "- **References:** Wells PS et al. *Thromb Haemost*. 2000;83(3):416-420 (PMID: 10744147); van Belle A et al. (Christopher Study) *JAMA*. 2006;295(2):172-179 (PMID: 16403929).",
                ]
            )
        return "\n".join(lines)

    def calculate_meld_scores(
        self,
        bilirubin_mg_dl: float,
        inr: float,
        creatinine_mg_dl: float,
        sodium_meq_l: float,
        sex: Optional[str] = None,
        albumin_g_dl: Optional[float] = None,
        dialysis_twice_in_past_week: bool = False,
    ) -> str:
        """
        Calculate deterministic MELD(i) (2016 base), MELD-Na (UNOS/OPTN 2016), and MELD 3.0 (Kim et al. 2021 / OPTN July 2023 standard)
        for end-stage liver disease severity and 90-day waitlist mortality estimation.

        :param bilirubin_mg_dl: Total bilirubin in mg/dL (0.1 to 60.0; values < 1.0 are clamped to 1.0 per OPTN policy).
        :param inr: International Normalized Ratio (0.5 to 20.0; values < 1.0 are clamped to 1.0 per OPTN policy).
        :param creatinine_mg_dl: Serum creatinine in mg/dL (0.1 to 25.0; clamped to [1.0, 4.0] for MELD-Na and [1.0, 3.0] for MELD 3.0).
        :param sodium_meq_l: Serum sodium in mEq/L (100.0 to 170.0; clamped to [125.0, 137.0] per OPTN policy).
        :param sex: Optional biological sex ('M' or 'F'), required along with albumin_g_dl to compute MELD 3.0.
        :param albumin_g_dl: Optional serum albumin in g/dL (0.5 to 7.0; clamped to [1.5, 3.5] for MELD 3.0).
        :param dialysis_twice_in_past_week: True if patient had >= 2 dialysis treatments in the prior 7 days or 24h of CVVHD (sets Cr = 4.0 for MELD-Na and Cr = 3.0 for MELD 3.0).
        :return: Markdown report with MELD(i), MELD-Na, and MELD 3.0 scores, clamping audit trail, and 90-day mortality estimates.
        """
        if not (0.1 <= bilirubin_mg_dl <= 60.0):
            return "Input Validation Error: bilirubin_mg_dl must be between 0.1 and 60.0 mg/dL."
        if not (0.5 <= inr <= 20.0):
            return "Input Validation Error: inr must be between 0.5 and 20.0."
        if not (0.1 <= creatinine_mg_dl <= 25.0):
            return "Input Validation Error: creatinine_mg_dl must be between 0.1 and 25.0 mg/dL."
        if not (100.0 <= sodium_meq_l <= 170.0):
            return "Input Validation Error: sodium_meq_l must be between 100 and 170 mEq/L."

        bili_clamped = max(1.0, float(bilirubin_mg_dl))
        inr_clamped = max(1.0, float(inr))
        na_clamped = min(137.0, max(125.0, float(sodium_meq_l)))

        if dialysis_twice_in_past_week:
            cr_meld_na = 4.0
            cr_meld_3 = 3.0
        else:
            cr_meld_na = min(4.0, max(1.0, float(creatinine_mg_dl)))
            cr_meld_3 = min(3.0, max(1.0, float(creatinine_mg_dl)))

        # 1. MELD(i) (UNOS 2016 base score)
        meld_i_unrounded = 10.0 * (
            0.957 * math.log(cr_meld_na)
            + 0.378 * math.log(bili_clamped)
            + 1.120 * math.log(inr_clamped)
            + 0.643
        )
        # Per OPTN policy, MELD(i) is rounded to the tenth decimal place before calculating MELD-Na, and rounded to integer for standalone MELD(i)
        meld_i_tenth = round(meld_i_unrounded, 1)
        meld_i_int = min(40, max(6, int(math.floor(meld_i_unrounded + 0.5))))

        # 2. MELD-Na (UNOS/OPTN 2016)
        if meld_i_tenth > 11.0:
            meld_na_raw = (
                meld_i_tenth
                + 1.32 * (137.0 - na_clamped)
                - (0.033 * meld_i_tenth * (137.0 - na_clamped))
            )
        else:
            meld_na_raw = meld_i_tenth
        meld_na_int = min(40, max(6, int(math.floor(meld_na_raw + 0.5))))

        # 3. MELD 3.0 (Kim et al., Gastroenterology 2021 / OPTN July 2023)
        meld_3_int: Optional[int] = None
        meld_3_raw: Optional[float] = None
        alb_clamped: Optional[float] = None
        sex_norm: Optional[str] = None
        if sex is not None and str(sex).strip() and albumin_g_dl is not None:
            try:
                sex_norm = _norm_sex(sex)
            except ValueError as e:
                return f"Input Validation Error: {e}"
            if not (0.5 <= albumin_g_dl <= 7.0):
                return "Input Validation Error: albumin_g_dl must be between 0.5 and 7.0 g/dL."
            alb_clamped = min(3.5, max(1.5, float(albumin_g_dl)))
            female_pts = 1.33 if sex_norm == "F" else 0.0
            meld_3_raw = (
                female_pts
                + 4.56 * math.log(bili_clamped)
                + 0.82 * (137.0 - na_clamped)
                - 0.24 * (137.0 - na_clamped) * math.log(bili_clamped)
                + 9.09 * math.log(inr_clamped)
                + 11.14 * math.log(cr_meld_3)
                + 1.85 * (3.5 - alb_clamped)
                - 1.83 * (3.5 - alb_clamped) * math.log(cr_meld_3)
                + 6.0
            )
            meld_3_int = min(40, max(6, int(math.floor(meld_3_raw + 0.5))))

        primary_score = meld_3_int if meld_3_int is not None else meld_na_int
        if primary_score <= 9:
            mortality_band = "~1.9% 90-day mortality (MELD <= 9)"
        elif primary_score <= 19:
            mortality_band = "~6.0% 90-day mortality (MELD 10-19)"
        elif primary_score <= 29:
            mortality_band = "~19.6% 90-day mortality (MELD 20-29)"
        elif primary_score <= 39:
            mortality_band = "~52.6% 90-day mortality (MELD 30-39)"
        else:
            mortality_band = "~71.3% 90-day mortality (MELD >= 40)"

        lines = [
            "# MELD(i), MELD-Na, and MELD 3.0 Liver Disease Severity Calculator",
            "",
            "## 1. Calculated Scores (Clamped to [6, 40])",
            f"- **MELD(i) (2016 Standard Base):** **{meld_i_int}** (unrounded: {meld_i_unrounded:.2f})",
            f"- **MELD-Na (UNOS/OPTN 2016):** **{meld_na_int}** (unrounded: {meld_na_raw:.2f})",
        ]
        if meld_3_int is not None and meld_3_raw is not None:
            lines.append(
                f"- **MELD 3.0 (OPTN July 2023 Standard):** **{meld_3_int}** (unrounded: {meld_3_raw:.2f})"
            )
        else:
            lines.append(
                "- **MELD 3.0 (OPTN July 2023 Standard):** *Pass both `sex` ('M'/'F') and `albumin_g_dl` to compute MELD 3.0.*"
            )
        lines.append(f"- **Estimated 90-Day Mortality (Wiesner 2003 reference band):** {mortality_band}")

        lines.extend(
            [
                "",
                "## 2. OPTN Parameter Clamping Audit Trail",
                f"- **Total Bilirubin:** input {bilirubin_mg_dl:.2f} mg/dL -> clamped to **{bili_clamped:.2f} mg/dL** (floor 1.0)",
                f"- **INR:** input {inr:.2f} -> clamped to **{inr_clamped:.2f}** (floor 1.0)",
                f"- **Serum Creatinine:** input {creatinine_mg_dl:.2f} mg/dL (Dialysis/CVVHD: {dialysis_twice_in_past_week}) -> MELD-Na Cr = **{cr_meld_na:.2f} mg/dL** (cap 4.0), MELD 3.0 Cr = **{cr_meld_3:.2f} mg/dL** (cap 3.0)",
                f"- **Serum Sodium:** input {sodium_meq_l:.1f} mEq/L -> clamped to **{na_clamped:.1f} mEq/L** (bounds 125-137)",
            ]
        )
        if alb_clamped is not None and sex_norm is not None:
            lines.append(
                f"- **Serum Albumin (MELD 3.0):** input {albumin_g_dl:.2f} g/dL -> clamped to **{alb_clamped:.2f} g/dL** (bounds 1.5-3.5) | **Sex:** {sex_norm} (+{1.33 if sex_norm == 'F' else 0.0} pts)"
            )

        if self.valves.INCLUDE_FORMULAS_AND_PMIDS:
            lines.extend(
                [
                    "",
                    "## 3. Formulas and Primary Citations",
                    "- **MELD(i):** `10 * (0.957 * ln(Cr) + 0.378 * ln(Bili) + 1.120 * ln(INR) + 0.643)`",
                    "- **MELD-Na:** `MELD(i) + 1.32 * (137 - Na) - 0.033 * MELD(i) * (137 - Na)` (when MELD(i) > 11)",
                    "- **MELD 3.0:** `1.33*(if Female) + 4.56*ln(Bili) + 0.82*(137 - Na) - 0.24*(137 - Na)*ln(Bili) + 9.09*ln(INR) + 11.14*ln(Cr) + 1.85*(3.5 - Alb) - 1.83*(3.5 - Alb)*ln(Cr) + 6`",
                    "- **References:** Kamath PS et al. *Hepatology*. 2001;33(2):464-470 (PMID: 11172350); Kim WR et al. *N Engl J Med*. 2008;359(10):1018-1026 (PMID: 18768945); Kim WR et al. *Gastroenterology*. 2021;161(6):1887-1895.e4 (PMID: 34481845).",
                ]
            )
        return "\n".join(lines)
