# Open WebUI Medical Tools Vetting Report

**Prepared for:** Stuart Brigham, MD (local RTX 3060 workstation Open WebUI Setup)
**Date:** Wednesday, October 28, 2026
**Scope:** Audit of existing Open WebUI Community Hub tools (`openwebui.com/t/`), GitHub repositories, and MCP tool packages across the 6 planned clinical tool categories, plus live verification of custom-built replacement tools in `/workspace/openwebui_medical_tools/`.

---

## Executive Summary Table

| # | Candidate Tool Category | Native Tools on `openwebui.com/t/` | External GitHub / MCP Packages Found | Key Clinical, API, or Security Pitfalls Found | Final Verdict | Pre-Built File in `/workspace/openwebui_medical_tools/` |
|---|---|---|---|---|---|---|
| **1** | **openFDA / DailyMed Drug Label Lookup** | **0 native tools** (`openfda`, `dailymed`, `fda` return 0 drug tools) | `@drvibeai/clinical-apis-mcp`, `Cicatriiz/healthcare-mcp-public`, `OpenClinicalAI/clinicalai-mcp`, `MONSTER13LIAR/Neurophrax` | `Cicatriiz/healthcare-mcp-public` omits `boxed_warning` on label queries and takes `limit=1` (returning combo/repackaged products); `@drvibeai/clinical-apis-mcp` dumps raw 50-300 KB SPL JSON that overflows local 12 GB VRAM context windows. | **Build clean custom Open WebUI tool** | `openfda_dailymed_drug_label.py` |
| **2** | **NLM RxNorm Lookup** | **0 native tools** (`rxnorm`, `rxnav` return 0 tools) | `@drvibeai/clinical-apis-mcp`, `OpenClinicalAI/clinicalai-mcp`, `mcp-clinical-reasoner` (PyPI) | NLM permanently retired the RxNav Drug Interaction API (`/REST/interaction/*`) on Jan 2, 2024 (live-verified HTTP 404). Older community scripts and `mitre/fhir-exercises` call the dead endpoint; `@drvibeai` omits brand (`BN`) and clinical strength (`SCD`/`SBD`) mappings. | **Build clean custom Open WebUI tool** | `nlm_rxnorm_lookup.py` |
| **3** | **ClinicalTrials.gov Search** | **0 native tools** (`clinicaltrials` returns 0 tools) | `@drvibeai/clinical-apis-mcp`, `Cicatriiz/healthcare-mcp-public`, `OpenClinicalAI/clinicalai-mcp`, `MONSTER13LIAR/Neurophrax` | Legacy ClinicalTrials.gov v1 API (`/api/query/study_fields`) was retired in June 2024 (live-verified HTTP 404). `Cicatriiz` v1.x passed invalid `query.status` instead of `filter.overallStatus`; `@drvibeai` and `Neurophrax` lack single-study NCT ID lookup with `resultsSection` outcomes. | **Build clean custom Open WebUI tool** | `clinicaltrials_v2_search.py` |
| **4** | **Search Over Local Clinical Library** | Generic file tools (`jcarlosroldan/safe_file_viewer`, `rjtpp/file_search_tool`) and KB admin (`izyazev/knowledge_base_manager`) | Open WebUI Built-in **Workspace -> Knowledge** (RAG Collections + Hybrid Search) | Community file viewers open `.pdf` files as raw binary bytes without PDF extraction and allow arbitrary host path traversal (`cd("..")`). Open WebUI's native Knowledge Collections handle PDF chunking, embeddings, and hybrid BM25 + vector reranking natively. | **Use Open WebUI built-in feature** (Knowledge Collections) + optional path-jailed companion tool | `local_clinical_library_search.py` (optional companion) |
| **5** | **Code-Based Clinical Calculators** | **0 clinical calculators** (`egfr`, `crcl`, `meld`, `wells`, `cha2ds2` return 0 clinical tools) | `OpenClinicalAI/clinicalai-mcp` (`@openclinicalai/calc`), `Cicatriiz/healthcare-mcp-public` (BMI only) | General calculators on `openwebui.com/t/` (`openwebui/calculator`, 6,283 downloads; `makelvin/enhanced_calculator_`, 688 downloads) execute raw `eval(equation)` / `sympify()` (critical RCE vulnerability) and contain zero medical formulas. `OpenClinicalAI/clinicalai-mcp` is an external Node.js monorepo (v0.1 in progress) and its `hepatology.ts` only implements FIB-4, Child-Pugh, and Glasgow-Blatchford (missing MELD-Na and MELD 3.0). | **Build clean custom Open WebUI tool** | `clinical_calculators.py` |
| **6** | **NLM ICD-10-CM Lookup** | **0 native tools** (`icd`, `icd10` return 0 tools) | `@drvibeai/clinical-apis-mcp`, `Cicatriiz/healthcare-mcp-public`, `OpenClinicalAI/clinicalai-mcp`, `MONSTER13LIAR/Neurophrax` | `MONSTER13LIAR/Neurophrax` (`sources/clinical_tables.py`) hardcodes `sf="name"`, which causes code-prefix searches (such as `I50.2` or `E11.22`) to fail completely. | **Build clean custom Open WebUI tool** | `nlm_icd10cm_lookup.py` |

