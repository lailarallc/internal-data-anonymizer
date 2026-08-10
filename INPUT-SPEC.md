# INPUT-SPEC — internal-data-anonymizer (client mode)

What to hand the anonymizer and what it does with it. Unlike the analysis tools, the
anonymizer has **no required columns** — it accepts any tabular file and anonymizes each
column according to a detected (or configured) strategy. This spec documents the accepted
formats, how columns are classified, and the `engagement.yml` overrides.

## Accepted files

- **CSV** or **XLSX** (also JSON/Parquet for the web app). Read via `lailara_engagement`'s
  tolerant reader: UTF-8 / UTF-8-BOM / latin-1; comma / semicolon / tab delimiters; leading
  blank rows and trailing junk dropped; header whitespace trimmed.
- **Identifiers are read as text.** ZIP/postal, GTIN/UPC, store numbers keep their leading
  zeros; nothing is parsed to a number on intake (so `02134` never becomes `2134`).

## How each column is handled

The detector classifies every column and picks a default strategy. You confirm or override
in `engagement.yml`.

| Detected type | Default strategy | What happens |
|---|---|---|
| `email` | fake | Replaced with a plausible synthetic email. |
| `phone` | fake | Replaced with a valid-format US phone. |
| `upc_gtin` | format-preserve | Replaced with a check-digit-valid UPC at the same validity rate. |
| `date` | jitter | Each date shifted ±1–30 days (seeded); output normalized to `YYYY-MM-DD`. Never returned unchanged. |
| `sku` | format-preserve | Replaced preserving the alphanumeric pattern. |
| `numeric` | jitter | Rank-preserving Gaussian perturbation, clamped to the original range. Digit-string columns (ZIPs, quantities) round-trip as zero-padded integer strings — never floats. |
| `name` | fake | **Person** columns → person names. |
| `company` | fake | **Business/retailer** columns (header hints: retailer, vendor, store, supplier, brand, …) → company names from the curated pool. |
| `generic_string` | hash | Replaced with a deterministic 12-char token. |
| (any) | passthrough | Left unchanged. Flagged in the report so you confirm it holds nothing sensitive. |
| (any) | drop | Column removed from the output. |

**Person vs. company:** a name-like column is treated as a **person** unless its header hints
at a business (retailer / vendor / store / supplier / brand / merchant / chain / distributor /
company / manufacturer / wholesaler / banner). Override per column in `engagement.yml`.

## Guarantees (the confidentiality contract)

- **1:1 and reversible.** Distinct originals always map to distinct fakes (collisions get a
  deterministic ` 2`, ` 3` … suffix), so a project mapping reverses cleanly even for 10,000+
  distinct values against the ~190-name pool. Same salt + column + value ⇒ same fake.
- **No silent leak.** A transforming column that would return its input unchanged fails loud
  (`AnonymizationLeakError`) rather than exporting originals. Any non-blank value without a
  mapping raises rather than passing through. Blank/null cells are preserved as-is.
- **No coercion of identifiers.** ZIPs and other digit-strings keep their width and leading
  zeros end-to-end.

## engagement.yml (client mode)

```yaml
client:
  name: "Meridian Farms"          # required — printed on the report
engagement:
  id: "MER-2026-08"               # required
as_of_date: "2026-07-31"          # required — never today's date
anonymize:
  seed: 42                        # deterministic jitter/mapping seed
  types:                          # override the detected type per column
    retailer: company
    account_manager: name
  strategies:                     # override the strategy per column
    ssn: hash
    internal_notes: drop
```

## Run

```bash
# from backend/, with lailara_engagement installed (pip install -e ../../engagement-template/lib)
python -m app.client_mode --config ../engagement.yml --input ../client-data/export.csv \
    --out ../client-output [--final] [--format csv|xlsx|json|parquet]
```

Outputs to `client-output/` (gitignored): the anonymized file and a branded, provenance-footed
`anonymization-report.html` listing every column, its strategy, and how many cells changed.
The report carries a DRAFT watermark until `--final`.
