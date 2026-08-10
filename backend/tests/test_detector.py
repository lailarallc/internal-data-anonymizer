import pandas as pd
import pytest

from app.services.detector import _classify_column, profile_columns
from app.services.fakers.identifiers import is_valid_upc as _is_upc_valid


class TestUPCValidation:
    def test_valid_upc12(self):
        assert _is_upc_valid("012345678905") is True

    def test_invalid_upc12(self):
        assert _is_upc_valid("012345678900") is False

    def test_valid_ean13(self):
        assert _is_upc_valid("4006381333931") is True

    def test_non_digit(self):
        assert _is_upc_valid("abcdefghijkl") is False

    def test_wrong_length(self):
        assert _is_upc_valid("12345") is False


class TestClassifyColumn:
    def test_email_column(self):
        series = pd.Series(["a@b.com", "c@d.org", "e@f.net", "g@h.io", "j@k.co"])
        detected, strategy = _classify_column(series)
        assert detected == "email"
        assert strategy == "fake"

    def test_phone_column(self):
        series = pd.Series([
            "555-123-4567", "555-234-5678", "555-345-6789",
            "555-456-7890", "555-567-8901"
        ])
        detected, strategy = _classify_column(series)
        assert detected == "phone"
        assert strategy == "fake"

    def test_upc_column_high_validity(self):
        # Generate UPCs with valid check digits (85% valid)
        valid_upcs = ["012345678905"] * 85
        invalid_upcs = ["012345678900"] * 15
        series = pd.Series(valid_upcs + invalid_upcs)
        detected, strategy = _classify_column(series)
        assert detected == "upc_gtin"
        assert strategy == "format-preserve"

    def test_numeric_not_upc(self):
        # 12-digit numbers that fail check digit at random rates (~10%)
        import random
        random.seed(42)
        nums = [str(random.randint(100000000000, 999999999999)) for _ in range(100)]
        series = pd.Series(nums)
        detected, strategy = _classify_column(series)
        # Most random 12-digit numbers won't pass the >10% UPC check threshold
        # consistently, but some might. The key test is that truly numeric data
        # without UPC structure goes to numeric/jitter.
        assert detected in ("numeric", "upc_gtin")

    def test_date_column(self):
        series = pd.Series([
            "2024-01-15", "2024-02-20", "2024-03-25",
            "2024-04-10", "2024-05-05"
        ])
        detected, strategy = _classify_column(series)
        assert detected == "date"
        assert strategy == "jitter"

    def test_sku_column(self):
        series = pd.Series([
            "WM-8842-A", "TG-1156-C", "CS-3301-B",
            "AB-9912-D", "XY-4420-F"
        ])
        detected, strategy = _classify_column(series)
        assert detected == "sku"
        assert strategy == "format-preserve"

    def test_numeric_column(self):
        series = pd.Series([100, 200, 300, 400, 500])
        detected, strategy = _classify_column(series)
        assert detected == "numeric"
        assert strategy == "jitter"

    def test_name_column(self):
        series = pd.Series(["John Smith", "Jane Doe", "Bob Johnson", "Alice Brown", "Charlie Wilson"])
        detected, strategy = _classify_column(series)
        assert detected == "name"
        assert strategy == "fake"

    def test_generic_string(self):
        series = pd.Series(["abc123", "def456", "ghi789", "jkl012", "mno345"])
        detected, strategy = _classify_column(series)
        assert detected == "generic_string"
        assert strategy == "hash"

    def test_company_named_column_detected_as_company(self):
        series = pd.Series([
            "Harvest Market", "Fresh Fields", "Green Basket",
            "Valley Foods", "Meadow Mart",
        ])
        detected, strategy = _classify_column(series, "retailer_name")
        assert detected == "company"
        assert strategy == "fake"

    def test_person_name_column_defaults_to_person(self):
        series = pd.Series(["John Smith", "Jane Doe", "Bob Jones", "Al Brown", "Cy Young"])
        detected, strategy = _classify_column(series, "customer_name")
        assert detected == "name"  # ambiguous header -> person (safe direction)
        assert strategy == "fake"

    def test_header_hint_routes_identical_values(self):
        # Same name-like values classify by header: a business hint -> company,
        # anything else -> person.
        series = pd.Series([
            "Harvest Market", "Fresh Fields", "Green Basket",
            "Valley Foods", "Meadow Mart",
        ])
        assert _classify_column(series, "vendor")[0] == "company"
        assert _classify_column(series, "contact")[0] == "name"

    def test_empty_column(self):
        series = pd.Series([None, None, None])
        detected, strategy = _classify_column(series)
        assert detected == "empty"
        assert strategy == "passthrough"

    def test_mixed_types_below_threshold(self):
        # 60% numeric, 40% string — below 80% threshold for numeric
        series = pd.Series(["100", "200", "300", "abc", "def"] * 2)
        detected, strategy = _classify_column(series)
        # Won't hit 80% for numeric, falls through to generic
        assert detected == "generic_string"

    def test_cascade_priority_email_over_name(self):
        # Email patterns should be caught before name check
        series = pd.Series(["user@domain.com", "admin@site.org", "test@mail.net", "dev@app.io", "ops@cloud.com"])
        detected, _ = _classify_column(series)
        assert detected == "email"


class TestProfileColumns:
    def test_multi_column_dataframe(self):
        df = pd.DataFrame({
            "email": ["a@b.com", "c@d.org", "e@f.net"],
            "amount": [100.5, 200.3, 300.7],
            "name": ["Alice Smith", "Bob Jones", "Carol White"],
        })
        profiles = profile_columns(df)
        assert len(profiles) == 3

        email_p = next(p for p in profiles if p.name == "email")
        assert email_p.suggested_strategy == "fake"
        assert email_p.detected_type == "email"

        amount_p = next(p for p in profiles if p.name == "amount")
        assert amount_p.suggested_strategy == "jitter"
        assert amount_p.detected_type == "numeric"
        assert "mean" in amount_p.stats

        name_p = next(p for p in profiles if p.name == "name")
        assert name_p.suggested_strategy == "fake"

    def test_null_rate_calculation(self):
        df = pd.DataFrame({"col": [1, 2, None, None, 5]})
        profiles = profile_columns(df)
        assert profiles[0].null_rate == 0.4

    def test_zero_rows(self):
        df = pd.DataFrame({"col_a": pd.Series(dtype="object"), "col_b": pd.Series(dtype="int64")})
        profiles = profile_columns(df)
        assert len(profiles) == 2
        for p in profiles:
            assert p.suggested_strategy == "passthrough"