---

## Detailed Vetting Findings by Category

### Category 1: openFDA / DailyMed Drug Label Lookup
- **Official APIs:**
  - openFDA Drug Label API: `https://api.fda.gov/drug/label.json`
  - NLM DailyMed REST API v2: `https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json`
- **What Exists in the Open WebUI Community:**
  - Direct queries against `https://api.openwebui.com/api/v1/posts/search` for `openfda`, `dailymed`, `fda`, and `drug` returned **0 native Open WebUI Python tools** for FDA or DailyMed drug labels.
  - External MCP / Python packages on GitHub:
    1. `@drvibeai/clinical-apis-mcp` (npm v0.3.0, author DrVibeAI): Exposes `openfda_query` and `dailymed_label`.
    2. `Cicatriiz/healthcare-mcp-public` (GitHub / npm v2.1.1, author Cicatriiz): Exposes `fda_drug_lookup` (`server/fda-tool.js`).
    3. `OpenClinicalAI/clinicalai-mcp` (GitHub monorepo, v0.1 in progress): Exposes `@openclinicalai/drugs` (`packages/drugs/src/clients/openfda.ts`).
    4. `MONSTER13LIAR/Neurophrax` (GitHub Python bot): Contains `sources/openfda.py`.
- **Code Quality, Security, and Clinical Pitfalls:**
  - **Missing Boxed Warnings in `Cicatriiz/healthcare-mcp-public`:** In `server/fda-tool.js`, when `searchType === 'label'`, the code extracts `indications_and_usage`, `dosage_and_administration`, `contraindications`, `warnings_and_cautions`, `adverse_reactions`, and `drug_interactions`, but **omits `boxed_warning`** (only extracting `boxed_warning` when `searchType === 'adverse_events'`).
  - **Combination / Repackaged Product Bias (`limit=1`):** Querying `api.fda.gov/drug/label.json` with `limit=1` often returns a multi-ingredient combination product (for example, sitagliptin/metformin when searching metformin) or a state repackager label. Adopting `MONSTER13LIAR/Neurophrax`'s check (`len(substance_name) == 1`) across the top 5 hits ensures single-ingredient monotherapy labels are selected first.
  - **Context Window Overflow on RTX 3060 (12 GB VRAM):** `@drvibeai/clinical-apis-mcp` returns the raw `data.results` array from openFDA without section extraction or truncation. A single FDA SPL JSON record is 50 KB to 300 KB, which immediately exhausts local model context on an RTX 3060.
- **Verdict:** **Build clean custom Open WebUI tool** (`openfda_dailymed_drug_label.py`).

---

### Category 2: NLM RxNorm Lookup
- **Official API:** `https://rxnav.nlm.nih.gov/REST/` (`rxcui.json`, `approximateTerm.json`, `rxcui/{rxcui}/properties.json`, `rxcui/{rxcui}/allrelated.json`, `rxcui/{rxcui}/related.json`).
- **Verification of Retired NLM RxNav Drug Interaction API:**
  - **Confirmed retired:** The NLM Lister Hill National Center for Biomedical Communications (LHNCBC) permanently discontinued the RxNav Drug-Drug Interaction API (`/REST/interaction/interaction.json` and `/REST/interaction/list.json`) on **January 2, 2024**.
  - **Live verification from box:** Calling `https://rxnav.nlm.nih.gov/REST/interaction/interaction.json?rxcui=88014` and `https://rxnav.nlm.nih.gov/REST/interaction/list.json?rxcuis=207106+152923+656659` returns **`HTTPError 404: Not Found`**.
