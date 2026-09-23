# internal-data-anonymizer — Current Work Plan

The current arc of work. Updated when the arc changes, not every
session. For session-by-session state, see HANDOFF.md.

---

## Goal

Build a web-based data anonymization tool that scans tabular files, proposes
deterministic value mappings per column, lets the user review/edit mappings in
a UI, applies them, and provides reverse lookup — with project-level persistence
so mappings carry across files.

## Why this arc, why now

Portfolio diversification — shifting from purely analytical pieces to a
product-building demonstration. Also scratches a real consulting itch:
hand-anonymizing client data is tedious and error-prone.

## Business question this arc answers

Can I ship a polished, publicly visible tool that demonstrates product
engineering skill while also being genuinely useful in my consulting practice?

## Scope (from /clarify)

**In:**
- Web app (local, browser-based UI)
- Input: CSV, XLSX, JSON, Parquet
- Output: user's choice of format
- Workflow: scan → propose mapping → user reviews/edits → apply
- Deterministic mappings within a project (same input = same output across files)
- Numeric columns preserve statistical shape (mean, variance, skew)
- Format-preserving fakes (UPCs, GTINs, phone numbers, etc.) that mirror
  input data quality — valid originals get valid fakes, dirty originals get dirty fakes
- Reverse lookup: operator can trace any anonymized value back to original
- Project concept: mappings persist across multiple files within a project
- Polished GitHub repo with worked example

**Out (v1):**
- Desktop app packaging (v2 concern)
- Database connections
- Free-text anonymization (notes, comments, addresses)
- Differential privacy guarantees
- GUI for non-technical users (operator is the user for v1)
- Image, PDF, or unstructured data

## Tasks

All implementation units from `docs/plans/2026-05-16-001-feat-data-anonymizer-plan.md`:

- [x] U1: Project scaffolding — FastAPI + SvelteKit monorepo with SQLite
- [x] U2: Project dashboard — CRUD endpoints and Svelte dashboard UI
- [x] U3: File upload and column detection engine
- [x] U4: Strategy review UI — column-by-column confirmation flow
- [x] U5: Anonymization engine with custom Faker providers
- [x] U6: Rank-preserving numeric jitter with histogram preview
- [x] U7: Mapping review and edit UI (MappingTable, PatternRuleEditor)
- [x] U8: Apply mappings and multi-format export
- [x] U9: Reverse lookup (SearchBar, click-to-reveal in DataPreview)
- [x] U10: Multi-file mapping reuse

## Remaining before ship

- [x] End-to-end browser testing of full flow
- [x] README with worked example
- ~~Portfolio piece on Lailara site~~ (skipped — separate concern)

## Definition of done for this arc

- [ ] Tool accepts CSV/XLSX/JSON/Parquet and outputs in user-chosen format
- [ ] Web UI shows proposed mappings and allows editing before apply
- [ ] Deterministic: same input + same project = same output across runs/files
- [ ] Numeric columns preserve distribution shape visually and statistically
- [ ] Format-preserving fakes for structured identifiers (UPC, GTIN, phone, ZIP)
- [ ] Dirty-data fidelity: invalid originals produce invalid fakes at same rate
- [ ] Reverse lookup available in UI for the operator
- [ ] Project-level mapping persistence
- [x] Public GitHub repo with README and worked example
- ~~One portfolio piece on Lailara site using the tool's output~~ (deferred)

---

## Current Arc: (none active)

Improvement pass #2 completed 2026-07-31 — see Arc history below. No active arc;
next session picks from the optional follow-ups in HANDOFF.md or starts fresh.

## Completed Arc: Improvement pass #2 (2026-07-31)

**Goal:** Close the remaining data-leak paths, fix data-fidelity/robustness bugs, harden the API surface, and clean up dead/duplicated code — all findings from the 2026-07-31 /improve audit (correctness + security + maintainability reviewers, verified against the installed stack).

**Why this arc, why now:** /improve was overdue (due 2026-06-22). Audit reproduced a CRITICAL fail-open leak (columns set to hash/fake/format-preserve export real values when a mapping is missing — including new values in a second file) and a jitter path that passes non-numeric cells through verbatim. User approved fixing the full set including nice-to-haves.

**Tasks:**
- [x] 1. CRITICAL: export fails closed — generate missing mappings on demand + raise on any unmapped value (applier.py, export.py)
- [x] 2. HIGH: jitter never passes non-numeric cells through; dtype-safe write-back; pin pandas<3 (jitter.py, requirements.txt)
- [x] 3. HIGH: neutralize CSV/formula-injection on CSV/XLSX export (applier.py)
- [x] 4. Blank cells stay blank (not hashed/faked) (mappings.py, applier.py, export.py)
- [x] 5. Jitter preview matches export (same project seed + full column) (columns.py)
- [x] 6. read_file JSON reader honors columns + keep_default_na parity (ingest.py)
- [x] 7. Wrap remaining synchronous read_file calls in asyncio.to_thread (export/columns/mappings/upload)
- [x] 8. Typed Pydantic request bodies for strategy/mapping/export endpoints (schemas.py + routers)
- [x] 9. Defense-in-depth id-format guard in db.py path builders
- [x] 10. Generic client error messages (upload.py, columns.py) — detail dropped, not logged (no logging config in this local tool)
- [x] 11. Nice-to-have cleanups: dead sku branch, duplicated hash payload, cross-module `_is_valid_upc` naming, repeated format list
- [x] 12. npm audit fix (non-breaking) for postcss + vite highs — 4 vulns → 3 low (SvelteKit-3-only, deferred)

