"""Yapısal belge ayrıştırma.

Embedding için küçük, anlamlı parçalar; kullanıcıya gösterim ve kaynak bağlamı için
daha geniş üst bölümler üretilir. Tokenizer bağımlılığı eklenmeden önce karakter
hedefi kullanılır; embedding işçisi geldiğinde gerçek token sayımıyla iyileştirilecektir.
"""

from dataclasses import dataclass
import re


TARGET_CHARS = 1_800
MAX_CHARS = 2_400
MIN_CHARS = 180

MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LESSON_HEADING = re.compile(r"^\s*(\d{1,3})\.\s*Ders\b\s*(.*)$", re.IGNORECASE)
SEPARATOR = re.compile(r"^\s*#[-_]{8,}\s*$")
CODE_START = re.compile(r"^\s*```")


@dataclass(frozen=True)
class ParsedSection:
    heading_path: tuple[str, ...]
    content: str
    content_type: str
    line_start: int
    line_end: int


def _content_type(text: str, suffix: str) -> str:
    if suffix == ".py" or "```" in text:
        return "code"
    if "┌" in text or "│" in text or "|" in text:
        return "table"
    return "explanation"


def _split_large_section(section: ParsedSection) -> list[ParsedSection]:
    if len(section.content) <= MAX_CHARS or section.content_type in {"code", "table"}:
        return [section]

    paragraphs = re.split(r"\n\s*\n", section.content)
    output: list[ParsedSection] = []
    buffer: list[str] = []
    buffer_start = section.line_start
    current_length = 0
    consumed_lines = 0

    def flush() -> None:
        nonlocal buffer, current_length, buffer_start, consumed_lines
        if not buffer:
            return
        content = "\n\n".join(buffer).strip()
        if content:
            output.append(
                ParsedSection(
                    heading_path=section.heading_path,
                    content=content,
                    content_type=section.content_type,
                    line_start=buffer_start,
                    line_end=buffer_start + sum(item.count("\n") + 2 for item in buffer) - 1,
                )
            )
        consumed_lines += sum(item.count("\n") + 2 for item in buffer)
        buffer_start = section.line_start + consumed_lines
        buffer = []
        current_length = 0

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if buffer and current_length + len(paragraph) + 2 > TARGET_CHARS:
            flush()
        if len(paragraph) > MAX_CHARS:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            for sentence in sentences:
                if buffer and current_length + len(sentence) + 1 > TARGET_CHARS:
                    flush()
                buffer.append(sentence)
                current_length += len(sentence) + 1
            continue
        buffer.append(paragraph)
        current_length += len(paragraph) + 2
    flush()
    return output or [section]


def parse_text_document(text: str, suffix: str) -> list[ParsedSection]:
    """Başlıkları koruyarak belgeyi arama parçalarına ayırır."""
    sections: list[ParsedSection] = []
    heading_stack: list[str] = []
    buffer: list[str] = []
    start_line = 1
    in_code_block = False

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        content = "\n".join(buffer).strip()
        if len(content) >= MIN_CHARS:
            base = ParsedSection(
                heading_path=tuple(heading_stack) or ("Başlangıç",),
                content=content,
                content_type=_content_type(content, suffix),
                line_start=start_line,
                line_end=end_line,
            )
            sections.extend(_split_large_section(base))
        buffer = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if CODE_START.match(line):
            in_code_block = not in_code_block
            buffer.append(line)
            continue

        markdown_match = MARKDOWN_HEADING.match(line) if suffix == ".md" and not in_code_block else None
        lesson_match = LESSON_HEADING.match(line) if not in_code_block else None
        is_separator = SEPARATOR.match(line) and not in_code_block

        if markdown_match:
            flush(line_number - 1)
            level = len(markdown_match.group(1))
            heading_stack = heading_stack[: level - 1] + [markdown_match.group(2)]
            start_line = line_number + 1
        elif lesson_match:
            flush(line_number - 1)
            title = f"Ders {lesson_match.group(1)}"
            if lesson_match.group(2):
                title = f"{title} — {lesson_match.group(2).strip()}"
            heading_stack = [title]
            start_line = line_number + 1
        elif is_separator:
            if buffer:
                flush(line_number - 1)
                start_line = line_number + 1
        else:
            buffer.append(line)
    flush(len(text.splitlines()))
    return sections


def split_sections_by_tokens(
    sections: list[ParsedSection], tokenizer: object, max_tokens: int, overlap_tokens: int = 48
) -> list[ParsedSection]:
    """Bölümleri embedding modelinin gerçek token penceresine göre kayıpsız böler.

    Karakter uzunluğu yalnızca ilk yapısal bölme için kullanılır. Bu ikinci adım,
    token sayısı sınırı aşılırsa metni tokenizer'ın offset'leri üzerinden keser;
    böylece sözlük/embedding katmanında sessiz kırpma yaşanmaz. Örtüşme retrieval
    sınırlarında bağlamı korur, fakat kaynak metinden hiçbir kısmı çıkarmaz.
    """
    if max_tokens < 32:
        raise ValueError("Embedding için token bütçesi çok düşük.")
    safe_overlap = max(0, min(overlap_tokens, max_tokens // 4))
    output: list[ParsedSection] = []
    for section in sections:
        heading_prefix = " > ".join(section.heading_path) + "\n\n"
        heading_tokens = len(tokenizer(heading_prefix, add_special_tokens=False).get("input_ids", []))  # type: ignore[operator]
        # Başlık ile içerik ayrı tokenize edilse de birleşimde sınır tokenları
        # değişebilir; dört tokenlık ek pay indeksleyici doğrulamasını garanti eder.
        content_budget = max_tokens - heading_tokens - 4
        if content_budget < 32:
            raise ValueError("Bölüm başlığı embedding token bütçesini aşıyor.")
        encoded = tokenizer(  # type: ignore[operator]
            section.content,
            add_special_tokens=False,
            return_offsets_mapping=True,
        )
        token_ids = encoded.get("input_ids", [])
        offsets = encoded.get("offset_mapping", [])
        if len(token_ids) <= content_budget or not offsets:
            output.append(section)
            continue
        start_token = 0
        while start_token < len(token_ids):
            end_token = min(start_token + content_budget, len(token_ids))
            char_start = offsets[start_token][0]
            # Token sonrasındaki boşluğu ayrı bir tokena dönüştürmemek için sınırı
            # son gerçek tokenın sonunda kapatıyoruz. Örtüşmeli sonraki parça bu
            # sınır çevresindeki bağlamı zaten yeniden taşır.
            char_end = offsets[end_token - 1][1] if end_token < len(offsets) else len(section.content)
            content = section.content[char_start:char_end]
            if content.strip():
                output.append(
                    ParsedSection(
                        heading_path=section.heading_path,
                        content=content,
                        content_type=section.content_type,
                        line_start=section.line_start + section.content[:char_start].count("\n"),
                        line_end=section.line_start + section.content[:char_end].count("\n"),
                    )
                )
            if end_token == len(token_ids):
                break
            start_token = end_token - safe_overlap
    return output
