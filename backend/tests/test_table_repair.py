import copy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.table_repair import validate_shape, validate_against_source
from app.conversion import ConvertedAsset, ConvertedContent


def proposal():
    return validate_shape({"rows": 2, "columns": 2, "grid": [["Ad", "Değer"], ["A", "2"]], "description": "Tablo, A öğesinin değerini gösterir.", "confidence": 96, "needs_review": False})


def test_shape_rejects_incomplete_and_overlapping_tables():
    value = proposal()
    value["cells"].pop()
    with pytest.raises(ValueError, match="eksik"):
        validate_shape(value)
    value = proposal()
    value["cells"].append(copy.deepcopy(value["cells"][0]))
    with pytest.raises(ValueError, match="çakışıyor"):
        validate_shape(value)


def test_source_validation_detects_invented_numbers_without_trusting_confidence():
    value = proposal()
    assert validate_against_source(value, "Ad Değer A 2")["status"] == "passed"
    value["cells"][-1]["text"] = "9"
    result = validate_against_source(value, "Ad Değer A 2")
    assert result["status"] == "manual_review"
    assert result["additional_numbers"] == ["9"]
    assert result["missing_numbers"] == ["2"]


def test_dimension_correction_keeps_every_cell_and_requires_review():
    value = proposal()
    value["rows"] = 1
    corrected = validate_shape(value)
    assert corrected["rows"] == 2
    assert len(corrected["cells"]) == 4
    assert corrected["needs_review"] is True
    assert corrected["shape_adjustment"]["declared"] == [1, 2]

    grid_value = {"rows": 1, "columns": 2, "grid": [["A", "B"], ["1", "2"]]}
    corrected = validate_shape(grid_value)
    assert corrected["rows"] == 2 and len(corrected["cells"]) == 4
    assert corrected["needs_review"] is True
    short = validate_shape({"rows": 3, "columns": 2, "grid": [["A", "B"], ["1", "2"]]})
    assert short["rows"] == 2 and short["needs_review"] is True
    assert validate_against_source(short, "A B 1 2")["status"] == "manual_review"


def test_model_retries_invalid_shape_without_inventing_cells(monkeypatch):
    import asyncio
    import json
    from app import llm
    from types import SimpleNamespace
    calls = []
    async def fake_complete(config, messages, **kwargs):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            return json.dumps({"rows": 2, "columns": 2, "grid": [["eksik"]]})
        return json.dumps(proposal())
    monkeypatch.setattr(llm, "current_runtime_model_config", lambda: SimpleNamespace(vision_model="test-vision"))
    monkeypatch.setattr(llm, "complete", fake_complete)
    result = asyncio.run(llm.OllamaClient().reconstruct_table(b"source", 42))
    assert result["model_attempts"] == 2
    assert "yapısal hata" in calls[-1]


