"""Görsel modelin tablo önerilerini kaynak veriden bağımsız güven puanıyla kabul etmez."""
import re
from collections import Counter
from html import escape
from app.conversion_tables import render_table


def validate_shape(result: dict) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Tablo çıktısı JSON nesnesi olmalı.")
    rows, columns = result.get("rows"), result.get("columns")
    if type(rows) is not int or type(columns) is not int or not 1 <= rows <= 200 or not 1 <= columns <= 40:
        raise ValueError("Tablo boyutları geçersiz.")
    grid = result.get("grid")
    if grid is not None and "cells" not in result:
        if not isinstance(grid, list) or not grid or any(not isinstance(row, list) or not row or any(not isinstance(text, str) for text in row) for row in grid):
            raise ValueError("Tablo ızgarası eksik veya kesilmiş.")
        if len({len(row) for row in grid}) != 1:
            raise ValueError(f"Tablo ızgarası eksik: satırların sütun sayıları farklı ({[len(row) for row in grid][:40]}).")
        observed = (len(grid), len(grid[0]))
        if observed != (rows, columns):
            if observed[0] > 200 or observed[1] > 40:
                raise ValueError("Tablo boyutları geçersiz.")
            # Modelin sayma metadatasına güvenme. Dikdörtgen çıktıdaki gerçek
            # hücreleri aynen koru; satır ekleme/silme yapma. Bu durumda tam
            # kaynak eşleşse bile otomatik doğrulanmış sayılmaz.
            result["shape_adjustment"] = {"declared": [rows, columns], "observed": list(observed)}
            rows, columns = observed
            result.update(rows=rows, columns=columns, needs_review=True)
        result["cells"] = [{"row": row, "column": col, "rowspan": 1, "colspan": 1, "text": text} for row, values in enumerate(grid) for col, text in enumerate(values)]
    cells = result.get("cells")
    if not isinstance(cells, list) or not cells or len(cells) > 8000:
        raise ValueError("Tablo hücreleri eksik.")
    # Hücre koordinatları tam bir ızgara oluşturduğu halde yalnız üst boyut
    # metadatası küçük verilmiş olabilir. Değer eklemeden boyutu büyüt;
    # aşağıdaki kapsam/çakışma denetimi yine zorunludur ve inceleme işaretlenir.
    if all(isinstance(cell, dict) and all(type(cell.get(key, 1)) is int for key in ("row", "column", "rowspan", "colspan")) and "row" in cell and "column" in cell for cell in cells):
        observed_rows = max(cell["row"] + cell.get("rowspan", 1) for cell in cells)
        observed_columns = max(cell["column"] + cell.get("colspan", 1) for cell in cells)
        if (observed_rows > rows or observed_columns > columns) and observed_rows <= 200 and observed_columns <= 40:
            result["shape_adjustment"] = {"declared": [rows, columns], "observed": [max(rows, observed_rows), max(columns, observed_columns)]}
            rows, columns = max(rows, observed_rows), max(columns, observed_columns)
            result.update(rows=rows, columns=columns, needs_review=True)
    occupied = set()
    total_text = 0
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("Tablo hücresi geçersiz.")
        row, col = cell.get("row"), cell.get("column")
        rowspan, colspan = cell.get("rowspan", 1), cell.get("colspan", 1)
        if any(type(value) is not int for value in (row, col, rowspan, colspan)) or row < 0 or col < 0 or rowspan < 1 or colspan < 1 or row + rowspan > rows or col + colspan > columns:
            raise ValueError("Tablo hücresi sınırlar dışında.")
        text = cell.get("text")
        if not isinstance(text, str) or len(text) > 4000:
            raise ValueError("Tablo hücre metni geçersiz.")
        total_text += len(text)
        for r in range(row, row + rowspan):
            for c in range(col, col + colspan):
                if (r, c) in occupied:
                    raise ValueError("Tablo hücreleri çakışıyor.")
                occupied.add((r, c))
        cell.update(rowspan=rowspan, colspan=colspan)
    if total_text > 80000 or len(occupied) != rows * columns:
        raise ValueError("Tablo kesilmiş veya eksik konumlar içeriyor.")
    unreadable = result.get("unreadable_cells", [])
    if not isinstance(unreadable, list):
        raise ValueError("Okunamayan hücre listesi geçersiz.")
    for position in unreadable:
        if not isinstance(position, list) or len(position) != 2 or any(type(v) is not int for v in position) or not (0 <= position[0] < rows and 0 <= position[1] < columns):
            raise ValueError("Okunamayan hücre konumu geçersiz.")
    result["unreadable_cells"] = unreadable
    context = result.get("context_text", [])
    if not isinstance(context, list) or len(context) > 10 or any(not isinstance(text, str) or len(text) > 500 for text in context):
        raise ValueError("Tablo çevresi metni geçersiz.")
    return result


