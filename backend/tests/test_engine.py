"""Tests for the anonymization engine (engine.py)."""

import pytest

from app.services.engine import generate_mappings


class TestDeterminism:
    """R28: Same project salt + column + value = same output."""

    def test_same_inputs_same_output(self):
        salt = "project-abc-123"
        col = "vendor_name"
        values = ["Walmart", "Target", "Costco"]

        result1 = generate_mappings(values, "fake", col, salt, "name")
        result2 = generate_mappings(values, "fake", col, salt, "name")

        assert result1 == result2

    def test_deterministic_across_calls(self):
        """Multiple calls produce identical mappings."""
        salt = "stable-salt-xyz"
        col = "email"
        values = ["user@example.com", "admin@test.org"]

        results = [
            generate_mappings(values, "fake", col, salt, "email")
            for _ in range(5)
        ]

        for r in results[1:]:
            assert r == results[0]


class TestCrossProjectIsolation:
    """R29: Different project salts = different outputs."""

    def test_different_salt_different_output(self):
        col = "vendor_name"
        values = ["Walmart", "Target"]

        result_a = generate_mappings(values, "fake", col, "salt-project-A", "name")
        result_b = generate_mappings(values, "fake", col, "salt-project-B", "name")

        # At least one mapping must differ
        assert result_a != result_b


class TestPassthroughStrategy:
    def test_returns_original_unchanged(self):
        values = ["keep_this", "and_this", "also_this"]
        result = generate_mappings(values, "passthrough", "col", "salt")

        for v in values:
            assert result[v] == v

    def test_all_values_present(self):
        values = ["a", "b", "c"]
        result = generate_mappings(values, "passthrough", "col", "salt")
        assert len(result) == 3


class TestDropStrategy:
    def test_returns_empty_dict(self):
        values = ["secret1", "secret2", "secret3"]
        result = generate_mappings(values, "drop", "col", "salt")
        assert result == {}


class TestHashStrategy:
    def test_consistent_12_char_hex(self):
        values = ["value1", "value2"]
        result = generate_mappings(values, "hash", "col", "salt123")

        for v in values:
            hashed = result[v]
            assert len(hashed) == 12
            # All hex characters
            assert all(c in "0123456789abcdef" for c in hashed)

    def test_hash_deterministic(self):
        values = ["test"]
        r1 = generate_mappings(values, "hash", "col", "salt")
        r2 = generate_mappings(values, "hash", "col", "salt")
        assert r1 == r2

    def test_hash_different_salts_differ(self):
        values = ["test"]
        r1 = generate_mappings(values, "hash", "col", "salt-A")
        r2 = generate_mappings(values, "hash", "col", "salt-B")
        assert r1["test"] != r2["test"]


class TestFakeStrategy:
    def test_name_type_returns_plausible_name(self):
        values = ["Walmart Inc", "Target Corp"]
        result = generate_mappings(values, "fake", "store", "salt", "name")

        for v in values:
            # Should be a non-empty string, not the original
            assert result[v] != v
            assert len(result[v]) > 0

    def test_email_type_returns_email_format(self):
        values = ["user@example.com"]
        result = generate_mappings(values, "fake", "email_col", "salt", "email")

        generated = result["user@example.com"]
        assert "@" in generated
        assert "." in generated.split("@")[1]

    def test_phone_type_returns_phone_format(self):
        values = ["555-123-4567"]
        result = generate_mappings(values, "fake", "phone_col", "salt", "phone")

        generated = result["555-123-4567"]
        # Should match XXX-XXX-XXXX pattern
        parts = generated.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 3
        assert len(parts[1]) == 3
        assert len(parts[2]) == 4


class TestSingleValue:
    def test_single_unique_value_one_entry_mapping(self):
        values = ["only_one"]
        result = generate_mappings(values, "fake", "col", "salt", "generic_string")
        assert len(result) == 1
        assert "only_one" in result


class TestFormatPreserve:
    def test_sku_format_preserved(self):
        values = ["WM-8842-A", "TG-1156-C"]
        result = generate_mappings(
            values, "format-preserve", "sku_col", "salt", "sku"
        )

        for v in values:
            generated = result[v]
            # Should match pattern: 2 alpha, dash, 4 digit, dash, 1 alpha
            assert len(generated) == len(v)
            assert generated[2] == "-"
            assert generated[7] == "-"
            assert generated[0:2].isalpha()
            assert generated[3:7].isdigit()
            assert generated[8].isalpha()


