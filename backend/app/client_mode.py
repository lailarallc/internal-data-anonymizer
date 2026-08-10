"""Client-mode CLI for the anonymizer.

Brings the anonymizer to the engagement checklist standard using the shared
``lailara_engagement`` library: tolerant CSV/XLSX intake, an engagement.yml that
carries client identity + per-column strategy overrides, and a provenance-footed,
draft-watermarked **Anonymization Report** that lists exactly which columns were
transformed and how — so a client can verify what left their data untouched and
what was disguised.

The heavy lifting reuses the existing, now leak-guarded pipeline (detector →
jitter/engine → applier), so client mode and the web app share one engine.

Usage:
    python -m app.client_mode --config engagement.yml --input client-data/file.csv \
        --out client-output [--final] [--format csv]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from lailara_engagement import build_provenance, load_config, read_table
from lailara_engagement import palette as P
from lailara_engagement.provenance import validation_status_label

from .services.applier import apply_mappings, export_dataframe
from .services.detector import profile_columns
from .services.engine import generate_mappings
from .services.jitter import apply_jitter

TOOL = "internal-data-anonymizer"
TOOL_VERSION = "1.0"

# Strategies that transform (vs. reveal) data. A column left on one of the
# revealing strategies is called out in the report so the operator can confirm.
_REVEALING = {"passthrough"}


@dataclass
class ColumnPlan:
    name: str
    detected_type: str
    strategy: str
    unique_count: int
    null_rate: float
    source: str  # "auto" | "config"
    transformed: int = 0  # non-blank cells actually changed


def _project_salt(config) -> str:
    """Deterministic per-engagement salt so mappings are stable and reversible."""
    return hashlib.sha256(
        (config.engagement_id + "|" + config.config_hash).encode()
    ).hexdigest()


# Fix 3 (Option B): a numeric column is passed through UNTOUCHED only when confidently
# a MEASURE (money/quantity) — name matches measure vocabulary AND values look
# continuous (decimals or wide range), with NO identifier tell (identifier name,
# leading zero, or fixed width). Everything else numeric is anonymized. Fail-safe: on
# any doubt, not a measure -> anonymize.
_MEASURE_WORDS = {
    "amount", "amt", "price", "cost", "revenue", "total", "subtotal", "qty",
    "quantity", "weight", "units", "unit", "margin", "spend", "sales", "dollars",
    "value", "net", "gross", "fee", "balance", "rate",
}
_IDENTIFIER_WORDS = {
    "zip", "postal", "id", "account", "member", "employee", "phone", "ssn",
    "number", "num", "code", "fax", "license", "routing", "card", "serial",
}


def _is_measure(name: str, series: pd.Series) -> bool:
    tokens = {t for t in re.split(r"[^a-z0-9]+", name.strip().lower()) if t}
    if tokens & _IDENTIFIER_WORDS:
        return False
    if not (tokens & _MEASURE_WORDS):
        return False
    vals = series.dropna().astype(str).str.strip()
    vals = vals[vals != ""]
    if len(vals) == 0:
        return False
    if vals.str.match(r"^0\d").any():                    # leading zero -> identifier-like
        return False
    if len(vals) > 1 and vals.str.len().nunique() == 1:  # fixed width -> identifier-like
        return False
    has_decimals = vals.str.contains(".", regex=False).any()
    nums = pd.to_numeric(vals, errors="coerce").dropna()
    wide_range = (
        len(nums) >= 4 and nums.nunique() > 3
        and (float(nums.max()) - float(nums.min())) >= 100
    )
    return bool(has_decimals or wide_range)


def build_plan(frame: pd.DataFrame, config) -> list[ColumnPlan]:
    """Detect a strategy per column, then apply engagement.yml overrides."""
    overrides = config.raw.get("anonymize", {}) if isinstance(config.raw, dict) else {}
    strat_over = overrides.get("strategies", {}) or {}
    type_over = overrides.get("types", {}) or {}
    passthrough_over = set(overrides.get("passthrough", []) or [])

    plans: list[ColumnPlan] = []
    for prof in profile_columns(frame):
        strategy = prof.suggested_strategy
        detected = prof.detected_type
        source = "auto"
        if prof.name in type_over:
            detected = type_over[prof.name]
            source = "config"
        # Explicit config wins both directions; otherwise a numeric column passes
        # through only if it is confidently a measure, else it is anonymized
        # format-preserving. (Date jitter/shift is unaffected.)
        if prof.name in strat_over:
            strategy = strat_over[prof.name]
            source = "config"
        elif prof.name in passthrough_over:
            strategy = "passthrough"
            source = "config"
        elif strategy == "jitter" and detected == "numeric":
            strategy = "passthrough" if _is_measure(prof.name, frame[prof.name]) else "format-preserve"
        plans.append(ColumnPlan(
            name=prof.name,
            detected_type=detected,
            strategy=strategy,
            unique_count=prof.unique_count,
            null_rate=prof.null_rate,
            source=source,
        ))
    return plans


def _anonymize(frame: pd.DataFrame, plans: list[ColumnPlan], config, tmp_csv: Path):
    """Run the existing pipeline on the tolerantly-read frame. Returns the df."""
    overrides = config.raw.get("anonymize", {}) if isinstance(config.raw, dict) else {}
    seed = int(overrides.get("seed", 42))
    salt = _project_salt(config)

    strategies = {p.name: p.strategy for p in plans}
    jitter_results: dict[str, pd.Series] = {}
    column_mappings: dict[str, dict[str, str]] = {}

    for p in plans:
        col = frame[p.name]
        if p.strategy == "jitter":
            jitter_results[p.name], _ = apply_jitter(col, seed=seed)
        elif p.strategy in ("fake", "format-preserve", "hash"):
            uniq = [v for v in col.astype(str) if v.strip() != ""]
            uniq = list(dict.fromkeys(uniq))  # stable de-dup, preserve order
            column_mappings[p.name] = generate_mappings(
                uniq, p.strategy, p.name, salt, p.detected_type
            )

    # Write the tolerantly-read frame to a normalized CSV so the existing
    # apply_mappings (with its leak guard) + export path can consume it.
    frame.to_csv(tmp_csv, index=False)
    out_df = apply_mappings(tmp_csv, column_mappings, strategies, jitter_results)

    # Count non-blank cells actually changed, for the report.
    for p in plans:
        if p.name not in out_df.columns:  # dropped
            p.transformed = 0
            continue
        orig = frame[p.name].astype(str).reset_index(drop=True)
        new = out_df[p.name].astype(str).reset_index(drop=True)
        nonblank = orig.str.strip() != ""
        p.transformed = int(((orig != new) & nonblank).sum())
    return out_df


def _render_report_html(config, plans, read_result, provenance, *, draft: bool) -> str:
    esc = html.escape
    draft_class = " ll-draft" if draft else ""
    rows = ""
    strat_label = {
        "jitter": "Jitter (shift/perturb)", "fake": "Fake (substitute)",
        "format-preserve": "Format-preserving fake", "hash": "Hash (irreversible token)",
        "passthrough": "Passthrough (UNCHANGED)", "drop": "Drop (removed)",
    }
    for p in plans:
        reveal = p.strategy in _REVEALING
        badge_bg = P.LL_SG_SURFACE if reveal else P.LL_HK_SURFACE
        badge_fg = P.LL_SG_DARK if reveal else P.LL_HK_DARK
        label = strat_label.get(p.strategy, p.strategy)
        rows += (
            f"<tr><td class=mono>{esc(p.name)}</td>"
            f"<td>{esc(p.detected_type)}</td>"
            f"<td><span class=ll-badge style='background:{badge_bg};color:{badge_fg}'>{esc(label)}</span></td>"
            f"<td class=num>{p.unique_count:,}</td>"
            f"<td class=num>{p.transformed:,}</td>"
            f"<td>{esc(p.source)}</td></tr>"
        )
    revealed = [p.name for p in plans if p.strategy in _REVEALING]
    reveal_note = ""
    if revealed:
        reveal_note = (
            "<p class=ll-warn>Left unchanged (passthrough): "
            + esc(", ".join(revealed))
            + " — confirm these carry no sensitive data before sharing.</p>"
        )
    # Fix 3 disclosure (the real safety net): list every numeric column passed through
    # UNTOUCHED and why, so a wrong call ("ship_zip — passed through as measure") is
    # visible to an operator rather than silent.
    untouched = [p for p in plans
                 if p.detected_type == "numeric" and p.strategy == "passthrough"]
    if untouched:
        items = "".join(
            "<li><span class=mono>" + esc(p.name) + "</span> — passed through "
            + ("(config)" if p.source == "config" else "as measure") + "</li>"
            for p in untouched
        )
        money_note = (
            "<div class=ll-warn><strong>Numeric columns passed through UNTOUCHED "
            "(not anonymized):</strong><ul>" + items + "</ul>"
            "Confirm each is genuinely a measure (money/quantity), not an identifier — "
            "these values are <strong>not</strong> anonymized. To anonymize one, name it "
            "under <span class=mono>anonymize.strategies</span>.</div>"
        )
    else:
        money_note = (
            "<div class=ll-safe><strong>No numeric column was passed through untouched.</strong> "
            "Every numeric column was anonymized; measures are passed through only when "
            "confidently identified.</div>"
        )
    css = _report_css(draft)
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Anonymization Report — {esc(config.client_name)}</title>
<style>{css}</style></head>
<body class="{draft_class.strip()}"><main class=ll-page>
<header class=ll-header>
  <div class=ll-eyebrow>Lailara LLC · Anonymization</div>
  <h1 class=ll-title>Anonymization Report</h1>
  <div class=ll-client>
    <div><span class=ll-k>Client</span> {esc(config.client_name)}</div>
    <div><span class=ll-k>Engagement</span> {esc(config.engagement_id)}</div>
    <div><span class=ll-k>As of</span> {esc(config.as_of_date.isoformat())}</div>
    <div><span class=ll-k>Input</span> {esc(read_result.filename)}</div>
    <div><span class=ll-k>Rows</span> {read_result.n_rows:,}</div>
  </div>
</header>
{money_note}
<section class=ll-section>
  <h2 class=ll-h2>What was transformed, and how</h2>
  <table class=ll-table>
    <thead><tr><th>Column</th><th>Detected type</th><th>Strategy</th>
      <th>Distinct mapped</th><th>Cells changed</th><th>Plan</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {reveal_note}
</section>
{provenance.to_html()}
</main></body></html>"""


