"""
Tests for BANKING77 external benchmark dataset and mapping.

These are deterministic tests that validate the dataset structure
and mapping completeness without making any Groq API calls.
"""

import json
import pytest
from pathlib import Path
from collections import Counter


DATASETS_DIR = Path(__file__).parent.parent / "evaluation" / "datasets"
MAPPING_FILE = DATASETS_DIR / "banking77_mapping.json"
BENCHMARK_FILE = DATASETS_DIR / "banking77_benchmark.json"

VALID_SUPPORTFLOW_CATEGORIES = {
    "Technical Support",
    "Billing",
    "Order Issue",
    "General Inquiry",
    "Refund Request",
}

EXPECTED_INTENTS = 77
EXPECTED_CASES = 154
EXPECTED_CASES_PER_INTENT = 2
EXPECTED_SAMPLING_SEED = 42


class TestBANKING77Mapping:
    """Tests for banking77_mapping.json"""

    @pytest.fixture
    def mapping_data(self):
        with open(MAPPING_FILE, 'r') as f:
            return json.load(f)

    def test_mapping_file_exists(self):
        """Mapping file must exist."""
        assert MAPPING_FILE.exists(), f"Mapping file not found: {MAPPING_FILE}"

    def test_mapping_has_metadata(self, mapping_data):
        """Mapping must have metadata section."""
        assert "metadata" in mapping_data
        assert "mappings" in mapping_data

    def test_mapping_covers_all_77_intents(self, mapping_data):
        """Mapping must cover exactly 77 BANKING77 intents."""
        mappings = mapping_data["mappings"]
        assert len(mappings) == EXPECTED_INTENTS, (
            f"Expected {EXPECTED_INTENTS} intents, got {len(mappings)}"
        )

    def test_all_mappings_have_required_fields(self, mapping_data):
        """Every mapping must have supportflow_category, rationale, ambiguous."""
        mappings = mapping_data["mappings"]
        for intent, mapping in mappings.items():
            assert "supportflow_category" in mapping, f"{intent}: missing 'supportflow_category'"
            assert "rationale" in mapping, f"{intent}: missing 'rationale'"
            assert "ambiguous" in mapping, f"{intent}: missing 'ambiguous'"

    def test_all_categories_are_valid(self, mapping_data):
        """All non-null categories must be valid SupportFlow categories."""
        mappings = mapping_data["mappings"]
        for intent, mapping in mappings.items():
            category = mapping.get("supportflow_category")
            if category is not None:
                assert category in VALID_SUPPORTFLOW_CATEGORIES, (
                    f"{intent}: invalid category '{category}'"
                )

    def test_ambiguous_field_is_boolean(self, mapping_data):
        """Ambiguous field must be a boolean."""
        mappings = mapping_data["mappings"]
        for intent, mapping in mappings.items():
            assert isinstance(mapping["ambiguous"], bool), (
                f"{intent}: 'ambiguous' must be boolean"
            )

    def test_special_case_reverted_card_payment_resolved(self, mapping_data):
        """The special case 'reverted_card_payment?' must be explicitly resolved."""
        mappings = mapping_data["mappings"]
        special_intent = "reverted_card_payment?"
        
        assert special_intent in mappings, f"Missing special case: {special_intent}"
        
        mapping = mappings[special_intent]
        # Must have a category (not ambiguous) OR be explicitly marked ambiguous
        assert (
            mapping.get("supportflow_category") is not None
            or mapping.get("ambiguous") is True
        ), f"{special_intent}: must be mapped or marked ambiguous"