def test_visual_caption_is_checked_against_same_image_before_approval(monkeypatch):
    import asyncio
    import json
    from app import llm
    from types import SimpleNamespace
    calls = []
    async def fake_complete(config, messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps({"description": "Tahmini nesne." if len(calls) == 1 else "Bardağa düşen iki oval nesne gösterilir.", "confidence": 85, "needs_review": True})
    monkeypatch.setattr(llm, "current_runtime_model_config", lambda: SimpleNamespace(vision_model="test-vision"))
    monkeypatch.setattr(llm, "complete", fake_complete)
    result = asyncio.run(llm.OllamaClient().describe_visual_asset(b"same-source", "picture", 47))
    assert len(calls) == 2
    assert all(call[1]["image"] == b"same-source" for call in calls)
    assert "Tahmini nesne." in calls[1][0][-1]["content"]
    assert result["description"] == "Bardağa düşen iki oval nesne gösterilir."
    assert result["needs_review"] is True


def test_automatic_table_requires_approval_and_is_reversible(tmp_path, monkeypatch):
    from app import main
    original = "> Kaynak tablo korunuyor."
    markdown = f"# Ders\n\n<!-- konulab-table-body:0:start -->\n\n{original}\n\n<!-- konulab-table-body:0:end -->\n\n<!-- konulab-table:0 -->\n\nSonraki konu."
    monkeypatch.setattr(main.settings, "storage_path", tmp_path)
    monkeypatch.setattr(main, "convert_to_markdown", lambda path: ConvertedContent(markdown=markdown, converter="test", converter_version="test", ocr_used=False, page_count=1, table_count=1, image_count=0, assets=(ConvertedAsset(kind="table", page_number=1, content=b"source-png", table_index=0, structured_markdown=original),), quality_report={"tables": [{"index": 0, "source_image_fallback": True, "validation": {"source_text": "Ad Değer A 2"}}]}))
    calls = []
    class Model:
        async def reconstruct_table(self, image, page):
            calls.append(page)
            return proposal()
    monkeypatch.setattr(main, "get_ollama_client", lambda: Model())
    with TestClient(main.app) as client:
        response = client.post("/api/documents/convert", data={"logical_key": uuid4().hex}, files={"file": ("farkli-belge.pdf", b"pdf", "application/pdf")})
        assert response.status_code == 202
        identifier = response.json()["document"]["id"]
        url = f"/api/documents/{identifier}/conversion"
        before = client.get(url).json()
        assert calls == [1]
        asset = before["assets"][0]
        assert asset["analysis_status"] == "ready"
        assert asset["table_validation"]["status"] == "passed"
        assert asset["inclusion_status"] == "pending"
        assert asset["proposed_markdown"] not in before["markdown"]
        assert client.post(f"/api/documents/{identifier}/publish-conversion", json={"confirmation": "OGRENME_HARITASINA_DONUSTUR"}).status_code == 409
        decision = f'/api/documents/{identifier}/assets/{asset["id"]}/inclusion/'
        client.post(decision + "accepted").raise_for_status()
        accepted = client.get(url).json()["markdown"]
        assert asset["proposed_markdown"] in accepted
        assert "Sonraki konu." in accepted
        client.post(decision + "accepted").raise_for_status()
        assert client.get(url).json()["markdown"] == accepted
        client.post(decision + "rejected").raise_for_status()
        rejected = client.get(url).json()["markdown"]
        assert original in rejected and asset["proposed_markdown"] not in rejected
        refinement_calls = []
        async def fake_refine(candidate, source_path, table_index, source, aliases, model, directory, progress):
            refinement_calls.append(table_index)
            progress("Hücre kontrolü deneniyor.")
            result = copy.deepcopy(candidate)
            result["refinement"] = {"status": "confirmed", "cells": []}
            return result
        monkeypatch.setattr(main, "refine_table", fake_refine)
        client.post(f'/api/documents/{identifier}/assets/{asset["id"]}/analyze?refine_only=true').raise_for_status()
        refined = client.get(url).json()
        assert calls == [1], "Hedefli kontrol tüm tabloyu tekrar üretmemeli."
        assert refinement_calls == [0]
        assert refined["assets"][0]["inclusion_status"] == "pending"
        assert refined["markdown"] == rejected


def test_failed_table_preserves_source_and_allows_explicit_keep(tmp_path, monkeypatch):
    from app import main
    from app.llm import OllamaUnavailableError
    original = "> Kaynak tablo okunamadı, kaynak görseli korunuyor."
    markdown = f"<!-- konulab-table-body:0:start -->\n{original}\n<!-- konulab-table-body:0:end -->\n<!-- konulab-table:0 -->"
    monkeypatch.setattr(main.settings, "storage_path", tmp_path)
    monkeypatch.setattr(main, "convert_to_markdown", lambda path: ConvertedContent(markdown=markdown, converter="test", converter_version="test", ocr_used=False, page_count=1, table_count=1, image_count=0, assets=(ConvertedAsset(kind="table", page_number=1, content=b"source-png", table_index=0, structured_markdown=original),), quality_report={"tables": [{"index": 0, "source_image_fallback": True}]}))
    class Model:
        async def reconstruct_table(self, image, page):
            raise OllamaUnavailableError("Model çevrimdışı.")
    monkeypatch.setattr(main, "get_ollama_client", lambda: Model())
    with TestClient(main.app) as client:
        response = client.post("/api/documents/convert", data={"logical_key": uuid4().hex}, files={"file": ("tarama.pdf", b"pdf", "application/pdf")})
        identifier = response.json()["document"]["id"]
        url = f"/api/documents/{identifier}/conversion"
        before = client.get(url).json()
        asset = before["assets"][0]
        assert before["status"] == "ready" and original in before["markdown"]
        assert asset["analysis_status"] == "failed" and asset["proposed_table"] is None
        decision = f'/api/documents/{identifier}/assets/{asset["id"]}/inclusion/'
        assert client.post(decision + "accepted").status_code == 409
        client.post(decision + "rejected").raise_for_status()
        after = client.get(url).json()
        assert original in after["markdown"] and after["assets"][0]["inclusion_status"] == "rejected"