class TestMappingUniqueness:
    """Distinct originals must map to distinct fakes (1:1 within a column)."""

    def test_250_distinct_names_produce_250_distinct_fakes(self):
        # The curated retail-name pool has ~190 entries, so without collision
        # handling 250 originals cannot stay distinct (pigeonhole).
        values = [f"Person {i}" for i in range(250)]
        result = generate_mappings(values, "fake", "customer_name", "salt", "name")
        assert len(result) == 250
        assert len(set(result.values())) == 250

    def test_mapping_round_trips_uniquely(self):
        values = [f"Person {i}" for i in range(250)]
        result = generate_mappings(values, "fake", "customer_name", "salt", "name")
        reverse = {fake: orig for orig, fake in result.items()}
        assert len(reverse) == len(result)  # no fake shared by two originals
        for orig, fake in result.items():
            assert reverse[fake] == orig

    def test_collision_suffixes_are_deterministic(self):
        values = [f"Person {i}" for i in range(250)]
        r1 = generate_mappings(values, "fake", "customer_name", "salt", "name")
        r2 = generate_mappings(values, "fake", "customer_name", "salt", "name")
        assert r1 == r2

    def test_existing_fakes_are_reserved(self):
        # A fake persisted for another original (e.g. from an earlier file)
        # must not be reused; the collision gets a stable " 2" suffix.
        base = generate_mappings(["Some Value"], "fake", "col", "salt", "name")
        taken = base["Some Value"]
        result = generate_mappings(
            ["Some Value"], "fake", "col", "salt", "name",
            existing_fakes={taken},
        )
        assert result["Some Value"] != taken
        assert result["Some Value"] == f"{taken} 2"

    def test_format_preserve_mappings_are_unique(self):
        values = [f"CH-{i:04d}" for i in range(500)]
        result = generate_mappings(values, "format-preserve", "sku", "salt", "sku")
        assert len(set(result.values())) == 500

    def test_hash_mappings_are_unique(self):
        values = [f"row-{i}" for i in range(500)]
        result = generate_mappings(values, "hash", "col", "salt", "generic_string")
        assert len(set(result.values())) == 500


class TestReversibilityAtScale:
    """R28/R29 at scale: 10k distinct originals stay 1:1 and fully reversible."""

    def test_10k_company_names_are_1_to_1_reversible(self):
        # The retail pool has ~190 names, so 10,000 distinct originals force heavy
        # collision handling. Every original must still map to a distinct fake, and
        # the fake -> original inverse must recover every original.
        values = [f"Retailer {i}" for i in range(10_000)]
        result = generate_mappings(values, "fake", "retailer_name", "salt", "company")
        assert len(result) == 10_000
        assert len(set(result.values())) == 10_000  # no two originals share a fake
        reverse = {fake: orig for orig, fake in result.items()}
        assert len(reverse) == 10_000
        for orig in values:
            assert reverse[result[orig]] == orig  # round-trips uniquely

    def test_10k_person_names_are_1_to_1_reversible(self):
        values = [f"Person {i}" for i in range(10_000)]
        result = generate_mappings(values, "fake", "customer_name", "salt", "name")
        assert len(result) == 10_000
        assert len(set(result.values())) == 10_000

    def test_10k_reversibility_is_deterministic(self):
        values = [f"Retailer {i}" for i in range(10_000)]
        r1 = generate_mappings(values, "fake", "retailer_name", "salt", "company")
        r2 = generate_mappings(values, "fake", "retailer_name", "salt", "company")
        assert r1 == r2


class TestPersonVsCompanyFakes:
    """Person-name columns get person fakes; business columns get company fakes."""

    def test_name_type_produces_person_not_company(self):
        from app.services.fakers.retail import ALL_NAMES

        values = ["Jane Doe", "John Smith", "Maria Garcia"]
        result = generate_mappings(values, "fake", "customer_name", "salt", "name")
        for v in values:
            # A person fake must not come from the curated company pool.
            assert result[v] not in ALL_NAMES

    def test_company_type_produces_company_name(self):
        from app.services.fakers.retail import ALL_NAMES

        values = ["Walmart", "Target"]
        result = generate_mappings(values, "fake", "retailer", "salt", "company")
        for v in values:
            # Value comes from the pool (a dedup suffix like " 2" may be appended).
            assert any(
                result[v] == n or result[v].startswith(n + " ") for n in ALL_NAMES
            )