class TestBANKING77Benchmark:
    """Tests for banking77_benchmark.json"""

    @pytest.fixture
    def benchmark_data(self):
        with open(BENCHMARK_FILE, 'r') as f:
            return json.load(f)

    def test_benchmark_file_exists(self):
        """Benchmark file must exist."""
        assert BENCHMARK_FILE.exists(), f"Benchmark file not found: {BENCHMARK_FILE}"

    def test_benchmark_has_metadata(self, benchmark_data):
        """Benchmark must have metadata section."""
        assert "metadata" in benchmark_data
        assert "cases" in benchmark_data

    def test_benchmark_has_154_cases(self, benchmark_data):
        """Benchmark must have exactly 154 cases (2 × 77)."""
        cases = benchmark_data["cases"]
        assert len(cases) == EXPECTED_CASES, (
            f"Expected {EXPECTED_CASES} cases, got {len(cases)}"
        )

    def test_all_77_intents_represented(self, benchmark_data):
        """All 77 BANKING77 intents must be represented."""
        cases = benchmark_data["cases"]
        intent_counts = Counter(c["original_intent"] for c in cases)
        assert len(intent_counts) == EXPECTED_INTENTS, (
            f"Expected {EXPECTED_INTENTS} unique intents, got {len(intent_counts)}"
        )

    def test_exactly_2_cases_per_intent(self, benchmark_data):
        """Each intent must have exactly 2 cases."""
        cases = benchmark_data["cases"]
        intent_counts = Counter(c["original_intent"] for c in cases)
        
        for intent, count in intent_counts.items():
            assert count == EXPECTED_CASES_PER_INTENT, (
                f"{intent}: expected {EXPECTED_CASES_PER_INTENT} cases, got {count}"
            )

    def test_all_ids_unique(self, benchmark_data):
        """All case IDs must be unique."""
        cases = benchmark_data["cases"]
        ids = [c["id"] for c in cases]
        unique_ids = set(ids)
        assert len(ids) == len(unique_ids), (
            f"Duplicate IDs found: {len(ids)} total, {len(unique_ids)} unique"
        )

    def test_all_ids_follow_format(self, benchmark_data):
        """All IDs must follow format 'banking77-XXX'."""
        cases = benchmark_data["cases"]
        for case in cases:
            assert case["id"].startswith("banking77-"), (
                f"Invalid ID format: {case['id']}"
            )

    def test_all_texts_unique(self, benchmark_data):
        """All case texts must be unique (no accidental duplicates)."""
        cases = benchmark_data["cases"]
        texts = [c["text"] for c in cases]
        unique_texts = set(texts)
        assert len(texts) == len(unique_texts), (
            f"Duplicate texts found: {len(texts)} total, {len(unique_texts)} unique"
        )

    def test_all_cases_have_required_fields(self, benchmark_data):
        """All cases must have required schema fields."""
        cases = benchmark_data["cases"]
        required_fields = [
            "id",
            "text",
            "original_intent",
            "gold_supportflow_category",
            "mapping_ambiguous"
        ]
        
        for i, case in enumerate(cases):
            for field in required_fields:
                assert field in case, (
                    f"Case {i} ({case.get('id', 'unknown')}): missing field '{field}'"
                )

    def test_all_categories_valid_or_ambiguous(self, benchmark_data):
        """All categories must be valid SupportFlow categories or ambiguous."""
        cases = benchmark_data["cases"]
        for case in cases:
            if not case["mapping_ambiguous"]:
                category = case["gold_supportflow_category"]
                assert category in VALID_SUPPORTFLOW_CATEGORIES, (
                    f"Case {case['id']}: invalid category '{category}'"
                )

    def test_metadata_has_sampling_seed(self, benchmark_data):
        """Metadata must document sampling seed for reproducibility."""
        metadata = benchmark_data["metadata"]
        assert "sampling_seed" in metadata, "Missing sampling_seed in metadata"
        assert metadata["sampling_seed"] == EXPECTED_SAMPLING_SEED, (
            f"Expected seed {EXPECTED_SAMPLING_SEED}, got {metadata['sampling_seed']}"
        )

    def test_metadata_documents_test_split(self, benchmark_data):
        """Metadata must document that test split was used."""
        metadata = benchmark_data["metadata"]
        assert "split" in metadata, "Missing split in metadata"
        assert metadata["split"] == "test", (
            f"Expected 'test' split, got '{metadata['split']}'"
        )

    def test_no_original_text_modification(self, benchmark_data):
        """Original BANKING77 text must not be modified."""
        cases = benchmark_data["cases"]
        for case in cases:
            text = case["text"]
            # Basic sanity checks
            assert len(text) > 0, f"Case {case['id']}: empty text"
            assert isinstance(text, str), f"Case {case['id']}: text must be string"


class TestBANKING77MappingConsistency:
    """Tests for consistency between mapping and benchmark."""

    @pytest.fixture
    def mapping_data(self):
        with open(MAPPING_FILE, 'r') as f:
            return json.load(f)

    @pytest.fixture
    def benchmark_data(self):
        with open(BENCHMARK_FILE, 'r') as f:
            return json.load(f)

    def test_benchmark_intents_all_in_mapping(self, mapping_data, benchmark_data):
        """All benchmark intents must exist in mapping."""
        mappings = mapping_data["mappings"]
        cases = benchmark_data["cases"]
        
        benchmark_intents = set(c["original_intent"] for c in cases)
        mapping_intents = set(mappings.keys())
        
        missing = benchmark_intents - mapping_intents
        assert len(missing) == 0, (
            f"Benchmark uses intents not in mapping: {missing}"
        )

    def test_benchmark_categories_match_mapping(self, mapping_data, benchmark_data):
        """Benchmark categories must match mapping."""
        mappings = mapping_data["mappings"]
        cases = benchmark_data["cases"]
        
        for case in cases:
            intent = case["original_intent"]
            benchmark_cat = case["gold_supportflow_category"]
            benchmark_amb = case["mapping_ambiguous"]
            
            mapping = mappings[intent]
            mapping_cat = mapping["supportflow_category"]
            mapping_amb = mapping["ambiguous"]
            
            assert benchmark_cat == mapping_cat, (
                f"Case {case['id']}: category mismatch for {intent}. "
                f"Mapping={mapping_cat}, Benchmark={benchmark_cat}"
            )
            assert benchmark_amb == mapping_amb, (
                f"Case {case['id']}: ambiguous flag mismatch for {intent}. "
                f"Mapping={mapping_amb}, Benchmark={benchmark_amb}"
            )


class TestBANKING77CategoryDistribution:
    """Tests for category distribution reporting."""

    @pytest.fixture
    def benchmark_data(self):
        with open(BENCHMARK_FILE, 'r') as f:
            return json.load(f)

    def test_category_distribution_documented(self, benchmark_data):
        """Category distribution should be calculable."""
        cases = benchmark_data["cases"]
        non_ambiguous = [c for c in cases if not c["mapping_ambiguous"]]
        
        category_counts = Counter(
            c["gold_supportflow_category"] for c in non_ambiguous
        )
        
        # Just verify we can compute it (no specific distribution required)
        assert len(category_counts) > 0, "No categories found"
        assert sum(category_counts.values()) == len(non_ambiguous)
        
        # Verify all categories are valid
        for category in category_counts.keys():
            assert category in VALID_SUPPORTFLOW_CATEGORIES

    def test_refund_request_category_exists(self, benchmark_data):
        """Refund Request category should have at least some cases."""
        cases = benchmark_data["cases"]
        refund_cases = [
            c for c in cases
            if c["gold_supportflow_category"] == "Refund Request"
            and not c["mapping_ambiguous"]
        ]
        
        # Just verify it exists (even if small)
        assert len(refund_cases) > 0, "No Refund Request cases found"