def validate_against_source(candidate: dict, source: str, aliases: dict | None = None) -> dict:
    validate_shape(candidate)
    def normalize(text):
        text = text.translate(str.maketrans(aliases or {})).casefold().replace("\u0307", "").replace("µ", "μ")
        return re.sub(r"(?<=\w)[\-\u00ad\x02]\s*(?=\w)", "", text)
    source = normalize(source)
    extracted = normalize(" ".join([*(cell["text"] for cell in candidate["cells"]), *candidate.get("context_text", [])]))
    def words(text):
        return Counter(re.findall(r"[^\W\d_]+", text, re.UNICODE))
    expected, found = words(source), words(extracted)
    matched = sum((expected & found).values())
    recall = matched / sum(expected.values()) if expected else 0.0
    precision = matched / sum(found.values()) if found else 0.0
    source_numbers = Counter(re.findall(r"\d+(?:[.,]\d+)?", source))
    candidate_numbers = Counter(re.findall(r"\d+(?:[.,]\d+)?", extracted))
    missing_numbers = list((source_numbers - candidate_numbers).elements())
    additional_numbers = list((candidate_numbers - source_numbers).elements())
    passed = bool(expected) and recall >= .95 and precision >= .95 and not missing_numbers and not additional_numbers and not candidate["unreadable_cells"] and not candidate.get("needs_review", True)
    return {"status": "passed" if passed else "manual_review", "word_recall": round(recall, 4), "word_precision": round(precision, 4), "missing_numbers": missing_numbers[:100], "additional_numbers": additional_numbers[:100], "unreadable_cells": candidate["unreadable_cells"], "message": "Metin ve sayılar kaynakla eşleşti; kullanıcı onayı bekleniyor." if passed else "Kaynakla tam eşleşme doğrulanamadı; görsel ile karşılaştırarak karar verin."}


def as_table_data(candidate):
    return {"num_rows": candidate["rows"], "num_cols": candidate["columns"], "table_cells": [dict(start_row_offset_idx=cell["row"], start_col_offset_idx=cell["column"], end_row_offset_idx=cell["row"] + cell["rowspan"], end_col_offset_idx=cell["column"] + cell["colspan"], row_span=cell["rowspan"], col_span=cell["colspan"], text=cell["text"]) for cell in candidate["cells"]]}


def candidate_markdown(candidate):
    validate_shape(candidate)
    context = "\n\n".join("> Tablo bağlamı: " + " ".join(text.split()) for text in candidate.get("context_text", []))
    return (context + "\n\n" if context else "") + render_table(as_table_data(candidate))


def candidate_html(candidate):
    validate_shape(candidate)
    rows = []
    for row in range(candidate["rows"]):
        cells = sorted((cell for cell in candidate["cells"] if cell["row"] == row), key=lambda cell: cell["column"])
        rows.append("<tr>" + "".join(f'<td rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">{escape(cell["text"])}</td>' for cell in cells) + "</tr>")
    context = "".join("<p>" + escape(text) + "</p>" for text in candidate.get("context_text", []))
    return context + "<table>" + "".join(rows) + "</table>"
