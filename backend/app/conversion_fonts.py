"""Belge içeriğine değil, gömülü fontun glyph eşlemelerine dayalı düzeltmeler."""
import ctypes
import hashlib
import json
from io import BytesIO
from pathlib import Path


def infer_font_aliases(source: Path, pages: set[int]) -> dict[str, str]:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from fontTools.ttLib import TTFont, TTLibError
    from fontTools.agl import toUnicode
    candidates: dict[str, set[str]] = {}
    unresolved = set()
    registry = json.loads(Path(__file__).with_name("conversion-font-aliases.json").read_text(encoding="utf-8"))
    with pdfium.PdfDocument(str(source)) as pdf:
        for number in sorted(pages):
            page = pdf[number - 1]
            textpage = page.get_textpage()
            try:
                for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT], textpage=textpage):
                    chars = {char for char in obj.extract() if 0xE000 <= ord(char) <= 0xF8FF}
                    if not chars:
                        continue
                    font = obj.get_font()
                    size = ctypes.c_size_t()
                    if not raw.FPDFFont_GetFontData(font, None, 0, ctypes.byref(size)):
                        unresolved.update(chars)
                        continue
                    buffer = (ctypes.c_ubyte * size.value)()
                    if not raw.FPDFFont_GetFontData(font, buffer, size.value, ctypes.byref(size)):
                        unresolved.update(chars)
                        continue
                    approved = registry.get(hashlib.sha256(bytes(buffer)).hexdigest(), {}).get("aliases", {})
                    for char in chars & approved.keys():
                        candidates.setdefault(char, set()).add(approved[char])
                    chars -= approved.keys()
                    if not chars:
                        continue
                    try:
                        with TTFont(BytesIO(bytes(buffer))) as parsed:
                            cmap = parsed.getBestCmap() or {}
                            for char in chars:
                                glyph = cmap.get(ord(char))
                                values = set()
                                if glyph:
                                    unicode_value = toUnicode(glyph)
                                    if len(unicode_value) == 1 and not 0xE000 <= ord(unicode_value) <= 0xF8FF:
                                        values.add(unicode_value)
                                    values.update(chr(code) for code, name in cmap.items() if name == glyph and not 0xE000 <= code <= 0xF8FF)
                                if len(values) == 1:
                                    candidates.setdefault(char, set()).update(values)
                                else:
                                    unresolved.add(char)
                    except (TTLibError, ValueError, KeyError):
                        unresolved.update(chars)
            finally:
                textpage.close()
                page.close()
    return {char: next(iter(values)) for char, values in candidates.items() if len(values) == 1 and char not in unresolved}
