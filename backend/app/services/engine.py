import hashlib

from faker import Faker

from .fakers.identifiers import (
    EmailProvider,
    PhoneProvider,
    UPCProvider,
    is_valid_upc,
)
from .fakers.patterns import PatternProvider
from .fakers.retail import RetailProvider


def _make_faker_with_providers() -> Faker:
    fake = Faker()
    fake.add_provider(RetailProvider)
    fake.add_provider(UPCProvider)
    fake.add_provider(PhoneProvider)
    fake.add_provider(EmailProvider)
    fake.add_provider(PatternProvider)
    return fake


def _key_payload(project_salt: str, column_name: str, value: str) -> bytes:
    """The bytes keyed by both the fake seed and the hash strategy.

    Kept in one place so the salting scheme can't drift between the two.
    """
    return (project_salt + column_name + value).encode()


def _compute_seed(project_salt: str, column_name: str, value: str) -> int:
    return int(hashlib.sha256(_key_payload(project_salt, column_name, value)).hexdigest(), 16)


def _detect_upc_valid_rate(values: list[str]) -> float:
    if not values:
        return 1.0
    valid_count = sum(1 for v in values if is_valid_upc(v.strip()))
    return valid_count / len(values)


def _generate_fake(fake: Faker, value: str, detected_type: str) -> str:
    if detected_type == "name":
        # Person-name columns get person fakes. (Previously every name column got
        # a company name from the retail pool — the 07-31 audit's P2 leak of
        # persons rendered as businesses.)
        return fake.name()
    elif detected_type == "company":
        return fake.retail_name()
    elif detected_type == "email":
        return fake.realistic_email()
    elif detected_type == "phone":
        return fake.us_phone()
    elif detected_type == "upc_gtin":
        return fake.upc(valid=True)
    elif detected_type == "sku":
        return fake.pattern_match(value)
    else:
        return fake.name()


def _generate_format_preserve(
    fake: Faker,
    value: str,
    detected_type: str,
    valid_rate: float = 1.0,
) -> str:
    if detected_type == "upc_gtin":
        should_be_valid = fake.random_int(1, 100) <= int(valid_rate * 100)
        return fake.upc(valid=should_be_valid)
    return fake.pattern_match(value)


def _dedupe_fake(candidate: str, used: set[str]) -> str:
    """Make a generated fake unique within its column mapping.

    Distinct originals must never share a fake: a collision merges two
    entities in the export and makes reverse lookup ambiguous. On collision
    the candidate is deterministically extended with a stable numeric suffix
    ("Spark Electronics" -> "Spark Electronics 2", then "... 3", ...), so the
    same inputs always produce the same mapping.
    """
    if candidate not in used:
        return candidate
    n = 2
    while f"{candidate} {n}" in used:
        n += 1
    return f"{candidate} {n}"


def generate_mappings(
    unique_values: list[str],
    strategy: str,
    column_name: str,
    project_salt: str,
    detected_type: str = "generic_string",
    existing_fakes: set[str] | None = None,
) -> dict[str, str]:
    """Generate a deterministic 1:1 original->anonymized mapping.

    ``existing_fakes`` carries anonymized values already persisted for this
    column (e.g. from earlier files in the project); new fakes are kept
    distinct from them as well, so uniqueness holds across the whole stored
    column mapping, not just this batch.
    """
    if strategy == "drop":
        return {}

    if strategy == "passthrough":
        return {v: v for v in unique_values}

    valid_rate = 1.0
    if strategy == "format-preserve" and detected_type == "upc_gtin":
        valid_rate = _detect_upc_valid_rate(unique_values)

    fake = _make_faker_with_providers()
    used: set[str] = set(existing_fakes) if existing_fakes else set()
    mappings: dict[str, str] = {}
    for value in unique_values:
        seed = _compute_seed(project_salt, column_name, value)
        fake.seed_instance(seed)

        if strategy == "hash":
            candidate = hashlib.sha256(
                _key_payload(project_salt, column_name, value)
            ).hexdigest()[:12]
        elif strategy == "fake":
            candidate = _generate_fake(fake, value, detected_type)
        elif strategy == "format-preserve":
            candidate = _generate_format_preserve(
                fake, value, detected_type, valid_rate
            )
        else:
            continue

        candidate = _dedupe_fake(candidate, used)
        used.add(candidate)
        mappings[value] = candidate

    return mappings
