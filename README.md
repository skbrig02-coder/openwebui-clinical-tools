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
   - Zero-`eval()`, deterministic Python implementations with physiologic bounds checking, standard clinical half-up rounding for integer scores, step-by-step score breakdowns, guideline interpretation bands, and static PubMed ID (PMID) citations for:
     - **Cockcroft-Gault CrCl** (Actual, Devine Ideal, and 40% Adjusted Body Weight CrCl; PMIDs 1244564, 22892266)
     - **CKD-EPI 2021 Race-Free Creatinine eGFR** (with KDIGO G1-G5 staging and optional Mosteller BSA-deindexed eGFR; PMIDs 34554658, 38490803)
     - **CHA2DS2-VASc Stroke Risk in Atrial Fibrillation** (with non-sex `CHA2DS2-VA` score and 2023 ACC/AHA/HRS Class 1/2a guidance; PMIDs 19762550, 38033089)
     - **Wells Criteria for DVT** (3-tier and 2-tier pretest probability models; PMIDs 9428249, 14507948)
     - **Wells Criteria for PE** (3-tier and 2-tier pretest probability models; PMIDs 10744147, 16403929)
     - **MELD(i), MELD-Na (OPTN 2016), and MELD 3.0 (OPTN July 2023)** (with full OPTN parameter clamping audit trail; formula PMIDs 11172350, 18768945, 34481845). Observed 3-month mortality percentages from Wiesner et al., Gastroenterology 2003 (PMID 12512033), Table 4, are attached only to the original MELD(i) integer for bands `<9`, `10-19`, `20-29`, `30-39`, and `>=40`. Integer MELD(i) 9 is left unavailable because that table has no column for it. Those percentages are not applied to MELD-Na or MELD 3.0, and this tool does not invent replacement mortality percentages for those scores.
7. `nlm_icd10cm_lookup.py` (**Category 6 - Custom Built**):
   - Queries the official NLM Clinical Tables ICD-10-CM API v3 (`https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search`) with `sf=code,name` and `df=code,name` so both clinical diagnosis terms and ICD-10-CM code prefixes return accurate results.
8. `verify_all_tools.py`:
   - Verification suite for all 6 tool categories. It first runs deterministic checks with a mocked HTTP transport (no network): MELD mortality mapping, formula and input bounds, and lookup status/timestamp/provenance for success, match count 0, timeout, malformed response, and partial retrieval. It then runs the original live federal API checks and calculator reference vectors.

These six tools do not call NCBI E-utilities or PubMed. DailyMed, RxNorm, ClinicalTrials.gov, and Clinical Tables are NLM or ClinicalTrials.gov HTTP APIs. openFDA is an FDA HTTP API. Calculator PMIDs are static citation text copied into the source, not a live citation lookup. The local library tool reads files on disk.

## How to Install in Open WebUI (When Ready)

1. Open **Open WebUI** (`http://localhost:3000` on the RTX 3060 PC).
2. Navigate to **Workspace -> Tools -> + Create Tool**.
3. Copy and paste the contents of any `.py` tool file from `/workspace/openwebui_medical_tools/` and click **Save**.
4. For **Category 4 (Local Clinical Library)**:
   - Go to **Admin Panel -> Settings -> Documents** and enable **Hybrid Search**.
   - Go to **Workspace -> Knowledge -> + Create Knowledge Base**, name it `Clinical Library`, and upload the guideline PDFs and clinical summaries from `Documents\AI Teams\Clinical Library`.
   - Bind the `Clinical Library` Knowledge Collection and the 5 custom tools to your custom clinical model preset under **Workspace -> Models**.

## Lookup status, time, and provenance

Live lookup tools (`openfda_dailymed_drug_label.py`, `nlm_rxnorm_lookup.py`, `clinicaltrials_v2_search.py`, `nlm_icd10cm_lookup.py`) add a retrieval record to every report:

- `retrieved_at_utc` is when the tool made the request, as an explicit UTC timestamp `YYYY-MM-DDTHH:MM:SSZ`. It is separate from a label effective date, DailyMed published date, or trial update date (`source_content_date` when the payload has one).
- `status: ok` means the requested sources responded and at least one record was returned.
- `status: no_match` with `match_count: 0` means the sources responded successfully and returned no records. For openFDA drug labels, HTTP 404 with the API's NOT_FOUND response is this empty-search case.
- `status: error` with `error_type` of `timeout`, `network_error`, `http_error`, or `malformed_response` means the request failed or the body was not usable. That report is not a finding of zero matching records.
- `status: partial` means at least one source returned usable data and another failed, or a local scan stopped early or could not read every file.
- `status: invalid_input` means the tool rejected the arguments and did not send the request.
- `provenance: live_http` marks a federal or NLM HTTP call. `provenance: local_filesystem` marks the clinical library. `provenance: local_deterministic_calculation` marks the calculators. `ncbi_eutilities: not_used` is present on these reports. `citation_mode: static_pmid` marks calculator citations.

