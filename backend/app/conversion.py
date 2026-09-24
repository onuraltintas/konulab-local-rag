"""Ofis belgelerini doğrulanabilir Markdown'a dönüştürme sınırı.

Bu modül özellikle istemciye veya LLM'e belge yorumlatmaz: Docling'in yapısal
çıktısını üretir, kalite eşiğini denetler ve kalan yayın kararını API katmanına
bırakır. Böylece ham dosya ile öğrenme haritası arasındaki sınır nettir.
"""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import re


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


@dataclass(frozen=True)
class ConvertedAsset:
    """Markdown ile birlikte saklanacak küçük, güvenli bir görsel türevi."""

    kind: str
    page_number: int | None
    content: bytes
    structured_markdown: str | None = None
    row_count: int | None = None
    column_count: int | None = None


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


def _table_dimensions(markdown: str) -> tuple[int, int]:
    rows = [line.strip() for line in markdown.splitlines() if line.strip().startswith("|")]
    data_rows = [row for row in rows if not re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?", row)]
    columns = max((len([cell for cell in row.strip("|").split("|")]) for row in data_rows), default=0)
    return max(0, len(data_rows) - 1), columns


def _extract_markdown_tables(markdown: str) -> list[str]:
    return [match.group(0).strip() for match in _MARKDOWN_TABLE.finditer(markdown)]


def _extract_assets(document: object, table_markdowns: list[str]) -> tuple[ConvertedAsset, ...]:
    """Tablo ve şekilleri OCR/LLM'den bağımsız, sınırlı PNG türevleri olarak alır."""
    assets: list[ConvertedAsset] = []
    table_index = 0
    for item, *_ in getattr(document, "iterate_items", lambda: [])():
        class_name = item.__class__.__name__
        if class_name not in {"PictureItem", "TableItem"}:
            continue
        if len(assets) >= 100:
            break
        # Markdown tabloları Docling'in belge sırasındadır. Görsel türevi
        # alınamayan bir tablo da bu sırayı tüketmelidir; aksi halde sonraki
        # tablo yanlış Markdown yapısıyla eşleşir.
        structured_markdown = None
        row_count = None
        column_count = None
        if class_name == "TableItem":
            if table_index < len(table_markdowns):
                structured_markdown = table_markdowns[table_index]
                row_count, column_count = _table_dimensions(structured_markdown)
            table_index += 1
        try:
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
    return ConvertedContent(
        markdown=markdown,
        converter="docling",
        converter_version=_docling_version(),
        ocr_used=use_ocr,
        page_count=page_count,
        table_count=table_count,
        image_count=image_count,
        assets=_extract_assets(document, _extract_markdown_tables(markdown)),
    )


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