def _report_css(draft: bool) -> str:
    draft_css = (
        ".ll-draft::before{content:'DRAFT';position:fixed;top:50%;left:50%;"
        "transform:translate(-50%,-50%) rotate(-32deg);font-family:var(--s);"
        "font-size:22vw;font-weight:700;color:rgba(204,16,10,.06);z-index:0;"
        "pointer-events:none;white-space:nowrap;}" if draft else ""
    )
    return f"""
:root{{--s:{P.LL_SERIF};--f:{P.LL_SANS};}}
*{{box-sizing:border-box}}
body{{margin:0;background:{P.LL_CANVAS};color:{P.LL_TEXT};font-family:var(--f);line-height:1.6}}
.ll-page{{position:relative;z-index:1;max-width:{P.LL_MAX_WIDTH};margin:0 auto;padding:48px 24px}}
.ll-header{{border-bottom:1px solid {P.LL_GRIDLINE};padding-bottom:24px;margin-bottom:24px}}
.ll-eyebrow{{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:{P.LL_RED};font-weight:600}}
.ll-title{{font-family:var(--s);font-weight:700;color:{P.LL_INK};font-size:34px;margin:8px 0 16px}}
.ll-client{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px 24px;font-size:14px}}
.ll-k{{display:block;color:{P.LL_TEXT_SEC};font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
.ll-h2{{font-family:var(--s);font-weight:700;color:{P.LL_INK};font-size:22px;margin:0 0 12px;
 padding-bottom:6px;border-bottom:1px solid {P.LL_GRIDLINE}}}
.ll-table{{width:100%;border-collapse:collapse;font-size:14px}}
.ll-table th{{text-align:left;background:{P.LL_CHICAGO};color:#fff;padding:8px 12px}}
.ll-table td{{padding:8px 12px;border-bottom:1px solid {P.LL_GRIDLINE}}}
.ll-badge{{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:2px}}
.mono{{font-family:ui-monospace,Consolas,monospace;font-size:12px}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.ll-warn{{background:{P.LL_SG_SURFACE};color:{P.LL_SG_DARK};padding:12px 16px;border-radius:2px;margin:16px 0}}
.ll-safe{{background:{P.LL_HK_SURFACE};color:{P.LL_HK_DARK};padding:12px 16px;border-radius:2px;margin:0 0 24px}}
.ll-provenance{{margin-top:40px;background:{P.LL_CARD_BG};color:{P.LL_CARD_TEXT};padding:20px 24px;border-radius:2px;font-size:13px}}
.ll-prov-title{{font-family:var(--s);font-weight:700;font-size:16px;margin-bottom:8px}}
.ll-provenance div{{margin-bottom:4px;color:{P.LL_CARD_SUBTITLE}}}
.ll-provenance strong{{color:{P.LL_CARD_TEXT}}}
.ll-prov-inputs{{width:100%;border-collapse:collapse;margin-top:8px}}
.ll-prov-inputs th{{text-align:left;border-bottom:1px solid rgba(255,255,255,.12);padding:4px 8px;color:{P.LL_CARD_MUTED}}}
.ll-prov-inputs td{{padding:4px 8px;border-bottom:1px solid rgba(255,255,255,.08);color:{P.LL_CARD_SUBTITLE}}}
.ll-prov-brand{{margin-top:12px;font-family:var(--s);color:{P.LL_CARD_MUTED}}}
{draft_css}
@media print{{body{{background:#fff}}}}
"""