Reports do not write queries or API keys to a log. The optional openFDA key stays in the request and is omitted from the displayed URL.

## Updating an already installed Open WebUI copy

Merging or pulling this repository does not change either installed Open WebUI. Do that only when an update on the workstation is approved.

1. On the machine that hosts Open WebUI, open **Workspace -> Tools** and open each installed tool whose source changed.
2. Replace the entire Python source with the matching file from the approved commit, then **Save**. Paste the whole file. The tools are self-contained and do not import one another.
3. Files to replace when this change is the one being installed: `clinical_calculators.py`, `openfda_dailymed_drug_label.py`, `nlm_rxnorm_lookup.py`, `clinicaltrials_v2_search.py`, `nlm_icd10cm_lookup.py`, and `local_clinical_library_search.py`.
4. No new package and no new API key is required. Leave `OPENFDA_API_KEY` blank unless an openFDA key was already configured in that tool's valves. Do not paste a key into the tool source.
5. In a chat bound to the tools, run one label lookup and one MELD calculation. The label report should show `retrieved_at_utc` ending in `Z` and `status: ok` or `status: no_match`. The MELD report should show a Wiesner 2003 percentage only next to MELD(i), and `MELD-Na mortality percentage: unavailable` plus `MELD 3.0 mortality percentage: unavailable`.
6. Optional check from this repository directory: `python3 verify_all_tools.py`. The deterministic section does not need network access. The six live groups need outbound HTTPS to `api.fda.gov`, `dailymed.nlm.nih.gov`, `rxnav.nlm.nih.gov`, `clinicaltrials.gov`, and `clinicaltables.nlm.nih.gov`.
7. A passing verification script is a regression check of formulas, bounds, citations, and response handling. It is not a clinical validation of the calculators.

## Staged queue after the six-tool fix (not installed)

The six existing tools are unchanged by the later queue until a stage says otherwise. These additions are repository code only. They are not installed in Open WebUI by this pull request.

`deployment_inventory.py` is the rank-1 health check from the November 7, 2026 plan, extended through `verify_all_tools.py`. It is repository code and is not installed. A run labels `environment` so a checkout is not reported as a production pass (`production_instance_pass` stays false). It records each local tool file's `version` and `content_sha256`. If `OPENWEBUI_API_KEY` is unset, installed tools, valves, access grants, model bindings, and live execution are `untested` and no request is sent. The key is read only from that environment variable. The command does not open a database or browser cookies. With a key, it GETs `/api/v1/tools/` (active tools), `/api/v1/tools/list` (registry and access grants), `/api/v1/tools/id/{id}`, `/api/v1/tools/id/{id}/valves`, and `/api/v1/models/export`. A tool present in the registry and absent from the active list is `registered, not enabled`. `callable` and `reference_tested` stay `untested`: Open WebUI documents tool invocation only as `POST /api/chat/completions` with `tool_ids`, which also runs a model, and this program does not call that endpoint. There is no documented read-only execute API, and none is invented here. `--backup-dir` writes fetched ids, valves, and grants locally and does not update Open WebUI. `OPENWEBUI_BASE_URL` defaults to `http://127.0.0.1:3000`. Missing `meta.toolIds` is `field_absent`, not an empty binding list.

`tabular_query.py` is the rank-5 read-only dataset query. It is not installed. `ALLOWED_ROOTS` defaults to empty, so every query fails closed. CSV and SQLite SELECT or WITH queries stay inside the allowlist, reject `..`, absolute paths, and symlink escape, and use a SQLite authorizer plus `mode=ro` so writes, ATTACH, PRAGMA, and loadable extensions do not run. Results include schema, a unit from a sibling `.units.json` when the owner supplied one, otherwise `not_provided_by_file`, a file sha256, and `queried_at_utc`. A row cap is `status: partial`. Zero rows are `status: no_match`. A file over the byte cap is `resource_limit` and is not read as a result set. Parquet is `unavailable_dependency` when pyarrow is absent, and this module does not install it. No public dataset is queried.

## Running the Verification Suite Locally

```bash
python3 /workspace/openwebui_medical_tools/verify_all_tools.py
# Or with uv + httpx + pydantic:
uv run --with pydantic --with httpx python3 /workspace/openwebui_medical_tools/verify_all_tools.py
```
