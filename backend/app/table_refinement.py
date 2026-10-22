"""PDF kaynak koordinatlarından yüksek çözünürlüklü, kanıtlı tablo düzeltmesi."""
import copy
import json
import math
import re
import csv
import io
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

from app.table_repair import validate_shape, validate_against_source


def normalized(text, aliases=None):
    text = text.translate(str.maketrans(aliases or {})).casefold().replace("\u0307", "").replace("µ", "μ")
    return re.sub(r"(?<=\w)[\-\u00ad\x02]\s*(?=\w)", "", text)


def tokens(text, aliases=None):
    return re.findall(r"[^\W\d_]+", normalized(text, aliases), re.UNICODE)


def reading_agrees(text, source, aliases=None):
    # Parantez/tırnak gibi kırpım kenarındaki noktalama harf farkı değildir.
    text = re.sub(r"^\W+|\W+$", "", text.strip())
    return normalized(text, aliases) == normalized(source, aliases)


def independent_ocr(image):
    """Generatif model uzlaşamazsa Türkçe OCR, ayrı bir kanıt olarak kullanılır."""
    binary = shutil.which("tesseract")
    if not binary:
        return None
    try:
        result = subprocess.run([binary, "stdin", "stdout", "-l", "tur+eng", "--psm", "7", "tsv"], input=image, capture_output=True, timeout=20, check=True)
        words = [row for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t") if row.get("level") == "5" and row.get("text", "").strip()]
        if not words or any(float(row["conf"]) < 85 for row in words):
            return None
        return {"text": " ".join(row["text"] for row in words), "confidence": min(float(row["conf"]) for row in words)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def bounds(box, height):
    if box.get("coord_origin") == "TOPLEFT":
        return box["l"], height - box["b"], box["r"], height - box["t"]
    return box["l"], box["b"], box["r"], box["t"]


class PdfTableEvidence:
    def __init__(self, source, table_index, directory, aliases):
        import pypdfium2 as pdfium
        structure = json.loads(Path(str(source) + ".structure.json").read_text(encoding="utf-8"))
        self.table = structure["tables"][table_index]
        provenance = self.table["prov"]
        if len(provenance) != 1:
            raise ValueError("Çok sayfalı tablo için tek kırpım güvenilir değil.")
        self.pdf = pdfium.PdfDocument(str(source))
        try:
            self.page = self.pdf[provenance[0]["page_no"] - 1]
            self.textpage = self.page.get_textpage()
            self.width, self.height = self.page.get_width(), self.page.get_height()
            self.box = bounds(provenance[0]["bbox"], self.height)
            self.directory = Path(directory)
            self.directory.mkdir(parents=True, exist_ok=True)
            self.aliases = aliases
            text, indices = "", []
            for index in range(self.textpage.count_chars()):
                char = self.textpage.get_text_range(index, 1)
                box = self.textpage.get_charbox(index)
                x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                if self.box[0] <= x <= self.box[2] and self.box[1] <= y <= self.box[3]:
                    text += char
                    indices.extend([index] * len(char))
                else:
                    text += " "
                    indices.append(index)
            self.words = []
            for match in re.finditer(r"[^\W\d_]+", text, re.UNICODE):
                boxes = [self.textpage.get_charbox(indices[i]) for i in range(match.start(), match.end())]
                box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
                self.words.append({"text": match.group(), "normalized": normalized(match.group(), aliases), "box": box})
        except Exception:
            self.close()
            raise

    def render(self, box, filename, scale=8, max_pixels=4_000_000, padding=3):
        left, bottom, right, top = box
        left, bottom = max(0, left - padding), max(0, bottom - padding)
        right, top = min(self.width, right + padding), min(self.height, top + padding)
        if right <= left or top <= bottom:
            raise ValueError("PDF kırpım koordinatları geçersiz.")
        scale = min(scale, math.sqrt(max_pixels / ((right - left) * (top - bottom))))
        bitmap = self.page.render(scale=scale, crop=(left, bottom, self.width - right, self.height - top))
        try:
            image = bitmap.to_pil().convert("RGB")
            if padding <= 1:
                from PIL import ImageOps
                image = ImageOps.expand(image, border=20, fill="white")
            path = self.directory / filename
            image.save(path, format="PNG")
            return path.read_bytes(), {"image": filename, "dpi": round(scale * 72), "box": list(box), "pixels": list(image.size)}
        finally:
            bitmap.close()

    def close(self):
        for key in ("textpage", "page", "pdf"):
            value = getattr(self, key, None)
            if value is not None:
                value.close()


def select_targets(candidate, evidence, source, aliases=None, limit=16):
    expected = set(tokens(source, aliases))
    targets, seen = [], set()
    for cell in candidate["cells"]:
        for match in re.finditer(r"[^\W\d_]+", cell["text"], re.UNICODE):
            word = normalized(match.group(), aliases)
            key = (cell["row"], cell["column"], word)
            if word in expected or key in seen or len(word) < 3:
                continue
            before = tokens(cell["text"][:match.start()], aliases)[-2:]
            after = tokens(cell["text"][match.end():], aliases)[:2]
            choices = []
            for index, item in enumerate(evidence.words):
                base = SequenceMatcher(None, word, item["normalized"]).ratio()
                preceding = {w["normalized"] for w in evidence.words[max(0, index-2):index]}
                following = {w["normalized"] for w in evidence.words[index+1:index+3]}
                context = len(set(before) & preceding) + len(set(after) & following)
                choices.append((base + .3 * context if base >= .55 else 0, item))
            score, closest = max(choices, key=lambda pair: pair[0], default=(0, None))
            if score < .55 or closest is None or word == closest["normalized"]:
                continue
            # Farklı sözcükler eşit olasılıklıysa konum tahmin edilmez. Aynı
            # sözcüğün tekrarında ise bağlamı en iyi uyan kırpım kullanılır.
            alternatives = [rank for rank, item in choices if item["normalized"] != closest["normalized"]]
            if alternatives and score - max(alternatives) < .06:
                continue
            seen.add(key)
            targets.append({"row": cell["row"], "column": cell["column"], "before": match.group(), "source": closest["text"], "box": closest["box"]})
    return targets[:limit], max(0, len(targets) - limit)


async def refine_table(candidate, source_path, table_index, source_text, aliases, model, directory, progress=lambda message: None):
    """Öneriyi düzeltir; yayınlanmış Markdown'a veya kaynak PDF'ye dokunmaz."""
    candidate = validate_shape(copy.deepcopy(candidate))
    audit = {"status": "processing", "cells": [], "context_added": [], "errors": []}
    evidence = None
    try:
        evidence = PdfTableEvidence(source_path, table_index, directory, aliases)
        targets, deferred = select_targets(candidate, evidence, source_text, aliases)
        audit["deferred_targets"] = deferred
        for number, target in enumerate(targets, 1):
            progress(f"Yüksek çözünürlüklü hücre kontrolü: {number}/{len(targets)}.")
            image, crop = evidence.render(target["box"], f"cell-{number:03}.png", padding=3)
            entry = {**target, **crop, "accepted": False}
            try:
                reading = await model.read_table_word(image)
                if not reading["readable"] or not reading_agrees(reading["text"], target["source"], aliases):
                    # Önceki yanlış okumayı veya PDF cevabını modele göstermeden
                    # ikinci bağımsız yakın plan okuma yap.
                    second_image, second_crop = evidence.render(target["box"], f"cell-{number:03}-retry.png", scale=10, padding=1)
                    entry["first_reading"] = reading
                    reading = await model.read_table_word(second_image)
                    entry["retry_crop"] = second_crop
                read = reading["text"].strip()
                agreed = reading["readable"] and reading_agrees(read, target["source"], aliases)
                entry["verification_method"] = "pdf_text+vision"
                if not agreed:
                    ocr = independent_ocr(second_image)
                    entry["ocr_reading"] = ocr
                    if ocr and reading_agrees(ocr["text"], target["source"], aliases):
                        agreed = True
                        entry["verification_method"] = "pdf_text+turkish_ocr"
                # Görsel model ve bağımsız PDF metni aynı sözcükte uzlaşmalı.
                # Alternatif: yüksek güvenli OCR ile PDF metninin uzlaşması.
                if agreed:
                    for cell in candidate["cells"]:
                        if cell["row"] == target["row"] and cell["column"] == target["column"]:
                            cell["text"] = re.sub(r"(?<!\w)" + re.escape(target["before"]) + r"(?!\w)", lambda _: target["source"], cell["text"])
                            entry.update(accepted=True, after=target["source"])
                            break
                entry["reading"] = reading
            except Exception as error:
                entry["error"] = str(error)[:250]
            audit["cells"].append(entry)
        progress("Tablo başlıkları, birleşik hücreler ve kaynak kapsamı yeniden doğrulanıyor.")
        image, crop = evidence.render(evidence.box, "table-review.png", scale=5)
        review = await model.review_table_structure(image, candidate)
        audit["structure_review"] = {**review, **crop}
        context = []
        for text in review.get("context_text", []):
            # Başlık/kenar notu ancak kaynakta aynen varsa korunabilir.
            compact = lambda value: re.sub(r"\s+", " ", normalized(value, aliases)).strip()
            if compact(text) and compact(text) in compact(source_text):
                context.append(text.strip())
            else:
                audit["errors"].append("Tablo çevresi açıklaması PDF metninde doğrulanamadı.")
        if context:
            candidate["context_text"] = list(dict.fromkeys(context))
            audit["context_added"] = candidate["context_text"]
        known = set(tokens(source_text, aliases))
        unresolved = sorted({word for cell in candidate["cells"] for word in tokens(cell["text"], aliases) if len(word) >= 3 and word not in known})
        audit["unresolved_words"] = unresolved[:100]
        if not known:
            audit["errors"].append("PDF metin katmanı yok; bağımsız sözcük doğrulaması yapılamadı.")
        candidate["needs_review"] = bool(not review["structure_correct"] or review["needs_review"] or review["issues"] or deferred or unresolved or audit["errors"] or any(not cell["accepted"] for cell in audit["cells"]))
        validation = validate_against_source(candidate, source_text, aliases)
        candidate["needs_review"] |= validation["status"] != "passed" or validation["word_recall"] < 1 or validation["word_precision"] < 1
        audit["status"] = "confirmed" if not candidate["needs_review"] else "manual_review"
    except Exception as error:
        # Eski öneri ve orijinal kaynak daima korunur; eksik doğrulama başarı değildir.
        audit["status"] = "unavailable"
        audit["errors"].append(str(error)[:500])
        candidate["needs_review"] = True
    finally:
        if evidence is not None:
            evidence.close()
    candidate["refinement"] = audit
    validate_shape(candidate)
    Path(directory).mkdir(parents=True, exist_ok=True)
    Path(directory, "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidate
