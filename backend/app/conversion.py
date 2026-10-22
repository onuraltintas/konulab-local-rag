"""Ofis belgelerini doğrulanabilir Markdown'a dönüştürme sınırı.

Bu modül özellikle istemciye veya LLM'e belge yorumlatmaz: Docling'in yapısal
çıktısını üretir, kalite eşiğini denetler ve kalan yayın kararını API katmanına
bırakır. Böylece ham dosya ile öğrenme haritası arasındaki sınır nettir.
"""

from dataclasses import dataclass, field
from io import BytesIO
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
import re
import shutil
import subprocess

from app.conversion_tables import render_table, inspect_pdf_table
from app.conversion_fonts import infer_font_aliases


SUPPORTED_CONVERTIBLE_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx"}
_MEANINGFUL_TEXT = re.compile(r"[\wçğıöşüÇĞİÖŞÜ]")
_MARKDOWN_TABLE = re.compile(r"(?m)(?:^\|.*\|\s*$\n?){2,}")


class ConversionUnavailableError(RuntimeError):
    """Docling kurulu değilse ya da dönüştürücü başlatılamazsa verilen güvenli hata."""


class ConversionQualityError(RuntimeError):
    """Dönüşüm boş ya da kullanıcı incelemesine değmeyecek kadar yetersizse verilen hata."""


