from app.quality import inspect_citations


def test_citation_inspection_accepts_only_available_sources() -> None:
    inspection = inspect_citations("Tanım [K1], örnek [K2].", source_count=2)

    assert inspection.labels == [1, 2]
    assert inspection.invalid_labels == []
    assert inspection.is_valid is True


def test_citation_inspection_rejects_missing_or_out_of_range_labels() -> None:
    missing = inspect_citations("Atıfsız yanıt.", source_count=2)
    invalid = inspect_citations("Bu kaynak [K3].", source_count=2)

    assert missing.is_valid is False
    assert invalid.invalid_labels == [3]
    assert invalid.is_valid is False
