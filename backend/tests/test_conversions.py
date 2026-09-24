from uuid import uuid4
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.conversion import ConvertedAsset, ConvertedContent, _extract_assets
from app.document_analysis import TopicSuggestion
from app.main import app, markdown_with_visual_appendix
from app.models import AssetAnalysisStatus


def test_conversion_requires_markdown_publication_before_topic_analysis(tmp_path, monkeypatch) -> None:
    """Ham PDF, açık yayın onayı gelmeden bölüm/konu üretmemelidir."""
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(
        main,
        "convert_to_markdown",
        lambda source: ConvertedContent(
            markdown="# Tokenizasyon\n\n" + "Metin parçalara ayrılır ve her parçanın anlamı korunur. " * 20 + "\n\n## Örnek\n\n`merhaba` bir örnek metindir.",
            converter="docling",
            converter_version="test",
            ocr_used=False,
            page_count=1,
            table_count=0,
            image_count=0,
            assets=(ConvertedAsset(kind="picture", page_number=1, content=b"png-smoke"),),
        ),
    )
    class FakeAnalyzer:
        async def suggest_topics(self, sections):
            return [
                TopicSuggestion(
                    title="Tokenizasyon",
                    description="Metni parçalara ayırma.",
                    section_ids=[section.id for section in sections],
                    section_types={section.id: "explanation" for section in sections},
                )
            ]

        async def suggest_lesson_title(self, topics):
            return "Metin İşleme"

    monkeypatch.setattr(main, "get_document_analyzer", lambda: FakeAnalyzer())
    logical_key = f"donusum-onay-{uuid4().hex}"

    with TestClient(app) as client:
        converted = client.post(
            "/api/documents/convert",
            data={"logical_key": logical_key},
            files={"file": ("notlar.pdf", b"test-pdf", "application/pdf")},
        )
        assert converted.status_code == 202
        conversion = converted.json()
        document_id = conversion["document"]["id"]
        conversion = client.get(f"/api/documents/{document_id}/conversion").json()
        assert conversion["status"] == "ready"
        assert conversion["markdown"].startswith("# Tokenizasyon")
        assert len(conversion["assets"]) == 1
        assert conversion["assets"][0]["kind"] == "picture"
        assert conversion["assets"][0]["page_number"] == 1
        assert conversion["assets"][0]["url"].startswith(f"/api/documents/{document_id}/assets/")
        asset_response = client.get(conversion["assets"][0]["url"])
        assert asset_response.status_code == 200
        assert asset_response.content == b"png-smoke"

        # Dönüşüm hazır olsa da kullanıcı açıkça yayınlamadan konu analizi yoktur.
        assert client.get(f"/api/documents/{document_id}/analysis").status_code == 404
        bad_confirmation = client.post(
            f"/api/documents/{document_id}/publish-conversion", json={"confirmation": "onay"}
        )
        assert bad_confirmation.status_code == 422

        published = client.post(
            f"/api/documents/{document_id}/publish-conversion",
            json={"confirmation": "OGRENME_HARITASINA_DONUSTUR"},
        )

    # LLM testte çalıştırılmadığından arka plan analizi başarısız olabilir; önemli
    # sınır, yayın onayının bölümleri ve analiz kaydını başlatmış olmasıdır.
    assert published.status_code == 202
    assert published.json()["document"]["id"] == document_id
    assert list((tmp_path / "sources" / logical_key).glob("markdown-*.md"))