- **What Exists in the Community:**
  - **0 native Open WebUI Python tools** on `openwebui.com/t/`.
  - Legacy notebooks and community wrappers (such as `mitre/fhir-exercises` and pre-2024 `gagle/ncbijs` / OpenEMR issues) still reference the dead `/REST/interaction/` endpoint.
  - Newer MCP servers (`@drvibeai/clinical-apis-mcp`, `OpenClinicalAI/clinicalai-mcp`, and `mcp-clinical-reasoner` v1.0.0 on PyPI by `pcmedsinge/fhir-mcp-suite`) correctly note that NLM retired `/REST/interaction/*` and pivot to openFDA's `drug_interactions` label section for free-tier interaction checking. However, `@drvibeai`'s `rxnorm_normalize` only queries `tty=IN` (base ingredient), omitting brand names (`BN`), precise salt ingredients (`PIN`), dose forms (`DF`), and clinical drug strengths (`SCD`/`SBD`).
- **Verdict:** **Build clean custom Open WebUI tool** (`nlm_rxnorm_lookup.py`) providing full RxCUI normalization, `IN`/`PIN`/`MIN`/`BN`/`DF`/`SCD`/`SBD` concept mapping via `allrelated.json`, and an integrated `check_fda_label_interactions` method that normalizes drug names via RxNorm and cross-checks official FDA `drug_interactions` label sections.

---

### Category 3: ClinicalTrials.gov Search
- **Official API:** ClinicalTrials.gov REST API v2 (`https://clinicaltrials.gov/api/v2/studies` and `https://clinicaltrials.gov/api/v2/studies/{nctId}`).
- **Verification of Retired ClinicalTrials.gov v1 API:**
  - **Confirmed retired:** NLM retired the classic ClinicalTrials.gov v1 API (`/api/query/study_fields`, `/api/query/full_studies`) in **June 2024**.
  - **Live verification from box:** Calling `https://clinicaltrials.gov/api/query/study_fields?expr=diabetes&fields=NCTId,BriefTitle&fmt=json` returns **`HTTPError 404: Not Found`**.
- **What Exists in the Community:**
  - **0 native Open WebUI Python tools** on `openwebui.com/t/`.
  - In the MCP ecosystem, `Cicatriiz/healthcare-mcp-public` v1.x used invalid status parameter names (`query.status` instead of `filter.overallStatus`) and had to release a breaking fix in v2.0.0 (`server/clinical-trials-tool.js`).
  - `@drvibeai/clinical-apis-mcp` and `MONSTER13LIAR/Neurophrax` call `/api/v2/studies`, but neither provides a dedicated single-trial NCT ID lookup (`/api/v2/studies/{nctId}`) that parses posted `resultsSection` outcome measures and statistical analyses (hazard ratios, confidence intervals, p-values).
- **Verdict:** **Build clean custom Open WebUI tool** (`clinicaltrials_v2_search.py`) supporting both multi-filter search (`query.cond`, `query.intr`, `query.term`, `filter.overallStatus`, `AREA[Phase]`, `aggFilters=results:with`) and single-study `get_trial_by_nct_id` with `resultsSection` outcome parsing.

---

### Category 4: Search Over Local Clinical Library (`Documents\AI Teams\Clinical Library`)
- **What Exists in the Open WebUI Community:**
  - Community file tools on `openwebui.com/t/`:
    1. `jcarlosroldan/safe_file_viewer` (`https://openwebui.com/t/jcarlosroldan/safe_file_viewer`, 204 downloads)
    2. `rjtpp/file_search_tool` (`https://openwebui.com/t/rjtpp/file_search_tool`, 64 downloads)
    3. `izyazev/knowledge_base_manager` (`https://openwebui.com/t/izyazev/knowledge_base_manager`, 124 downloads)
- **Code Quality, Security, and Clinical Pitfalls of Community File Tools:**
  - **No Path Jail / Arbitrary Host Directory Traversal:** `jcarlosroldan/safe_file_viewer` exposes `cd(path)` starting at `os.path.expanduser("~")` with no root jail (`cd("../..")` can traverse the entire host disk). `rjtpp/file_search_tool` accepts arbitrary absolute paths in `search_files(directory=...)`.
  - **No PDF Text Extraction:** Both `safe_file_viewer` and `file_search_tool` read raw file lines/bytes (`open(fp, 'rb')`). Calling them on a clinical guideline PDF returns raw binary `%PDF-1.5` stream data that corrupts the LLM context.
