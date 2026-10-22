import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

from app import table_refinement as refinement
from app.table_repair import validate_shape, validate_against_source, candidate_markdown


def table(text="Eşesiz"):
    return validate_shape({"rows": 1, "columns": 1, "grid": [[text]], "needs_review": True, "confidence": 80, "description": "Tablo."})


class Evidence:
    words = [{"text": "Eşeysiz", "normalized": "eşeysiz", "box": (1, 1, 8, 8)}]
    box = (0, 0, 20, 20)
    def __init__(self, *args):
        pass
    def render(self, box, filename, **kwargs):
        return b"source-crop", {"dpi": 576, "image": filename, "box": list(box), "pixels": [160, 160]}
    def close(self):
        pass


class Model:
    read = "Eşeysiz"
    context = []
    async def read_table_word(self, image):
        assert image == b"source-crop"
        return {"text": self.read, "readable": True}
    async def review_table_structure(self, image, candidate):
        return {"structure_correct": True, "needs_review": False, "context_text": self.context, "issues": []}


def test_crop_confirmation_changes_only_suspect_word_and_keeps_input(tmp_path, monkeypatch):
    monkeypatch.setattr(refinement, "PdfTableEvidence", Evidence)
    original = table()
    snapshot = copy.deepcopy(original)
    result = asyncio.run(refinement.refine_table(original, Path("test.pdf"), 0, "Eşeysiz", {}, Model(), tmp_path))
    assert original == snapshot
    assert result["cells"][0]["text"] == "Eşeysiz"
    assert result["refinement"]["cells"][0]["accepted"] is True
    assert result["refinement"]["status"] == "confirmed"
    assert (tmp_path / "audit.json").is_file()


def test_visual_hallucination_cannot_replace_source_word(tmp_path, monkeypatch):
    monkeypatch.setattr(refinement, "PdfTableEvidence", Evidence)
    monkeypatch.setattr(refinement, "independent_ocr", lambda image: None)
    model = Model()
    model.read = "Bambaşka"
    result = asyncio.run(refinement.refine_table(table(), Path("test.pdf"), 0, "Eşeysiz", {}, model, tmp_path))
    assert result["cells"][0]["text"] == "Eşesiz"
    assert result["needs_review"] is True
    assert result["refinement"]["cells"][0]["accepted"] is False


def test_independent_ocr_must_agree_with_pdf_to_override_visual_misreading(tmp_path, monkeypatch):
    monkeypatch.setattr(refinement, "PdfTableEvidence", Evidence)
    monkeypatch.setattr(refinement, "independent_ocr", lambda image: {"text": "Eşeysiz", "confidence": 95})
    model = Model()
    model.read = "Eşesiz"
    result = asyncio.run(refinement.refine_table(table(), Path("test.pdf"), 0, "Eşeysiz", {}, model, tmp_path))
    assert result["cells"][0]["text"] == "Eşeysiz"
    assert result["refinement"]["cells"][0]["verification_method"] == "pdf_text+turkish_ocr"


def test_context_is_preserved_without_changing_table_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(refinement, "PdfTableEvidence", Evidence)
    model = Model()
    model.context = ["6. Etkinlik"]
    result = asyncio.run(refinement.refine_table(table("Ad"), Path("test.pdf"), 0, "6. Etkinlik Ad", {}, model, tmp_path))
    assert result["rows"] == result["columns"] == 1
    assert result["cells"][0]["text"] == "Ad"
    assert "> Tablo bağlamı: 6. Etkinlik" in candidate_markdown(result)
    assert validate_against_source(result, "6. Etkinlik Ad")["missing_numbers"] == []


def test_context_not_in_pdf_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(refinement, "PdfTableEvidence", Evidence)
    model = Model()
    model.context = ["99. Etkinlik"]
    result = asyncio.run(refinement.refine_table(table("Ad"), Path("test.pdf"), 0, "6. Etkinlik Ad", {}, model, tmp_path))
    assert not result.get("context_text")
    assert result["needs_review"] is True
    assert result["refinement"]["errors"]


def test_missing_geometry_never_counts_as_verified(tmp_path):
    original = table()
    result = asyncio.run(refinement.refine_table(original, tmp_path / "missing.pdf", 0, "Eşeysiz", {}, Model(), tmp_path / "audit"))
    assert result["cells"] == original["cells"]
    assert result["needs_review"] is True
    assert result["refinement"]["status"] == "unavailable"


def test_repeated_identical_words_keep_the_source_spelling():
    evidence = SimpleNamespace(words=Evidence.words * 2)
    targets, deferred = refinement.select_targets(table(), evidence, "Eşeysiz Eşeysiz")
    assert len(targets) == 1 and targets[0]["source"] == "Eşeysiz"


def test_neighbor_context_prevents_selecting_word_from_another_row():
    words = ["Adı", "Canlı", "Örneklerinin", "Tayini", "Amaç", "Çevresindeki", "bitki", "örneklerini", "tayin", "edebilme"]
    evidence = SimpleNamespace(words=[{"text": word, "normalized": refinement.normalized(word), "box": (0, 0, 5, 5)} for word in words])
    targets, _ = refinement.select_targets(table("Canlı Örneklerin Tayini"), evidence, " ".join(words))
    assert len(targets) == 1 and targets[0]["source"] == "Örneklerinin"


def test_turkish_uppercase_dotted_i_does_not_create_false_targets():
    evidence = SimpleNamespace(words=Evidence.words)
    targets, _ = refinement.select_targets(table("BİLEŞENLER"), evidence, "BİLEŞENLER")
    assert targets == []
