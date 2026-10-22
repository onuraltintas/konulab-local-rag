from types import SimpleNamespace

from PIL import Image

from app.conversion import _extract_assets, verified_spacing
from app.conversion_tables import render_table
from pathlib import Path


def test_all_visuals_are_extracted_after_hundredth_item():
    picture_type = type("PictureItem", (), {"get_image": lambda self, doc: Image.new("RGB", (2, 2))})
    pictures = [picture_type() for _ in range(105)]
    document = SimpleNamespace(iterate_items=lambda: ((item,) for item in pictures))
    assert len(_extract_assets(document, [])) == 105


def test_spacing_requires_identical_independent_character_sequence():
    assert verified_spacing("bu ş afaklarda", "Korkma, bu şafaklarda yüzen") == "bu şafaklarda"
    assert verified_spacing("bu ş afaklarda", "bu sokaklarda") is None
    assert verified_spacing("ş afak", "şafak ve şafak") is None


def test_font_repair_requires_explicit_mapping():
    assert verified_spacing("y  ld  z", "yldz", {"\uf0f5": "ı"}) == "yıldız"
    assert verified_spacing("y  ld  z", "yıldız") is None


def test_merged_columns_do_not_repeat_and_index_numbers_are_joined():
    data = {"num_rows": 1, "num_cols": 3, "table_cells": [
        {"start_row_offset_idx": 0, "start_col_offset_idx": 0, "end_row_offset_idx": 1, "col_span": 2, "text": "Biyoloji ........"},
        {"start_row_offset_idx": 0, "start_col_offset_idx": 2, "end_row_offset_idx": 1, "text": "1 7"},
    ]}
    assert render_table(data).count("Biyoloji") == 1
    assert render_table(data, is_index=True) == "- Biyoloji — 17"


def test_identical_tables_keep_their_own_source_positions(monkeypatch):
    from app import conversion
    raw = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    data = {"num_rows": 2, "num_cols": 2, "table_cells": [dict(start_row_offset_idx=r, start_col_offset_idx=c, end_row_offset_idx=r+1, text=text) for r, values in enumerate([["A", "B"], ["1", "2"]]) for c, text in enumerate(values)]}
    table_type = type("TableItem", (), {"get_image": lambda self, doc: Image.new("RGB", (2, 2)), "export_to_markdown": lambda self, doc: raw, "export_to_html": lambda self, **kwargs: "<table></table>"})
    tables = []
    for index in range(2):
        table = table_type()
        table.data = SimpleNamespace(num_rows=2, num_cols=2, model_dump=lambda **kwargs: data)
        table.prov, table.label, table.self_ref = [], "table", f"#/tables/{index}"
        tables.append(table)
    document = SimpleNamespace(tables=tables, pages={}, export_to_markdown=lambda: f"# Başlangıç\n\n{raw}\n\nARA BÖLÜM\n\n{raw}\n\nSON", iterate_items=lambda: ((table,) for table in tables))
    monkeypatch.setattr(conversion, "_build_converter", lambda **kwargs: SimpleNamespace(convert=lambda path: SimpleNamespace(document=document)))
    markdown = conversion._convert_once(Path("test.docx"), use_ocr=False).markdown
    assert markdown.index("<!-- konulab-table-body:0:end -->") < markdown.index("ARA BÖLÜM") < markdown.index("<!-- konulab-table-body:1:start -->")
