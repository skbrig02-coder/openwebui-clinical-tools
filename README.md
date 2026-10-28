# Open WebUI Medical Tools Suite (Vetted and Pre-Built)

This directory (`/workspace/openwebui_medical_tools/`) contains the complete community vetting report and 6 self-contained, audit-verified Python tool files prepared for Stuart Brigham, MD's Open WebUI setup on his local RTX 3060 workstation.

## Contents

1. `VETTING_REPORT.md`: Detailed community audit across `openwebui.com/t/`, GitHub, and MCP packages for all 6 candidate medical tool categories, including security findings, retired federal API verifications, and exact verdicts.
2. `openfda_dailymed_drug_label.py` (**Category 1 - Custom Built**):
   - Queries official FDA Structured Product Labeling (SPL) via `https://api.fda.gov/drug/label.json` and NLM DailyMed REST API v2 (`https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json`).
   - Prefers single-ingredient monotherapy labels over combination products, highlights `boxed_warning`, and extracts indications, dosing, contraindications, warnings, adverse reactions, drug interactions, and specific-population dosing with context-safe truncation.
3. `nlm_rxnorm_lookup.py` (**Category 2 - Custom Built**):
   - Queries active NLM RxNorm REST endpoints (`https://rxnav.nlm.nih.gov/REST/rxcui.json`, `approximateTerm.json`, `properties.json`, `allrelated.json`, `related.json`) for RxCUI normalization, base/salt ingredients (`IN`/`PIN`/`MIN`), brand names (`BN`), dose forms (`DF`), and clinical drug strengths (`SCD`/`SBD`).
   - Avoids the retired NLM RxNav Drug Interaction API (discontinued January 2, 2024) and includes `check_fda_label_interactions` to cross-check normalized ingredient names against official FDA `drug_interactions` label sections.
4. `clinicaltrials_v2_search.py` (**Category 3 - Custom Built**):
   - Queries the modern ClinicalTrials.gov REST API v2 (`https://clinicaltrials.gov/api/v2/studies` and `/api/v2/studies/{nctId}`).
   - Supports filtering by condition, intervention, free-text term/acronym, recruitment status (`filter.overallStatus`), study phase (`AREA[Phase]`), and posted results (`aggFilters=results:with`), plus single-study NCT lookup with `resultsSection` primary outcome statistical analyses.
5. `local_clinical_library_search.py` (**Category 4 - Companion to Open WebUI Native Knowledge RAG**):
   - Primary recommendation for Category 4 is to use **Open WebUI's built-in Workspace -> Knowledge (RAG Collections)** with **Hybrid Search** enabled in Admin Settings -> Documents.
   - This optional companion tool provides strict read-only, `Path.resolve()`-jailed filename inventory and exact keyword/acronym search across `.md`, `.txt`, `.html`, and `.pdf` files inside `Documents\AI Teams\Clinical Library` without allowing directory traversal outside the configured root.
6. `clinical_calculators.py` (**Category 5 - Custom Built**):
   - Zero-`eval()`, deterministic Python implementations with physiologic bounds checking, standard clinical half-up rounding, step-by-step score breakdowns, guideline interpretation bands, and PubMed ID (PMID) citations for:
     - **Cockcroft-Gault CrCl** (Actual, Devine Ideal, and 40% Adjusted Body Weight CrCl; PMIDs 1244564, 22892266)
     - **CKD-EPI 2021 Race-Free Creatinine eGFR** (with KDIGO G1-G5 staging and optional Mosteller BSA-deindexed eGFR; PMIDs 34554658, 38490803)
     - **CHA2DS2-VASc Stroke Risk in Atrial Fibrillation** (with non-sex `CHA2DS2-VA` score and 2023 ACC/AHA/HRS Class 1/2a guidance; PMIDs 19762550, 38033089)
     - **Wells Criteria for DVT** (3-tier and 2-tier pretest probability models; PMIDs 9428249, 14507948)
     - **Wells Criteria for PE** (3-tier and 2-tier pretest probability models; PMIDs 10744147, 16403929)
     - **MELD(i), MELD-Na (OPTN 2016), and MELD 3.0 (OPTN July 2023)** (with full OPTN parameter clamping audit trail; PMIDs 11172350, 18768945, 34481845)
7. `nlm_icd10cm_lookup.py` (**Category 6 - Custom Built**):
   - Queries the official NLM Clinical Tables ICD-10-CM API v3 (`https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search`) with `sf=code,name` and `df=code,name` so both clinical diagnosis terms and ICD-10-CM code prefixes return accurate results.
8. `verify_all_tools.py`:
   - Live API and unit test verification suite that tests all 6 tool categories against live federal APIs and reference clinical calculator test vectors.

## How to Install in Open WebUI (When Ready)

1. Open **Open WebUI** (`http://localhost:3000` on the RTX 3060 PC).
2. Navigate to **Workspace -> Tools -> + Create Tool**.
3. Copy and paste the contents of any `.py` tool file from `/workspace/openwebui_medical_tools/` and click **Save**.
4. For **Category 4 (Local Clinical Library)**:
   - Go to **Admin Panel -> Settings -> Documents** and enable **Hybrid Search**.
   - Go to **Workspace -> Knowledge -> + Create Knowledge Base**, name it `Clinical Library`, and upload the guideline PDFs and clinical summaries from `Documents\AI Teams\Clinical Library`.
   - Bind the `Clinical Library` Knowledge Collection and the 5 custom tools to your custom clinical model preset under **Workspace -> Models**.

## Running the Verification Suite Locally

```bash
python3 /workspace/openwebui_medical_tools/verify_all_tools.py
# Or with uv + httpx + pydantic:
uv run --with pydantic --with httpx python3 /workspace/openwebui_medical_tools/verify_all_tools.py
```