- **Comparison with Open WebUI Built-in Knowledge (RAG) Collections:**
  - Open WebUI includes a mature, built-in **Workspace -> Knowledge** engine that ingests `.pdf`, `.md`, and `.txt` files, extracts text per page, chunks with configurable overlap, indexes into the local vector database (Chroma/PGVector/Qdrant), and supports **Hybrid Search (BM25 lexical + dense embedding + CrossEncoder reranking)** in **Admin Panel -> Settings -> Documents**.
  - A Knowledge Collection can be permanently bound to a custom "Clinical Assistant" model preset under **Workspace -> Models** (or invoked on demand with `#` in any chat), requiring zero custom tool calls for semantic retrieval.
- **Verdict:** **Use Open WebUI built-in feature (Workspace -> Knowledge Collections with Hybrid Search enabled)** as the primary mechanism for guideline PDFs and summaries, and optionally pair it with our pre-built, strictly path-jailed companion tool (`local_clinical_library_search.py`) when exact filename inventory (`*`) or literal regex/acronym grep across `Documents\AI Teams\Clinical Library` is desired.

---

### Category 5: Code-Based Clinical Calculators (CrCl, CKD-EPI 2021 eGFR, CHA2DS2-VASc, Wells DVT & PE, MELD-Na / MELD 3.0)
- **What Exists in the Open WebUI Community:**
  - Searching `openwebui.com/t/` for `egfr`, `crcl`, `meld`, `wells`, `cha2ds2`, `creatinine`, and `gfr` returned **0 medical calculator tools**.
  - General calculator tools on `openwebui.com/t/`:
    1. `openwebui/calculator` (`https://openwebui.com/t/openwebui/calculator`, 6,283 downloads): **CRITICAL SECURITY FLAW** - executes `eval(equation)` directly in Python (`result = eval(equation)`), exposing the host to arbitrary remote code execution (RCE) via prompt injection, and has no medical formulas.
    2. `makelvin/enhanced_calculator_` (`https://openwebui.com/t/makelvin/enhanced_calculator_`, 688 downloads): Calls `sympy.sympify(newequation)` on untrusted strings (which internally invokes `eval()` in SymPy).
    3. `filipebrigido/advanced_calculator` (679 downloads) and `nananek/openwebui-calculator-tools` (GitHub): Safe AST arithmetic evaluators, but contain zero clinical equations, parameter bounds, or risk-stratification tables.
  - External MCP packages on GitHub:
    1. `Cicatriiz/healthcare-mcp-public` (`server/medical-calculator-tool.js`): Only implements BMI (584 bytes).
    2. `OpenClinicalAI/clinicalai-mcp` (`packages/calc`): TypeScript MCP package (v0.1 in progress) that implements Cockcroft-Gault, CKD-EPI 2021, CHA2DS2-VASc, Wells DVT, and Wells PE, **but its `hepatology.ts` only implements FIB-4, Child-Pugh, and Glasgow-Blatchford (missing MELD-Na and MELD 3.0)**.
- **Verdict:** **Build clean custom Open WebUI tool** (`clinical_calculators.py`) in pure, deterministic Python with zero `eval()`, strict physiologic input validation, standard clinical half-up rounding, step-by-step score breakdowns, and primary PMID citations for all 6 required calculators:
  1. **Cockcroft-Gault CrCl** (Cockcroft & Gault 1976, PMID 1244564) + Devine Ideal Body Weight (IBW) and 40% Adjusted Body Weight (AdjBW) dosing recommendations (Winter 2012, PMID 22892266).
  2. **CKD-EPI 2021 Race-Free Creatinine eGFR** (Inker et al. *NEJM* 2021, PMID 34554658) + KDIGO 2024 G1-G5 staging + optional Mosteller BSA-deindexed eGFR (mL/min).
  3. **CHA2DS2-VASc** (Lip et al. *Chest* 2010, PMID 19762550) + non-sex `CHA2DS2-VA` score + 2023 ACC/AHA/ACCP/HRS oral anticoagulation recommendations (PMID 38033089).
  4. **Wells Criteria for DVT** (Wells et al. *Lancet* 1997 / *NEJM* 2003, PMID 9428249 / 14507948) with both 3-tier and 2-tier pretest probability models.
  5. **Wells Criteria for PE** (Wells et al. *Thromb Haemost* 2000, PMID 10744147) with both 3-tier and 2-tier pretest probability models.
  6. **MELD(i), MELD-Na (OPTN 2016), and MELD 3.0 (OPTN July 2023)** (Kamath 2001 PMID 11172350; Kim 2008 PMID 18768945; Kim 2021 PMID 34481845) with full OPTN parameter clamping audit trail (Bilirubin/INR/Cr floor 1.0; Cr cap 4.0 for MELD-Na and 3.0 for MELD 3.0; Na 125-137 mEq/L; Albumin 1.5-3.5 g/dL).

---

### Category 6: NLM ICD-10-CM Lookup
- **Official API:** NLM Clinical Tables ICD-10-CM REST API v3 (`https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search`).
- **What Exists in the Community:**
  - **0 native Open WebUI Python tools** on `openwebui.com/t/` (`icd` and `icd10` searches return 0 tools).
  - External MCP / bot implementations exist in `@drvibeai/clinical-apis-mcp`, `Cicatriiz/healthcare-mcp-public`, `OpenClinicalAI/clinicalai-mcp`, and `MONSTER13LIAR/Neurophrax`.
- **Code Quality and API Pitfalls:**
  - In `MONSTER13LIAR/Neurophrax` (`sources/clinical_tables.py`), the query sets `sf="name"` (search fields = description only). If a clinician searches by an ICD-10-CM code or prefix (such as `I50.2` or `E11.22`), the API returns 0 matches because `code` was excluded from `sf`.
  - Setting `sf="code,name"` and `df="code,name"` allows seamless searching by either clinical description or ICD-10-CM code prefix in a single tool.
- **Verdict:** **Build clean custom Open WebUI tool** (`nlm_icd10cm_lookup.py`).

---

## Bonus Audit: Existing PubMed Tools on `openwebui.com/t/`
While auditing the Open WebUI Community Hub for medical tools, we also inspected all 5 existing PubMed tools on `openwebui.com/t/` so Stuart knows which ones are safe or unsafe if he adds a PubMed tool:

1. **`zyman/pubmedsearch`** (`https://openwebui.com/t/zyman/pubmedsearch`, 397 downloads): **DO NOT INSTALL (SEVERE PRIVACY / EXFILTRATION RISK).** Does not use NCBI E-utilities; hardcodes calls to Alibaba Cloud DashScope (`dashscope.aliyuncs.com`) and sends user prompts and URLs to a third-party proxy (`https://gpts.webpilot.ai/api/read`).
2. **`beaudamore/pubmed_medical_deep_research_tool`** (`https://openwebui.com/t/beaudamore/pubmed_medical_deep_research_tool`, 732 downloads, GitHub `beaudamore/openwebui-pubmed-tool`): Has a documented HIGH-severity bug in its own repository (`KNOWN_ISSUES.md`, Aug 2026): 7 synchronous `requests.get()` calls inside `async def` methods that block the entire Open WebUI FastAPI event loop for up to 120 seconds, plus heavy NLP dependencies (`spacy`, `nltk`, `pandas`).
3. **`vvyas/pubmed_search`** (`https://openwebui.com/t/vvyas/pubmed_search`, 172 downloads, GitHub `dnacode/ai-openwebui-tools-pubmed`): Calls `esearch` + `esummary` using synchronous `requests.get()` with **no `timeout` parameter** (can hang Open WebUI indefinitely) and never fetches abstracts (`efetch`).
4. **`zuiluo1116/pubmed_search`** (`https://openwebui.com/t/zuiluo1116/pubmed_search`, 322 downloads): Fetches abstracts via `efetch` XML and supports `NCBI_API_KEY` via `Valves`, but calls blocking `time.sleep()` and synchronous `requests.get()` inside the tool.
5. **`danvics/pubmed_literature_search`** (`https://openwebui.com/t/danvics/pubmed_literature_search`, v6.2.0): Feature-rich Vancouver citation and abstract fetcher with `Valves`, though it also uses synchronous `requests` rather than async `httpx`.