def run(config_path: str, input_path: str, out_dir: str,
        fmt: str = "csv", final: bool = False) -> dict:
    config = load_config(config_path)
    read = read_table(input_path)
    plans = build_plan(read.frame, config)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    tmp_csv = out_path / ".intake_normalized.csv"
    try:
        out_df = _anonymize(read.frame, plans, config, tmp_csv)
    finally:
        if tmp_csv.exists():
            tmp_csv.unlink()

    export_path = export_dataframe(out_df, out_path, fmt)

    status = validation_status_label("clean")
    provenance = build_provenance(
        tool=TOOL, tool_version=TOOL_VERSION, inputs=[read], config=config,
        validation_status=status,
        extra={"columns": str(len(plans)),
               "transformed_columns": str(sum(1 for p in plans if p.strategy not in _REVEALING))},
    )
    report_html = _render_report_html(config, plans, read, provenance, draft=not final)
    report_path = out_path / "anonymization-report.html"
    report_path.write_text(report_html, encoding="utf-8")

    return {
        "output": str(export_path),
        "report": str(report_path),
        "plans": plans,
        "rows": read.n_rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="anonymize-client",
                                 description="Anonymize a client file in engagement client mode.")
    ap.add_argument("--config", required=True, help="engagement.yml")
    ap.add_argument("--input", required=True, help="client data file (CSV/XLSX)")
    ap.add_argument("--out", default="client-output", help="output directory")
    ap.add_argument("--format", default="csv", choices=["csv", "xlsx", "json", "parquet"])
    ap.add_argument("--final", action="store_true", help="drop the DRAFT watermark")
    args = ap.parse_args(argv)

    result = run(args.config, args.input, args.out, fmt=args.format, final=args.final)
    print(f"anonymized {result['rows']:,} rows -> {result['output']}")
    print(f"report -> {result['report']}")
    transformed = [p for p in result["plans"] if p.strategy not in _REVEALING]
    passthrough = [p for p in result["plans"] if p.strategy in _REVEALING]
    print(f"transformed {len(transformed)} column(s); "
          f"{len(passthrough)} left unchanged: {', '.join(p.name for p in passthrough) or 'none'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
