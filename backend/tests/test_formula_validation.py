import asyncio
from pathlib import Path

from app import formula_validation as module


def test_formula_normalization_keeps_fraction_grouping_and_signs():
    assert module.normalize_latex(r"\left( \dfrac {a} {b} \right)") == r"(\frac{a}{b})"
    assert module.normalize_latex(r"\frac{ab}{c}") != module.normalize_latex(r"\frac{a}{bc}")
    assert module.normalize_latex("x=-2") != module.normalize_latex("x=2")
    assert module.syntax_issues(r"\frac{a}{b")
    assert module.syntax_issues(r"\sqrt{}")
    assert module.syntax_issues(r"\href{x}{y}")


def test_pdf_text_symbol_mismatch_detects_missing_minus_and_exponent():
    assert module.inspect_text_symbols("x=-2", "x=2")["status"] == "mismatch"
    assert module.inspect_text_symbols("b^2-4ac", "b-4ac")["status"] == "mismatch"
    assert module.inspect_text_symbols("x=-2", "x = −2")["status"] == "agrees"
    assert module.inspect_text_symbols("x=2", "")["status"] == "insufficient"


def test_independent_reading_agreement_and_disagreement_preserve_source(monkeypatch):
    monkeypatch.setattr(module, "formula_crop", lambda path, formula: (b"png", "x = -2"))
    class Model:
        async def read_formula(self, image):
            return {"latex": "x=-2", "readable": True, "model": "test"}
    report = {"formulas": [{"latex": "x=-2", "source_ref": "#/texts/1", "provenance": [{"page_no": 1}]}]}
    asyncio.run(module.validate_pdf_formulas(Path("test.pdf"), report, Model()))
    assert report["formulas"][0]["validation"]["status"] == "source_agreement"
    report["formulas"][0]["latex"] = "x=2"
    asyncio.run(module.validate_pdf_formulas(Path("test.pdf"), report, Model()))
    assert report["formulas"][0]["latex"] == "x=2"
    assert report["formulas"][0]["validation"]["needs_review"] is True
    assert len(report["issues"]) == 1


def test_model_unavailable_is_review_not_conversion_failure(monkeypatch):
    monkeypatch.setattr(module, "formula_crop", lambda path, formula: (b"png", "x=2"))
    class Model:
        async def read_formula(self, image):
            raise RuntimeError("offline")
    report = {"formulas": [{"latex": "x=2", "provenance": []}]}
    asyncio.run(module.validate_pdf_formulas(Path("test.pdf"), report, Model()))
    assert report["requires_review"]
    assert report["formulas"][0]["validation"]["error_type"] == "RuntimeError"


def test_edit_invalidates_formula_validation_but_non_math_edit_does_not():
    markdown = r"Örnek: $$x=\frac{a}{b}$$"
    report = {"formula_signature": module.formula_signature(markdown), "formulas": [{"validation": {"needs_review": False}}]}
    assert not module.formula_review_required(report, markdown + "\nYeni paragraf")
    assert module.formula_review_required(report, markdown.replace("{b}", "{c}"))
    assert module.formula_signature("`$kod$`\n```\n$$kod$$\n```") is None


def test_crop_handles_both_coordinate_origins(tmp_path):
    import pypdfium2 as pdfium
    # Gerçek PDFium koordinat/kırpım testi, model veya dış bağlantı gerektirmez.
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(300, 400)
    path = tmp_path / "crop.pdf"
    pdf.save(path)
    page.close()
    pdf.close()
    for box in ({"l": 10, "b": 300, "r": 150, "t": 350, "coord_origin": "BOTTOMLEFT"},
                {"l": 10, "t": 50, "r": 150, "b": 100, "coord_origin": "TOPLEFT"}):
        data, text = module.formula_crop(path, {"provenance": [{"page_no": 1, "bbox": box}]})
        assert data.startswith(b"\x89PNG")
