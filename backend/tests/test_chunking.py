from app.chunking import parse_text_document


def test_markdown_headings_stay_in_chunk_metadata() -> None:
    text = "# Ana Konu\n\n" + ("Açıklama cümlesi. " * 40) + "\n\n## Alt Konu\n\n" + ("İkinci açıklama. " * 40)

    sections = parse_text_document(text, ".md")

    assert len(sections) == 2
    assert sections[0].heading_path == ("Ana Konu",)
    assert sections[1].heading_path == ("Ana Konu", "Alt Konu")


def test_code_block_is_not_split() -> None:
    text = "# Örnek\n\n```python\n" + ("print('merhaba')\n" * 200) + "```"

    sections = parse_text_document(text, ".md")

    assert len(sections) == 1
    assert sections[0].content_type == "code"
