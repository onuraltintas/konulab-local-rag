"""Tablo hücrelerini kaybetmeden, birleşik hücreleri tekrar etmeden dışa aktarır."""
import re
from pathlib import Path


def inspect_pdf_table(source: Path, table, font_aliases: dict | None = None) -> dict:
    """Her dolu hücrenin harf/rakam dizisini kendi PDF konumuyla karşılaştırır."""
    import pypdfium2 as pdfium
    def compact(text):
        return re.sub(r"[\W_]+", "", text.translate(str.maketrans(font_aliases or {})).casefold())
    with pdfium.PdfDocument(str(source)) as pdf:
        page = pdf[table.prov[0].page_no - 1]
        textpage = page.get_textpage()
        try:
            mismatches = []
            checked = 0
            for number, cell in enumerate(table.data.table_cells):
                if not compact(cell.text):
                    continue
                checked += 1
                if cell.bbox is None:
                    mismatches.append(number)
                    continue
                box = cell.bbox.to_bottom_left_origin(page.get_height())
                extracted = textpage.get_text_bounded(left=box.l - 1, bottom=box.b - 1, right=box.r + 1, top=box.t + 1)
                if compact(cell.text) not in compact(extracted):
                    mismatches.append(number)
            box = table.prov[0].bbox.to_bottom_left_origin(page.get_height())
            raw = textpage.get_text_bounded(left=box.l, bottom=box.b, right=box.r, top=box.t)
            raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
            # Yalnız mevcut hücreleri denetlemek, modelin tamamen atladığı
            # sözcükleri yakalamaz. Kaynak bölgenin metin kapsamını da ölç.
            source_compact = compact(raw)
            cells_compact = compact("".join(cell.text for cell in table.data.table_cells))
            def grams(text):
                return {text[index:index + 5] for index in range(max(0, len(text) - 4))}
            source_grams, cell_grams = grams(source_compact), grams(cells_compact)
            coverage = len(source_grams & cell_grams) / len(source_grams) if source_grams else 1.0
            if len(source_compact) > 30 and coverage < 0.97:
                mismatches.append(-1)  # Kaynaktaki metnin bir bölümü hücrelerde yok.
            if not checked:
                mismatches.append(-2)  # Tamamen boş çıkarım, başarı sayılmaz.
            return {"checked_cells": checked, "mismatched_cells": mismatches, "source_text_coverage": round(coverage, 4), "source_text": raw}
        finally:
            textpage.close()
            page.close()


def render_table(data: dict, *, is_index: bool = False) -> str:
    rows, columns = data.get("num_rows", 0), data.get("num_cols", 0)
    grid = [["" for _ in range(columns)] for _ in range(rows)]
    for cell in data.get("table_cells", []):
        row, column = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
        if not (0 <= row < rows and 0 <= column < columns):
            raise ValueError("Tablo hücresi sınırların dışında.")
        text = re.sub(r"\s+", " ", cell.get("text", "")).strip()
        if grid[row][column]:
            raise ValueError("Tablo hücreleri aynı konuma yazılıyor.")
        grid[row][column] = text
        # Satır başlığı sonraki satırlarda anlam bağını korur; yatay birleşik
        # hücreler ise aynı metni farklı sütunlarda tekrar üretmez.
        if cell.get("row_span", 1) > 1:
            for target in range(row + 1, min(rows, cell["end_row_offset_idx"])):
                grid[target][column] = text
    if is_index:
        lines = []
        for row in grid:
            text = " ".join(value for value in row if value)
            text = re.sub(r"\.{2,}", " — ", text)
            text = re.sub(r"(?<=\d)\s+(?=\d\s*$)", "", text)
            lines.append("- " + re.sub(r"\s+", " ", text).strip())
        return "\n".join(lines)
    def line(row):
        return "| " + " | ".join(value.replace("|", "\\|") for value in row) + " |"
    if not grid:
        return ""
    return "\n".join([line(grid[0]), "| " + " | ".join("---" for _ in range(columns)) + " |", *(line(row) for row in grid[1:])])
