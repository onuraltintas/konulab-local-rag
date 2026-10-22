"""İsteğe bağlı arama bağlamı; kaynak metne hiçbir zaman yazılmaz."""

import hashlib
import json

from sqlalchemy import select

from app.models import DocumentSection
from app.prompts import CONTEXT_PROMPT_VERSION, JSON_ONLY_SYSTEM_PROMPT, search_context_prompt
from app.runtime_models import ModelProviderError, complete, current_runtime_model_config


def context_fingerprint(document, section, config, source_digest):
    data = [CONTEXT_PROMPT_VERSION, config.text_provider.value, config.text_base_url,
            config.text_model, document.content_hash, source_digest,
            document.lesson_title, section.heading_path, section.content]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


async def enrich_document(database, document, progress=lambda done, total: None, *, enabled=None, flush=True):
    if not (document.contextual_enrichment if enabled is None else enabled):
        return
    sections = list(database.scalars(select(DocumentSection).where(
        DocumentSection.document_id == document.id).order_by(DocumentSection.sequence)))
    config = current_runtime_model_config()
    source_digest = hashlib.sha256(json.dumps(
        [(s.heading_path, s.content) for s in sections], ensure_ascii=False).encode()).hexdigest()
    for index, section in enumerate(sections):
        fingerprint = context_fingerprint(document, section, config, source_digest)
        if ((section.search_context_metadata or {}).get("fingerprint") == fingerprint
                and (section.search_context_metadata or {}).get("status") != "failed"):
            progress(index + 1, len(sections))
            continue
        nearby = "\n\n".join(s.content[:2000] for s in sections[max(0, index - 1):index + 2])
        metadata = {"fingerprint": fingerprint, "model": config.text_model,
                    "provider": config.text_provider.value, "prompt_version": CONTEXT_PROMPT_VERSION}
        section.search_context = None
        try:
            response = await complete(config, [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": search_context_prompt(
                    document.lesson_title or document.source_name, section.heading_path, nearby, section.content)},
            ], temperature=0, max_tokens=220, json_mode=True)
            result = json.loads(response)
            context = result.get("context") if isinstance(result, dict) else None
            if not isinstance(context, str) or len(context) > 400:
                raise ValueError("Geçersiz bağlam")
            section.search_context = " ".join(context.split()) or None
            metadata["status"] = "generated" if section.search_context else "empty"
        except (ModelProviderError, ValueError):
            # Üretim hatası kaynak kaybına veya indeksleme engeline dönüşmez.
            metadata["status"] = "failed"
        section.search_context_metadata = metadata
        if flush:
            database.flush()
        progress(index + 1, len(sections))


def contextual_passage(base, section, document, embedder):
    """Yalnız sığan bağlamı ekler; asıl kaynakta tek token bile kesmez."""
    if not document.contextual_enrichment or not section.search_context:
        if section.search_context_metadata:
            section.search_context_metadata = {**section.search_context_metadata, "used_in_embedding": False,
                                               "skip_reason": "disabled" if not document.contextual_enrichment else "empty"}
        return base
    candidate = f"Arama bağlamı: {section.search_context}\n\n{base}"
    fits = len(embedder.tokenizer(candidate, add_special_tokens=False)["input_ids"]) <= embedder.passage_token_budget()
    metadata = dict(section.search_context_metadata or {})
    metadata["used_in_embedding"] = fits
    metadata["skip_reason"] = None if fits else "token_budget"
    section.search_context_metadata = metadata
    return candidate if fits else base
