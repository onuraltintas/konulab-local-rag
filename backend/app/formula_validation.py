"""Formülü değiştirmeyen, kaynak kırpımı ve bağımsız okuma karşılaştırması."""
from collections import Counter
import hashlib
import json
from io import BytesIO
import math
import re
import unicodedata


def formula_signature(markdown):
    text = re.sub(r"```[\s\S]*?```|`[^`\n]+`", "", markdown)
    expressions = re.findall(r"\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)|(?<!\$)\$([^$\n]+)\$(?!\$)", text)
    return hashlib.sha256(json.dumps(expressions, ensure_ascii=False).encode()).hexdigest() if expressions else None


def formula_review_required(report, markdown):
    if any(f.get("validation", {}).get("needs_review", True) for f in report.get("formulas", [])):
        return True
    return formula_signature(markdown) != report.get("formula_signature")


def normalize_latex(value):
    value = unicodedata.normalize("NFKC", value).strip().strip("$")
    value = re.sub(r"\\(?:left|right|displaystyle|textstyle)\b", "", value)
    value = re.sub(r"\\(?:dfrac|tfrac)\b", r"\\frac", value)
    value = re.sub(r"\\(?:mid|vert)\b|\\\|", "|", value)
    value = re.sub(r"\\[,!;:]|\\ ", "", value)
    value = re.sub(r"\\(?:mathrm|mathit|mathbf)\{([A-Za-z])\}", r"\1", value)
    value = re.sub(r"([_^])\{([A-Za-z0-9])\}", r"\1\2", value)
    return re.sub(r"\s+", "", value).replace("−", "-")


def syntax_issues(value):
    depth = 0
    issues = []
    for match in re.finditer(r"(?<!\\)[{}]", value):
        depth += 1 if match.group() == "{" else -1
        if depth < 0:
            break
    if depth:
        issues.append("Dengesiz LaTeX parantezi")
    if re.search(r"\\(?:frac|sqrt)\s*\{\s*\}", value):
        issues.append("Boş kesir/kök bileşeni")
    if re.search(r"\\(?:input|include|href|html\w*|def|newcommand)\b", value):
        issues.append("Desteklenmeyen formül komutu")
    return issues


def source_symbols(latex):
    value = normalize_latex(latex).replace("\\pm", "±").replace("\\times", "×").replace("\\cdot", "·")
    value = re.sub(r"\\[A-Za-z]+", "", value)
    return Counter(re.findall(r"[A-Za-z0-9=+±−-]", value))


def inspect_text_symbols(latex, source):
    # PDF'nin iki boyutlu okuma sırası anlam eşitliğini kanıtlamaz. Yalnız
    # açık harf/rakam/işaret uyuşmazlıklarını işaretleriz; tahminle düzeltmeyiz.
    source = unicodedata.normalize("NFKC", source).replace("−", "-")
    observed = Counter(re.findall(r"[A-Za-z0-9=+±-]", source))
    expected = source_symbols(latex)
    if not observed or re.search(r"[\ue000-\uf8ff]", source):
        return {"status": "insufficient", "source_text": source}
    return {"status": "agrees" if observed == expected else "mismatch", "source_text": source}


def formula_crop(source_path, formula):
    import pypdfium2 as pdfium
    provenance = formula.get("provenance", [])
    if len(provenance) != 1:
        raise ValueError("Formül için tek kaynak konumu bulunamadı.")
    prov = provenance[0]
    with pdfium.PdfDocument(str(source_path)) as pdf:
        page = pdf[int(prov["page_no"]) - 1]
        textpage = page.get_textpage()
        try:
            width, height = page.get_width(), page.get_height()
            box = prov["bbox"]
            if box.get("coord_origin") == "TOPLEFT":
                left, bottom, right, top = box["l"], height - box["b"], box["r"], height - box["t"]
            else:
                left, bottom, right, top = box["l"], box["b"], box["r"], box["t"]
            left, bottom, right, top = max(0, left - 3), max(0, bottom - 3), min(width, right + 3), min(height, top + 3)
            if right <= left or top <= bottom:
                raise ValueError("Geçersiz formül kaynak konumu.")
            text = textpage.get_text_bounded(left=left, bottom=bottom, right=right, top=top)
            scale = min(4, math.sqrt(4_000_000 / ((right - left) * (top - bottom))))
            bitmap = page.render(scale=scale, crop=(left, bottom, width - right, height - top))
            try:
                stream = BytesIO()
                bitmap.to_pil().convert("RGB").save(stream, format="PNG")
                return stream.getvalue(), text
            finally:
                bitmap.close()
        finally:
            textpage.close()
            page.close()


async def validate_pdf_formulas(source_path, report, model, progress=lambda done, total: None):
    formulas = report.get("formulas", [])
    report["issues"] = [issue for issue in report.get("issues", []) if issue.get("type") != "formula_validation"]
    for index, formula in enumerate(formulas):
        latex = formula.get("latex", "")
        issues = syntax_issues(latex)
        validation = {"status": "review", "needs_review": True, "syntax_issues": issues,
                      "scope": "Kaynak okuma karşılaştırması; matematiksel doğruluk ispatı değildir."}
        if not latex:
            validation["reason"] = "Formül çıkarılamadı."
        else:
            try:
                image, text = formula_crop(source_path, formula)
                evidence = inspect_text_symbols(latex, text)
                validation["pdf_text"] = evidence
                # Taslak formülü VLM'ye vermiyoruz: kaynak görüntüyü bağımsız okur.
                reading = await model.read_formula(image)
                validation.update({"independent_latex": reading["latex"], "model": reading.get("model"), "readable": reading["readable"]})
                agrees = reading["readable"] and normalize_latex(latex) == normalize_latex(reading["latex"])
                if agrees and not issues and evidence["status"] != "mismatch":
                    validation.update(status="source_agreement", needs_review=False,
                                      reason="Bağımsız görsel okuma aynı ifadeyi verdi; kaynak metninde belirgin çelişki yok.")
                else:
                    validation["reason"] = "Kaynak metni, bağımsız okuma veya LaTeX yapısı uyuşmuyor; kaynakla karşılaştırın."
            except Exception as error:
                validation.update(reason="Kaynak konumu veya görsel modelle doğrulama tamamlanamadı.", error_type=type(error).__name__)
        formula["validation"] = validation
        if validation["needs_review"]:
            report["issues"].append({"type": "formula_validation", "pages": [p["page_no"] for p in formula.get("provenance", [])],
                                     "text": validation["reason"], "source_ref": formula.get("source_ref")})
        progress(index + 1, len(formulas))
    report["requires_review"] = bool(report.get("issues"))