@dataclass(frozen=True)
class ConvertedContent:
    markdown: str
    converter: str
    converter_version: str | None
    ocr_used: bool
    page_count: int | None
    table_count: int
    image_count: int
    assets: tuple["ConvertedAsset", ...] = field(default_factory=tuple)
    source_structure: dict = field(default_factory=dict)
    quality_report: dict = field(default_factory=dict)
    source_pages: tuple[bytes, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ConvertedAsset:
    """Markdown ile birlikte saklanacak küçük, güvenli bir görsel türevi."""

    kind: str
    page_number: int | None
    content: bytes
    structured_markdown: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    table_index: int | None = None


def _docling_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:
        return None


def _build_converter(*, use_ocr: bool):
    """PDF için tablo yapısını koruyan, gerektiğinde OCR açılan dönüştürücü."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as error:
        raise ConversionUnavailableError(
            "Belge dönüştürücü henüz kurulu değil. Sunucuya Docling bileşeni eklenmelidir."
        ) from error

    pdf_options = PdfPipelineOptions()
    pdf_options.do_ocr = use_ocr
    pdf_options.do_table_structure = True
    pdf_options.do_formula_enrichment = True
    # Görseller ilk aşamada yalnızca saklanır. VLM açıklaması ayrı, açıkça
    # seçilmiş bir işlem olacağından her görseli model bağlamına sokmayız.
    pdf_options.generate_picture_images = True
    pdf_options.generate_table_images = True
    pdf_options.images_scale = 1.5
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
    )


def _counts(document: object) -> tuple[int | None, int, int]:
    """Docling sürümleri arasındaki küçük model farklarını güvenle tolere eder."""
    pages = getattr(document, "pages", None)
    page_count = len(pages) if pages is not None else None
    items = list(getattr(document, "iterate_items", lambda: [])())
    table_count = sum(item.__class__.__name__ == "TableItem" for item, *_ in items)
    image_count = sum(item.__class__.__name__ in {"PictureItem", "FigureItem"} for item, *_ in items)
    return page_count, table_count, image_count


def _item_text(item: object) -> str:
    cells = getattr(getattr(item, "data", None), "table_cells", None)
    return "\n".join(cell.text for cell in cells) if cells is not None else (getattr(item, "text", "") or "")


def _table_dimensions(markdown: str) -> tuple[int, int]:
    rows = [line.strip() for line in markdown.splitlines() if line.strip().startswith("|")]
    data_rows = [row for row in rows if not re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?", row)]
    columns = max((len([cell for cell in row.strip("|").split("|")]) for row in data_rows), default=0)
    return max(0, len(data_rows) - 1), columns


def _extract_markdown_tables(markdown: str) -> list[str]:
    return [match.group(0).strip() for match in _MARKDOWN_TABLE.finditer(markdown)]


def _extract_assets(document: object, table_markdowns: list[str], table_blocks: dict | None = None, source_path: Path | None = None) -> tuple[ConvertedAsset, ...]:
    if source_path is not None and source_path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(str(source_path)) as pdf:
            @lru_cache(maxsize=2)
            def source_page(number):
                page = pdf[number - 1]
                try:
                    return page.render(scale=1.5).to_pil().convert("RGB"), page.get_height()
                finally:
                    page.close()
            return _collect_assets(document, table_markdowns, table_blocks, source_page)
    return _collect_assets(document, table_markdowns, table_blocks)


def _collect_assets(document, table_markdowns, table_blocks=None, source_page=None):
    """Tablo ve şekilleri OCR/LLM'den bağımsız, sınırlı PNG türevleri olarak alır."""
    assets: list[ConvertedAsset] = []
    table_index = 0
    for item, *_ in getattr(document, "iterate_items", lambda: [])():
        class_name = item.__class__.__name__
        if class_name not in {"PictureItem", "FigureItem", "TableItem"}:
            continue
        # Markdown tabloları Docling'in belge sırasındadır. Görsel türevi
        # alınamayan bir tablo da bu sırayı tüketmelidir; aksi halde sonraki
        # tablo yanlış Markdown yapısıyla eşleşir.
        structured_markdown = None
        row_count = None
        column_count = None
        if class_name == "TableItem":
            if table_blocks is not None:
                structured_markdown = table_blocks.get(getattr(item, "self_ref", ""))
                row_count = max(0, getattr(item.data, "num_rows", 1) - 1)
                column_count = getattr(item.data, "num_cols", 0)
            elif table_index < len(table_markdowns):
                structured_markdown = table_markdowns[table_index]
                row_count, column_count = _table_dimensions(structured_markdown)
            table_index += 1
        try:
            provenance = getattr(item, "prov", []) or []
            if source_page is not None and provenance:
                page_image, height = source_page(provenance[0].page_no)
                bbox = provenance[0].bbox.to_bottom_left_origin(height)
                image = page_image.crop((round(bbox.l * 1.5), round((height - bbox.t) * 1.5), round(bbox.r * 1.5), round((height - bbox.b) * 1.5)))
            else:
                image = item.get_image(document)
            if image is None:
                continue
            image.thumbnail((2_000, 2_000))
            stream = BytesIO()
            image.save(stream, format="PNG", optimize=True)
            content = stream.getvalue()
            # Bozuk/çok büyük varlık, işçiyi ve yedekleri şişirmemelidir.
            if not content or len(content) > 8 * 1024 * 1024:
                continue
            provenance = getattr(item, "prov", []) or []
            page_number = getattr(provenance[0], "page_no", None) if provenance else None
            assets.append(
                ConvertedAsset(
                    kind="table" if class_name == "TableItem" else "picture",
                    page_number=int(page_number) if page_number is not None else None,
                    content=content,
                    structured_markdown=structured_markdown,
                    row_count=row_count,
                    column_count=column_count,
                    table_index=table_index - 1 if class_name == "TableItem" else None,
                )
            )
        except Exception:
            # Görsel dışa aktarılamasa bile metin dönüşümü geçerliliğini korur.
            continue
    return tuple(assets)


def _convert_once(source_path: Path, *, use_ocr: bool) -> ConvertedContent:
    converter = _build_converter(use_ocr=use_ocr)
    try:
        result = converter.convert(str(source_path))
        document = result.document
        markdown = document.export_to_markdown().strip()
    except Exception as error:
        raise ConversionQualityError("Belge Docling ile Markdown'a dönüştürülemedi.") from error
    page_count, table_count, image_count = _counts(document)
    structure = document.export_to_dict() if hasattr(document, "export_to_dict") else {}
    private_pages = {prov.page_no for item, *_ in document.iterate_items() if re.search(r"[\ue000-\uf8ff]", _item_text(item)) for prov in (getattr(item, "prov", []) or [])}
    verified_mapping = infer_font_aliases(source_path, private_pages) if source_path.suffix.lower() == ".pdf" and private_pages else {}
    def normalize_font(text):
        return text.translate(str.maketrans(verified_mapping))
    corrections = sum(markdown.count(char) for char in verified_mapping)
    if verified_mapping:
        markdown = normalize_font(markdown)
    spacing_repairs = []
    if source_path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(str(source_path)) as pdf:
                for item, *_ in document.iterate_items():
                    original = getattr(item, "text", "") or ""
                    if not re.search(r"\w [çğıöşüÇĞİÖŞÜ] \w|[\ue000-\uf8ff]", original):
                        continue
                    provenance = getattr(item, "prov", []) or []
                    if not provenance or len({prov.page_no for prov in provenance}) != 1:
                        continue
                    page_number = provenance[0].page_no
                    page = pdf[page_number - 1]
                    textpage = page.get_textpage()
                    try:
                        independent = textpage.get_text_bounded()
                    finally:
                        textpage.close()
                        page.close()
                    replacement = verified_spacing(original, independent, verified_mapping)
                    independent_sources = [independent]
                    if shutil.which("pdftotext"):
                        alternative = subprocess.run(["pdftotext", "-f", str(page_number), "-l", str(page_number), "-layout", str(source_path), "-"], capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
                        independent_sources.append(alternative)
                        alternate_repair = verified_spacing(original, alternative, verified_mapping)
                        def suspicious(value):
                            return len(re.findall(r"\w [çğıöşüÇĞİÖŞÜ] \w", value or original))
                        if alternate_repair and suspicious(alternate_repair) < suspicious(replacement):
                            replacement = alternate_repair
                    # Aynı paragraf iki sütunu kapsıyorsa cümleleri bağımsız
                    # eşleştir; sayfanın okuma sırasını tahmin ederek değiştirme.
                    parts = []
                    for part in re.findall(r"[^;.!?:]+[;.!?:]?", original):
                        options = [verified_spacing(part, source, verified_mapping) for source in independent_sources]
                        options = [option for option in options if option]
                        if options:
                            best = min(options, key=lambda value: len(re.findall(r"\w [çğıöşüÇĞİÖŞÜ] \w", value)))
                            prefix = part[:len(part) - len(part.lstrip())]
                            parts.append(prefix + best)
                        else:
                            parts.append(normalize_font(part))
                    segmented = "".join(parts)
                    if segmented and len(re.findall(r"\w [çğıöşüÇĞİÖŞÜ] \w", segmented)) < len(re.findall(r"\w [çğıöşüÇĞİÖŞÜ] \w", replacement or original)):
                        replacement = segmented
                    if replacement and replacement != original:
                        markdown = markdown.replace(normalize_font(original), replacement, 1)
                        spacing_repairs.append({"page": page_number, "source_ref": getattr(item, "self_ref", None), "before": original, "after": replacement})
        except (ImportError, OSError, ValueError, subprocess.SubprocessError) as error:
            spacing_repairs.append({"unavailable": type(error).__name__})
    table_blocks = {}
    table_exports = []
    table_issues = []
    table_cursor = 0
    for index, table in enumerate(getattr(document, "tables", [])):
        raw = table.export_to_markdown(doc=document).strip()
        pages = [prov.page_no for prov in table.prov]
        validation = inspect_pdf_table(source_path, table, verified_mapping) if source_path.suffix.lower() == ".pdf" else {}
        fallback = bool(validation.get("mismatched_cells"))
        try:
            rendered = render_table(table.data.model_dump(mode="json"), is_index=str(table.label) == "document_index")
        except ValueError:
            rendered = raw
            table_issues.append({"type": "table_structure", "pages": pages, "text": "Tablo hücre yerleşimi kaynak görselden kontrol edilmeli."})
            fallback = True
        if fallback:
            source_text = validation.get("source_text", "")
            source_content = "\n".join("> " + line for line in source_text.splitlines()) if source_text.strip() else rendered
            rendered = "> Bu tablonun hücre eşleşmesi doğrulanamadı. Özgün tablo görseli esas alınmalıdır.\n\n" + source_content
            table_issues.append({"type": "table_structure", "pages": pages, "text": "Şüpheli hücre tablosu yerine özgün PDF görseli ve kaynak metni korundu."})
        if verified_mapping:
            rendered = normalize_font(rendered)
        marker = f"<!-- konulab-table:{index} -->"
        source_table = normalize_font(raw)
        position = markdown.find(source_table, table_cursor) if raw else -1
        if position >= 0:
            framed = f"<!-- konulab-table-body:{index}:start -->\n\n{rendered}\n\n<!-- konulab-table-body:{index}:end -->\n\n{marker}"
            markdown = markdown[:position] + framed + markdown[position + len(source_table):]
            table_cursor = position + len(framed)
        else:
            table_issues.append({"type": "table_structure", "pages": pages, "text": "Tablo konumu Markdown ile eşleştirilemedi."})
        table_blocks[table.self_ref] = rendered
        table_exports.append({"index": index, "pages": pages, "source_ref": table.self_ref, "html": table.export_to_html(doc=document, add_caption=False) if not fallback else "", "marker": marker, "source_image_fallback": fallback, "validation": validation})
    assets = _extract_assets(document, _extract_markdown_tables(markdown), table_blocks, source_path)
    issues = table_issues.copy()
    formula_report = []
    for item, *_ in document.iterate_items():
        label = getattr(getattr(item, "label", None), "value", getattr(item, "label", None))
        if label != "formula":
            continue
        decoded = getattr(item, "text", "") or ""
        original = getattr(item, "orig", "") or ""
        formula_report.append({"source_ref": getattr(item, "self_ref", None), "decoded": bool(decoded), "latex": decoded,
                               "provenance": [prov.model_dump(mode="json") for prov in (getattr(item, "prov", []) or [])]})
        if not decoded:
            # Docling'in boş formül işaretçisinde kaynak ifade sessizce kaybolmasın.
            fallback = f"[Formül okunamadı; PDF kaynağını kontrol edin.] {original}"
            markdown = markdown.replace("<!-- formula-not-decoded -->", fallback, 1)
            issues.append({"type": "formula_not_decoded", "source_ref": getattr(item, "self_ref", None), "text": original[:300], "pages": [prov.page_no for prov in (getattr(item, "prov", []) or [])]})
    repaired_refs = {repair.get("source_ref") for repair in spacing_repairs}
    for item, *_ in document.iterate_items():
        text = _item_text(item)
        pages = [getattr(prov, "page_no", None) for prov in (getattr(item, "prov", []) or [])]
        if verified_mapping:
            text = normalize_font(text)
        if getattr(item, "self_ref", None) in repaired_refs:
            text = next(repair["after"] for repair in spacing_repairs if repair.get("source_ref") == item.self_ref)
        if re.search(r"[\ue000-\uf8ff]", text):
            issues.append({"type": "font_mapping", "pages": pages, "text": text[:300]})
        if re.search(r"\w [çğıöşüÇĞİÖŞÜ] \w", text):
            issues.append({"type": "suspicious_spacing", "pages": pages, "text": text[:300]})
    expected = table_count + image_count
    if len(assets) != expected:
        issues.append({"type": "asset_coverage", "expected": expected, "exported": len(assets)})
    source_pages = []
    if source_path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(str(source_path)) as pdf:
            for number in range(len(pdf)):
                page = pdf[number]
                try:
                    page_image = page.render(scale=1.5).to_pil().convert("RGB")
                    stream = BytesIO()
                    page_image.save(stream, format="PNG", optimize=True)
                    source_pages.append(stream.getvalue())
                finally:
                    page.close()
    return ConvertedContent(
        markdown=markdown,
        converter="docling",
        converter_version=_docling_version(),
        ocr_used=use_ocr,
        page_count=page_count,
        table_count=table_count,
        image_count=image_count,
        assets=assets,
        source_structure=structure,
        source_pages=tuple(source_pages),
        quality_report={"formulas": formula_report, "corrected_font_characters": corrections, "font_aliases": verified_mapping, "spacing_repairs": spacing_repairs, "tables": table_exports, "source_page_count": len(source_pages), "issues": issues, "requires_review": bool(issues)},
    )


def verified_spacing(original: str, independent: str, repair_font: dict | None = None) -> str | None:
    """Yalnızca aynı harf dizisi bağımsız PDF çıkarımında varsa boşlukları düzeltir."""
    def canonical(text):
        return text.translate(str.maketrans(repair_font or {}))
    independent = canonical(independent)
    positions = [index for index, char in enumerate(independent) if not char.isspace()]
    compact = "".join(independent[index] for index in positions).replace("’", "'")
    needle = re.sub(r"\s+", "", canonical(original)).replace("’", "'")
    start = compact.find(needle)
    if not needle:
        return None
    if start < 0:
        # Çok sütunlu sayfada paragraf sırası farklı olabilir. Yalnız birebir
        # eşleşen uzun harf aralıklarının boşluklarını bağımsız kaynaktan al.
        original = canonical(original)
        original_positions = [index for index, char in enumerate(original) if not char.isspace()]
        output = original
        matches = SequenceMatcher(None, needle, compact, autojunk=False).get_matching_blocks()
        changed = False
        for match in reversed(matches):
            if match.size < 24:
                continue
            a = original_positions[match.a]
            b = original_positions[match.a + match.size - 1] + 1
            replacement = re.sub(r"\s+", " ", independent[positions[match.b]:positions[match.b + match.size - 1] + 1])
            output = output[:a] + replacement + output[b:]
            changed = True
        return output if changed else None
    if compact.find(needle, start + 1) >= 0:
        return None
    return re.sub(r"\s+", " ", independent[positions[start]:positions[start + len(needle) - 1] + 1]).strip()


def convert_to_markdown(source_path: Path) -> ConvertedContent:
    """Belgeyi dönüştürür; PDF metni seyrekse yalnız o zaman OCR ile yineler.

    OCR için ikinci geçiş, dijital metinli PDF'lerde hem süreyi hem de yanlış
    karakter riskini azaltır. Çıktı yeterince anlamlı değilse yayınlanmak üzere
    boş Markdown üretmek yerine işlem güvenli biçimde başarısız olur.
    """
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_CONVERTIBLE_SUFFIXES:
        raise ConversionQualityError("Bu dosya türü dönüştürme modülünde desteklenmiyor.")
    converted = _convert_once(source_path, use_ocr=False)
    meaningful_chars = len(_MEANINGFUL_TEXT.findall(converted.markdown))
    if suffix == ".pdf" and meaningful_chars < 200:
        converted = _convert_once(source_path, use_ocr=True)
        meaningful_chars = len(_MEANINGFUL_TEXT.findall(converted.markdown))
    if meaningful_chars < 40:
        raise ConversionQualityError(
            "Dönüşümden yeterli metin alınamadı. Dosyayı ya da tarama kalitesini kontrol edin."
        )
    return converted