**Out of scope:** SvelteKit 3 major upgrade, new features, auto-generating mappings at upload time (kept at export)

**Definition of done:** All tests pass (81 existing + new regression tests for #1/#2/#3/#4), each fix verified, frontend still builds, no regressions

---

## Arc history

When an arc completes, archive its goal, completion date, and outcome
here. Then start a new arc above. Provides continuity without bloating
the active plan.

### 2026-07-31 — Improvement pass #2
- **Trigger:** User-initiated (`/improve` + code review + UI review) — overdue review (was due 2026-06-22)
- **What was reviewed:** Full audit + 3 parallel ce reviewers (correctness, security, maintainability) + UI review, verified against the installed stack
- **What was fixed:**
  - CRITICAL: export failed open — hash/fake/format-preserve columns exported real values when a mapping was missing (whole-column miss, or new values in a later file). Now fails closed + generates missing mappings on demand at export.
  - HIGH: jitter blanked non-numeric cells instead of leaking them; dtype-safe write-back; pinned pandas<3.
  - HIGH: neutralized CSV/formula-injection on csv/xlsx export.
  - Blank cells stay blank (not hashed/faked); jitter preview now matches export; JSON reader parity (columns + blank nulls); all read_file calls offloaded to threads; typed Pydantic request bodies; id-format path guards; generic error messages; dead-branch/dup-payload/naming cleanups.
  - npm audit fix: 4 vulns (2 high) → 3 low.
- **Verified:** 107 tests pass (26 new); critical leak fix confirmed end-to-end in the live app.
- **Deferred / tracked for next time:** integer-jitter `.0` fidelity; parquet reader NA parity; project-seed formula duplication (export + columns); UI review coverage of dynamic routes; 3 low npm vulns (need SvelteKit 3).
- **Correction:** correctness reviewer's "export fully broken" claim was based on pandas 3.0.3; installed is 2.3.3 — no crash, real bug was the narrower non-numeric leak.
- **Next review:** ~2026-10-31

### 2026-05-22 — Improvement pass
- **Trigger:** User-initiated (`/improve`) — lost track of project, wanted confidence it works correctly
- **What was reviewed:** Full audit: code quality, security, correctness, tests, dependencies, workflow files
- **What was fixed:**
  - CRITICAL: Jitter columns now actually anonymized at export (were silently passing through real data)
  - CRITICAL: `_infer_dtype` no longer crashes on mixed-type numeric columns
  - Hash strategy now includes column name in hash input (prevents cross-column value matching)
  - null_rate now correctly counts empty strings as nulls for CSV/XLSX files
  - Integer jitter resolves ties after rounding to preserve rank order
  - Reverse lookup escapes LIKE wildcards (%, _)
  - read_file supports usecols for targeted column reads
  - profile_columns runs in asyncio.to_thread (no longer blocks event loop)
  - Export filters mappings to only the file's columns
  - Review page uses shared api.ts client instead of raw fetch()
- **Deferred:** 7 npm vulnerabilities requiring SvelteKit/Vite major version upgrades (low/moderate severity, dev-server only, local tool)
- **Next review:** 2026-06-22

### 2026-05-16 — v1 shipped
- **Goal:** Build a web-based data anonymization tool with deterministic mappings, format-preserving fakes, and reverse lookup.
- **Outcome:** Shipped. 10 implementation units, 81 backend tests, full UI flow working. Public repo with README and worked example.
- **Deferred:** Portfolio piece on Lailara site, person-vs-product name detection, zip code format-preserving type.

---

## Improvement History

### 2026-09-23 — Audit (health check only)
- **Findings:** 0 critical, 5 important, 5 nice-to-have
- **Top concerns:** 9 commits (client mode, person/company name routing, fail-loud leak guard, Fix 3 measure passthrough) sit on branch client-mode-2026-08 unmerged to main, and client-mode CI only runs against main so they never ran in CI. In client mode, format-preserve on non-measure numeric columns collides and emits non-numeric values like "82 2" (20 of 80 in a 2-digit column), and an engagement.yml `types` override does not change the strategy (a retailer column detected as generic_string stays hashed). INPUT-SPEC.md still says numeric columns are jittered, and HANDOFF/PLAN/DECISIONS have not recorded any work since 2026-07-31. Automated /security-review, /ce:review and data-science-reviewer were replaced by a manual pass; 146 backend tests pass.
- **Action taken:** Audit only — no fixes this session
- **Next review:** 2026-12-22
