"""Ollama üzerinden kaynak-bağlamlı yerel sohbet istemcisi."""

import json
from functools import lru_cache

from app.prompts import JSON_ONLY_SYSTEM_PROMPT, answer_quality_judge_prompt, prerequisite_suggestion_prompt, source_grounded_chat_prompt, topic_quiz_prompt, visual_asset_description_prompt
from app.runtime_models import ModelProviderError, complete, current_runtime_model_config
from app.schemas import SearchResult, TopicStudyStepResponse


class OllamaUnavailableError(RuntimeError):
    pass


def format_sources(sources: list[SearchResult]) -> str:
    """Sohbet ve kalite değerlendirmesinde aynı, numaralı kaynak bağlamını kullanır."""
    return "\n\n".join(
        f"[K{index}] {source.source_name} | {source.heading_path} | "
        f"satır {source.line_start}-{source.line_end}\n{source.content}"
        for index, source in enumerate(sources, start=1)
    )


class OllamaClient:
    """Geriye dönük adını koruyan, Ollama veya vLLM kullanan birleşik istemci."""

    async def answer(
        self,
        question: str,
        sources: list[SearchResult],
        history: list[tuple[str, str]] | None = None,
        learning_mode: bool = False,
    ) -> str:
        context = format_sources(sources)
        system_prompt = source_grounded_chat_prompt(context, learning_mode)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": role, "content": content}
            for role, content in (history or [])
            if role in {"user", "assistant"}
        )
        messages.append({"role": "user", "content": question})
        try:
            return await complete(current_runtime_model_config(), messages, temperature=0.55 if learning_mode else 0.2, think=False)
        except ModelProviderError as error:
            raise OllamaUnavailableError(str(error)) from error

    async def evaluate_answer(self, question: str, sources: list[SearchResult], answer: str) -> dict[str, object]:
        """Yerel modelden kaynak-yanıt uyumu için yapılandırılmış kalite sinyali alır."""
        try:
            content = await complete(current_runtime_model_config(), [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": answer_quality_judge_prompt(question, format_sources(sources), answer)},
            ], temperature=0, max_tokens=220, json_mode=True)
            result = json.loads(content.removeprefix("```json").removesuffix("```").strip())
        except (ModelProviderError, ValueError, json.JSONDecodeError) as error:
            raise OllamaUnavailableError("Yerel model kalite değerlendirmesi döndüremedi.") from error

        required_scores = ("groundedness", "coverage", "teaching", "citation_quality")
        for key in required_scores:
            if not isinstance(result.get(key), int) or result[key] not in {0, 1, 2}:
                raise OllamaUnavailableError("Yerel model geçersiz kalite puanı döndürdü.")
        if result.get("verdict") not in {"pass", "review", "fail"}:
            raise OllamaUnavailableError("Yerel model geçersiz kalite kararı döndürdü.")
        if not isinstance(result.get("reason"), str) or not result["reason"].strip():
            raise OllamaUnavailableError("Yerel model kalite gerekçesi döndürmedi.")
        return result

    async def generate_topic_quiz(self, topic_title: str, steps: list[TopicStudyStepResponse]) -> dict[str, object]:
        """Kaynaklı konu quizi için modelden doğrulanabilir JSON alır."""
        allowed_step_ids = {str(step.id) for step in steps}
        prompt_steps = [
            {"step_id": str(step.id), "heading": step.heading, "content": step.content[:1_600]}
            for step in steps
        ]
        try:
            content = await complete(current_runtime_model_config(), [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": topic_quiz_prompt(topic_title, prompt_steps)},
            ], temperature=0.25, max_tokens=1_200, json_mode=True)
            result = json.loads(content.removeprefix("```json").removesuffix("```").strip())
        except (ModelProviderError, ValueError, json.JSONDecodeError) as error:
            raise OllamaUnavailableError("Yerel model kaynaklı quiz üretemedi.") from error

        questions = result.get("questions") if isinstance(result, dict) else None
        if not isinstance(questions, list) or len(questions) != 3:
            raise OllamaUnavailableError("Yerel model geçerli sayıda quiz sorusu döndürmedi.")
        seen_ids: set[str] = set()
        for question in questions:
            if not isinstance(question, dict):
                raise OllamaUnavailableError("Yerel model geçersiz quiz sorusu döndürdü.")
            question_id = str(question.get("id", "")).strip()
            if not question_id or question_id in seen_ids or not str(question.get("question", "")).strip():
                raise OllamaUnavailableError("Yerel model geçersiz quiz soru kimliği döndürdü.")
            seen_ids.add(question_id)
            options = question.get("options")
            if not isinstance(options, list) or len(options) != 4:
                raise OllamaUnavailableError("Yerel model dört seçenekli quiz sorusu döndürmedi.")
            option_ids = [str(option.get("id", "")).strip() for option in options if isinstance(option, dict)]
            if option_ids != ["A", "B", "C", "D"] or any(not str(option.get("text", "")).strip() for option in options):
                raise OllamaUnavailableError("Yerel model quiz seçeneklerini geçersiz döndürdü.")
            if question.get("correct_option_id") not in option_ids or not str(question.get("explanation", "")).strip():
                raise OllamaUnavailableError("Yerel model quiz cevabını geçersiz döndürdü.")
            if str(question.get("related_step_id", "")) not in allowed_step_ids:
                raise OllamaUnavailableError("Yerel model quiz sorusunu geçersiz bir öğrenme adımına bağladı.")
        return result

    async def suggest_prerequisites(self, target: dict[str, object], candidates: list[dict[str, object]]) -> list[dict[str, object]]:
        allowed_ids = {candidate["id"] for candidate in candidates}
        try:
            content = await complete(current_runtime_model_config(), [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": prerequisite_suggestion_prompt(target, candidates)},
            ], temperature=0.1, max_tokens=700, json_mode=True)
            result = json.loads(content.removeprefix("```json").removesuffix("```").strip())
        except (ModelProviderError, ValueError, json.JSONDecodeError) as error:
            raise OllamaUnavailableError("Yerel model ön koşul önerisi döndüremedi.") from error
        suggestions = result.get("suggestions") if isinstance(result, dict) else None
        if not isinstance(suggestions, list) or len(suggestions) > 4:
            raise OllamaUnavailableError("Yerel model geçersiz ön koşul önerisi döndürdü.")
        valid: list[dict[str, object]] = []
        seen: set[int] = set()
        for suggestion in suggestions:
            topic_id = suggestion.get("topic_id") if isinstance(suggestion, dict) else None
            reason = suggestion.get("reason") if isinstance(suggestion, dict) else None
            confidence = suggestion.get("confidence") if isinstance(suggestion, dict) else None
            if not isinstance(topic_id, int) or topic_id not in allowed_ids or topic_id in seen or not isinstance(reason, str) or not reason.strip() or not isinstance(confidence, (int, float)):
                raise OllamaUnavailableError("Yerel model geçersiz ön koşul adayı döndürdü.")
            seen.add(topic_id)
            valid.append({"topic_id": topic_id, "reason": reason.strip()[:300], "confidence": max(0.0, min(1.0, float(confidence)))})
        return valid

    async def describe_visual_asset(self, image: bytes, kind: str, page_number: int | None) -> dict[str, object]:
        """Görsel destekli bir Ollama modelinden sınırlı açıklama alır."""
        try:
            config = current_runtime_model_config()
            content = await complete(config, [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": visual_asset_description_prompt(kind, page_number)},
            ], model=config.vision_model, temperature=0.1, max_tokens=900, json_mode=True, image=image)
            result = json.loads(content.removeprefix("```json").removesuffix("```").strip())
        except (ModelProviderError, ValueError, json.JSONDecodeError) as error:
            raise OllamaUnavailableError("Görsel model açıklama üretemedi. Vision destekli modelin yüklü olduğunu kontrol edin.") from error
        description = result.get("description") if isinstance(result, dict) else None
        confidence = result.get("confidence") if isinstance(result, dict) else None
        needs_review = result.get("needs_review") if isinstance(result, dict) else None
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description.strip()) > 700
            or not isinstance(confidence, int)
            or not 0 <= confidence <= 100
            or not isinstance(needs_review, bool)
        ):
            raise OllamaUnavailableError("Görsel model geçerli bir açıklama döndürmedi.")
        return {
            "description": description.strip(),
            "confidence": confidence,
            "needs_review": needs_review,
        }


@lru_cache
def get_ollama_client() -> OllamaClient:
    return OllamaClient()