def test_deleting_conversion_removes_original_and_markdown(tmp_path, monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(
        main,
        "convert_to_markdown",
        lambda source: ConvertedContent(
            markdown="# Kaynak\n\nYeterli içerik burada yer alır.",
            converter="docling",
            converter_version="test",
            ocr_used=False,
            page_count=1,
            table_count=0,
            image_count=0,
        ),
    )
    logical_key = f"donusum-sil-{uuid4().hex}"
    with TestClient(app) as client:
        converted = client.post(
            "/api/documents/convert",
            data={"logical_key": logical_key},
            files={"file": ("sil.pdf", b"test-pdf", "application/pdf")},
        )
        document_id = converted.json()["document"]["id"]
        deleted = client.delete(f"/api/documents/{document_id}")

    assert deleted.status_code == 204
    assert not list((tmp_path / "sources" / logical_key).glob("*"))


def test_selected_asset_description_is_added_to_markdown_before_publication(tmp_path, monkeypatch) -> None:
    """VLM açıklaması seçilmeden çalışmaz; başarılı sonuç onay öncesi Markdown'da görünür."""
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(
        main,
        "convert_to_markdown",
        lambda source: ConvertedContent(
            markdown="# Akış\n\nDers metni burada yer alır.",
            converter="docling",
            converter_version="test",
            ocr_used=False,
            page_count=1,
            table_count=0,
            image_count=1,
            assets=(ConvertedAsset(kind="picture", page_number=1, content=b"visual-smoke"),),
        ),
    )

    class FakeVisionModel:
        async def describe_visual_asset(self, image, kind, page_number):
            assert image == b"visual-smoke"
            assert kind == "picture"
            assert page_number == 1
            return {"description": "Şekil, veri akışının üç adımını gösterir.", "confidence": 91, "needs_review": False}

    monkeypatch.setattr(main, "get_ollama_client", lambda: FakeVisionModel())
    logical_key = f"gorsel-aciklama-{uuid4().hex}"
    with TestClient(app) as client:
        created = client.post(
            "/api/documents/convert",
            data={"logical_key": logical_key},
            files={"file": ("akis.pdf", b"test-pdf", "application/pdf")},
        )
        document_id = created.json()["document"]["id"]
        conversion = client.get(f"/api/documents/{document_id}/conversion").json()
        asset_id = conversion["assets"][0]["id"]
        queued = client.post(f"/api/documents/{document_id}/assets/{asset_id}/analyze")
        described = client.get(f"/api/documents/{document_id}/conversion").json()
        accepted = client.post(f"/api/documents/{document_id}/assets/{asset_id}/inclusion/accepted")
        included = client.get(f"/api/documents/{document_id}/conversion").json()

    assert queued.status_code == 202
    assert described["assets"][0]["analysis_status"] == "ready"
    assert described["assets"][0]["confidence"] == 91
    assert "Şekil, veri akışının üç adımını gösterir." not in described["markdown"]
    assert accepted.status_code == 200
    assert included["assets"][0]["inclusion_status"] == "accepted"
    assert "Şekil, veri akışının üç adımını gösterir." in included["markdown"]


def test_markdown_edits_are_versioned_and_can_be_restored(tmp_path, monkeypatch) -> None:
    """Kullanıcı düzenlemesi özgün dönüşümü ezmez; eski sürüm yeni revizyon olarak geri gelir."""
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    original_markdown = "# Özgün başlık\n\nÖzgün açıklama metni."
    monkeypatch.setattr(
        main,
        "convert_to_markdown",
        lambda source: ConvertedContent(
            markdown=original_markdown,
            converter="docling",
            converter_version="test",
            ocr_used=False,
            page_count=1,
            table_count=0,
            image_count=0,
        ),
    )
    logical_key = f"markdown-surum-{uuid4().hex}"
    with TestClient(app) as client:
        created = client.post(
            "/api/documents/convert",
            data={"logical_key": logical_key},
            files={"file": ("surum.pdf", b"test-pdf", "application/pdf")},
        ).json()
        document_id = created["document"]["id"]
        initial = client.get(f"/api/documents/{document_id}/conversion").json()
        assert initial["current_revision_number"] == 1
        assert len(initial["revisions"]) == 1

        edited_text = "# Düzenlenmiş başlık\n\nKullanıcının eklediği açıklama."
        edited = client.put(
            f"/api/documents/{document_id}/conversion/markdown",
            json={"markdown": edited_text, "expected_markdown_hash": initial["markdown_hash"]},
        )
        assert edited.status_code == 200
        edited_payload = edited.json()
        assert edited_payload["current_revision_number"] == 2
        assert edited_payload["markdown"].startswith("# Düzenlenmiş başlık")
        assert len(edited_payload["revisions"]) == 2

        stale_save = client.put(
            f"/api/documents/{document_id}/conversion/markdown",
            json={"markdown": "# Çakışma", "expected_markdown_hash": initial["markdown_hash"]},
        )
        assert stale_save.status_code == 409

        first_revision = client.get(f"/api/documents/{document_id}/conversion/revisions/1")
        assert first_revision.status_code == 200
        assert first_revision.json()["markdown"].startswith("# Özgün başlık")

        restored = client.post(
            f"/api/documents/{document_id}/conversion/revisions/1/restore",
            json={"expected_markdown_hash": edited_payload["markdown_hash"]},
        )
    assert restored.status_code == 200
    assert restored.json()["current_revision_number"] == 3
    assert restored.json()["markdown"].startswith("# Özgün başlık")


def test_table_asset_mapping_skips_unexportable_table_without_losing_order() -> None:
    """İlk tablo PNG'si bozuksa ikinci tablonun Markdown yapısı kaymamalıdır."""
    class TableItem:
        def __init__(self, image) -> None:
            self._image = image
            self.prov = [SimpleNamespace(page_no=1)]

        def get_image(self, _document):
            return self._image

    class FakeImage:
        def thumbnail(self, _size) -> None:
            pass

        def save(self, stream, *, format, optimize) -> None:
            assert format == "PNG"
            assert optimize is True
            stream.write(b"table-png")

    first = "| İlk | Değer |\n| --- | --- |\n| a | 1 |"
    second = "| İkinci | Değer |\n| --- | --- |\n| b | 2 |"
    document = SimpleNamespace(iterate_items=lambda: [(TableItem(None),), (TableItem(FakeImage()),)])

    assets = _extract_assets(document, [first, second])

    assert len(assets) == 1
    assert assets[0].structured_markdown == second
    assert assets[0].row_count == 1
    assert assets[0].column_count == 2


def test_accepted_table_description_is_written_immediately_after_table() -> None:
    table = "| Terim | Anlam |\n| --- | --- |\n| RAG | Kaynaklı üretim |"
    asset = SimpleNamespace(
        id=uuid4(),
        kind="table",
        inclusion_status="accepted",
        analysis_status=AssetAnalysisStatus.READY,
        description="Tablo, temel kavram ile tanımını eşleştirir.",
        needs_review=False,
        structured_markdown=table,
        asset_path="/tmp/table.png",
        page_number=2,
    )

    markdown = markdown_with_visual_appendix(f"# Kavramlar\n\n{table}\n\nSonraki bölüm.", [asset])

    assert f"{table}\n\n> Tablo açıklaması: Tablo, temel kavram ile tanımını eşleştirir.\n\nSonraki bölüm." in markdown
