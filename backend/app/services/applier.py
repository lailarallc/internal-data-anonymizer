from pathlib import Path

import pandas as pd

from .ingest import read_file


def _is_blank(value) -> bool:
    """A cell counts as blank if it is null or empty/whitespace-only.

    read_file uses keep_default_na=False, so empty cells arrive as "" rather
    than NaN; both must be preserved as-is (never anonymized to a fake value).
    """
    return pd.isna(value) or str(value).strip() == ""


def _anonymize_value(value, mapping: dict[str, str], column: str):
    """Substitute one cell, failing closed if it has no mapping.

    Blank cells pass through unchanged. Any non-blank value without a mapping
    raises rather than leaking the original — the cardinal rule for an
    anonymizer is that real data must never reach the output.
    """
    if _is_blank(value):
        return value
    key = str(value)
    if key not in mapping:
        raise ValueError(
            f"No anonymization mapping for a value in column '{column}'. "
            "Refusing to export to avoid leaking original data."
        )
    return mapping[key]


class AnonymizationLeakError(ValueError):
    """Raised when a transforming column returns its input unchanged.

    The cardinal failure mode this guards against is the 07-31 audit's date-jitter
    no-op: ``apply_jitter`` coerced dates to NaN and returned the ORIGINAL series, so
    the export leaked real dates while the operator believed they were jittered. Any
    non-passthrough column that comes back byte-for-byte identical to its input is a
    leak — fail loud rather than export it.
    """


def _assert_transformed(
    col: str, original: pd.Series, result: pd.Series, strategy: str
) -> None:
    """Fail loud if a transforming strategy returned every value unchanged.

    Skips legitimate no-op cases: a column with <=1 distinct non-blank value
    (nothing to disguise), and numeric jitter (clamping/rounding can validly land
    a boundary value back on its original — the perturbation is real elsewhere).
    A fully-unchanged non-numeric jitter column (e.g. dates) or any unchanged
    generative column (fake/format-preserve/hash) is always a leak.
    """
    if strategy in ("passthrough", "drop"):
        return
    mask = ~original.map(_is_blank)
    if mask.sum() == 0:
        return
    o = original[mask].astype(str).reset_index(drop=True)
    r = result[mask].astype(str).reset_index(drop=True)
    if o.nunique() <= 1:
        return
    if strategy == "jitter":
        numeric_share = pd.to_numeric(o, errors="coerce").notna().mean()
        if numeric_share >= 0.8:
            return
    if o.equals(r):
        raise AnonymizationLeakError(
            f"column '{col}' (strategy '{strategy}') returned every value "
            "unchanged — refusing to export original data."
        )


def apply_mappings(
    file_path: Path,
    column_mappings: dict[str, dict[str, str]],
    column_strategies: dict[str, str],
    jitter_results: dict[str, pd.Series] | None = None,
) -> pd.DataFrame:
    """Apply anonymization mappings to a dataframe.

    Args:
        file_path: Path to the original uploaded file.
        column_mappings: {column_name: {original: anonymized}} for generative columns.
        column_strategies: {column_name: strategy} for all columns.
        jitter_results: {column_name: jittered_series} for numeric columns.

    Returns:
        Anonymized DataFrame.
    """
    df = read_file(file_path)
    drop_cols = []

    for col in df.columns:
        strategy = column_strategies.get(col, "passthrough")

        if strategy == "drop":
            drop_cols.append(col)
        elif strategy == "passthrough":
            continue
        elif strategy == "jitter":
            if jitter_results and col in jitter_results:
                original = df[col]
                _assert_transformed(col, original, jitter_results[col], strategy)
                df[col] = jitter_results[col]
        elif strategy in ("fake", "format-preserve", "hash"):
            mapping = column_mappings.get(col, {})
            original = df[col]
            transformed = df[col].map(
                lambda v, m=mapping, c=col: _anonymize_value(v, m, c)
            )
            _assert_transformed(col, original, transformed, strategy)
            df[col] = transformed

    if drop_cols:
        df = df.drop(columns=drop_cols)

    return df


_FORMULA_PREFIXES = ("=", "+", "@", "\t", "\r")


def _neutralize_cell(value):
    """Prefix spreadsheet-formula-triggering cells with a quote.

    A cell whose text begins with = + @ (or tab/CR) executes as a formula when
    the exported CSV/XLSX is opened in Excel/Sheets. Since this tool emits
    spreadsheets built from arbitrary uploaded data, neutralize those cells.
    Genuine negative numbers are left intact; only formula-like leading-minus
    strings are quoted.
    """
    if not isinstance(value, str) or value == "":
        return value
    first = value[0]
    if first in _FORMULA_PREFIXES:
        return "'" + value
    if first == "-":
        try:
            float(value.replace(",", ""))
        except ValueError:
            return "'" + value
    return value


def _neutralize_formulas(df: pd.DataFrame) -> pd.DataFrame:
    return df.map(_neutralize_cell)


def export_dataframe(df: pd.DataFrame, output_path: Path, fmt: str) -> Path:
    """Export a DataFrame to the specified format.

    Args:
        df: The anonymized DataFrame.
        output_path: Directory to save the file.
        fmt: Output format (csv, xlsx, json, parquet).

    Returns:
        Path to the exported file.
    """
    output_path.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        path = output_path / "anonymized.csv"
        _neutralize_formulas(df).to_csv(path, index=False)
    elif fmt == "xlsx":
        path = output_path / "anonymized.xlsx"
        _neutralize_formulas(df).to_excel(path, index=False, engine="openpyxl")
    elif fmt == "json":
        path = output_path / "anonymized.json"
        df.to_json(path, orient="records", indent=2)
    elif fmt == "parquet":
        path = output_path / "anonymized.parquet"
        df.to_parquet(path, index=False)
    else:
        raise ValueError(f"Unsupported export format: {fmt}")

    return path
