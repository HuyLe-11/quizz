"""Grounded, owner-scoped multiple-choice generation from syllabus chunks."""

from __future__ import annotations

import json
import math
import os
import random
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .gemini_service import GeminiService
from .question_prompts import (
    SYSTEM_INSTRUCTION,
    VERIFIER_SYSTEM_INSTRUCTION,
    blueprint_requirements,
    build_generation_prompt,
    build_verification_prompt,
)


DEFAULT_TOP_K = 8
MAX_TOP_K = 20
MAX_CONTEXT_CHARACTERS = 16_000
MAX_OUTPUT_CHARACTERS = 40_000
MAX_TOPIC_CHARACTERS = 300
MAX_LANGUAGE_CHARACTERS = 40
VERIFY_ENV = "QUIZPDF_VERIFY_QUESTIONS"
SHUFFLE_ENV = "QUIZPDF_SHUFFLE_ANSWERS"


class QuestionGenerationError(RuntimeError):
    """Base error for retrieval and generation failures."""


class QuestionOutputError(QuestionGenerationError, ValueError):
    """Raised when model output is malformed or violates the contract."""


class QuestionRepository(Protocol):
    def get_syllabus_document(self, document_id: str) -> dict[str, Any] | None: ...

    def match_syllabus_chunks(
        self, query_embedding: list[float], *, match_count: int,
        document_id: str,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class GeneratedQuestion:
    question: str
    options: tuple[str, str, str, str]
    correct_answer_index: int
    explanation: str
    difficulty: str
    grounding_type: str
    source_references: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class QuestionGenerationResult:
    status: str
    document_id: str
    document_title: str
    topic: str
    questions: tuple[GeneratedQuestion, ...]
    message: str | None = None


_QUESTION_BLUEPRINTS: dict[str, tuple[tuple[str, int], ...]] = {
    "easy": (
        ("conceptual understanding", 60),
        ("simple application", 40),
    ),
    "medium": (
        ("conceptual understanding", 20),
        ("application and problem solving", 40),
        ("scenario analysis", 40),
    ),
    "hard": (
        ("complex application", 40),
        ("multi-step analysis", 40),
        ("debugging, evaluation, or problem solving", 20),
    ),
}


def build_question_blueprint(
    question_count: int,
    difficulty: str,
) -> list[dict[str, Any]]:
    """Allocate exactly ``question_count`` cognitive objectives by largest remainder.

    Ties resolve in the declared blueprint order. The final schedule round-robins
    across objectives to avoid clustering identical question styles.
    """
    if type(question_count) is not int or not 1 <= question_count <= 10:
        raise ValueError("question_count must be from 1 to 10.")
    if difficulty not in _QUESTION_BLUEPRINTS:
        raise ValueError("difficulty must be easy, medium, or hard.")

    objectives = _QUESTION_BLUEPRINTS[difficulty]
    raw_counts = [question_count * percentage / 100 for _, percentage in objectives]
    allocations = [int(value) for value in raw_counts]
    remainder_order = sorted(
        range(len(objectives)),
        key=lambda index: (-(raw_counts[index] - allocations[index]), index),
    )
    for index in remainder_order[:question_count - sum(allocations)]:
        allocations[index] += 1

    schedule: list[str] = []
    while len(schedule) < question_count:
        for index, (objective, _) in enumerate(objectives):
            if allocations[index] > 0:
                schedule.append(objective)
                allocations[index] -= 1
                if len(schedule) == question_count:
                    break
    return [
        {
            "question_number": index + 1,
            "cognitive_objective": objective,
            "requirements": blueprint_requirements(objective),
        }
        for index, objective in enumerate(schedule)
    ]


def _clean_string(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuestionOutputError(f"Generated {field} must be a non-empty string.")
    result = value.strip()
    if len(result) > maximum:
        raise QuestionOutputError(f"Generated {field} exceeds the allowed length.")
    return result


def _parse_json_response(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        # Tolerate a single conventional code fence, but not arbitrary prose.
        fenced = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.I | re.S)
        if fenced is None:
            raise QuestionOutputError("Gemini returned invalid question JSON.") from exc
        try:
            value = json.loads(fenced.group(1))
        except ValueError as nested:
            raise QuestionOutputError("Gemini returned invalid question JSON.") from nested
    if not isinstance(value, dict):
        raise QuestionOutputError("Gemini question response must be a JSON object.")
    return value


def validate_question_output(
    text: str,
    *,
    requested_count: int,
    difficulty: str,
    retrieved_chunks: list[dict[str, Any]],
    language: str = "Vietnamese",
) -> tuple[tuple[GeneratedQuestion, ...], str | None]:
    """Validate shape and ensure every citation maps to retrieved provenance."""
    if not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARACTERS:
        raise QuestionOutputError("Gemini question response exceeds the allowed size.")
    payload = _parse_json_response(text)
    status = payload.get("status")
    if status == "insufficient_context":
        if payload.get("questions") != []:
            raise QuestionOutputError(
                "Insufficient-context responses must not include questions."
            )
        message = payload.get("message")
        if message is not None:
            message = _clean_string(message, "message", maximum=500)
        if language.casefold().startswith("vi"):
            # Keep this user-facing status deterministic and in the API's
            # requested language even if the model returns it in another one.
            message = (
                "Đề cương chưa xác nhận chủ đề này hoặc chưa đủ căn cứ để tạo câu hỏi đáng tin cậy."
            )
        elif message is None:
            message = (
                "The syllabus does not establish this topic or enough evidence is unavailable for reliable questions."
            )
        return (), message
    if status != "ok":
        raise QuestionOutputError("Gemini returned an unsupported question status.")

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) != requested_count:
        raise QuestionOutputError("Gemini returned an unexpected number of questions.")

    source_map: dict[str, set[int]] = {}
    for chunk in retrieved_chunks:
        chunk_id = chunk.get("chunk_id")
        pages = chunk.get("source_pages")
        if isinstance(chunk_id, str) and isinstance(pages, list):
            source_map[chunk_id] = {
                page for page in pages if type(page) is int and page > 0
            }

    results: list[GeneratedQuestion] = []
    seen_questions: set[str] = set()
    for raw in raw_questions:
        if not isinstance(raw, dict):
            raise QuestionOutputError("Each generated question must be an object.")
        question = _clean_string(raw.get("question"), "question", maximum=2000)
        normalized = " ".join(question.casefold().split())
        if normalized in seen_questions:
            raise QuestionOutputError("Gemini returned duplicate questions.")
        seen_questions.add(normalized)

        options = raw.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise QuestionOutputError("Each question must have exactly four options.")
        cleaned_options = tuple(
            _clean_string(option, "option", maximum=1000) for option in options
        )
        if len({" ".join(option.casefold().split()) for option in cleaned_options}) != 4:
            raise QuestionOutputError("Question options must be distinct.")

        answer_index = raw.get("correct_answer_index")
        if type(answer_index) is not int or not 0 <= answer_index < 4:
            raise QuestionOutputError("correct_answer_index must be from 0 to 3.")
        explanation = _clean_string(raw.get("explanation"), "explanation", maximum=3000)
        actual_difficulty = raw.get("difficulty")
        if actual_difficulty != difficulty:
            raise QuestionOutputError("Generated question difficulty did not match the request.")
        grounding = raw.get("grounding_type")
        if grounding not in {"source_supported", "syllabus_aligned"}:
            raise QuestionOutputError("Generated question has an invalid grounding_type.")

        raw_refs = raw.get("source_references")
        if not isinstance(raw_refs, list) or not raw_refs:
            raise QuestionOutputError("Each question needs at least one source reference.")
        refs: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, tuple[int, ...]]] = set()
        for ref in raw_refs:
            if not isinstance(ref, dict):
                raise QuestionOutputError("Source references must be objects.")
            chunk_id = ref.get("chunk_id")
            pages = ref.get("source_pages")
            if not isinstance(chunk_id, str) or chunk_id not in source_map:
                raise QuestionOutputError("A source reference is not a retrieved chunk.")
            if not isinstance(pages, list) or not pages or any(
                type(page) is not int or page <= 0 or page not in source_map[chunk_id]
                for page in pages
            ):
                raise QuestionOutputError("A source reference contains invalid page provenance.")
            page_tuple = tuple(pages)
            ref_key = (chunk_id, page_tuple)
            if ref_key not in seen_refs:
                refs.append({"chunk_id": chunk_id, "source_pages": list(page_tuple)})
                seen_refs.add(ref_key)
        results.append(GeneratedQuestion(
            question=question,
            options=cleaned_options,  # type: ignore[arg-type]
            correct_answer_index=answer_index,
            explanation=explanation,
            difficulty=difficulty,
            grounding_type=grounding,
            source_references=tuple(refs),
        ))
    return tuple(results), None


def _bounded_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = MAX_CONTEXT_CHARACTERS
    result = []
    for chunk in chunks:
        if remaining <= 0:
            break
        content = chunk.get("content")
        chunk_id = chunk.get("chunk_id")
        pages = chunk.get("source_pages")
        if not isinstance(content, str) or not content.strip() or not isinstance(chunk_id, str):
            continue
        if not isinstance(pages, list):
            continue
        content = content.strip()
        if len(content) > remaining:
            content = content[:remaining].rstrip() + " [excerpt truncated]"
        item = {
            "chunk_id": chunk_id,
            "source_pages": pages,
            "section_heading": chunk.get("section_heading"),
            "content": content,
        }
        similarity = chunk.get("similarity")
        if isinstance(similarity, (int, float)) and not isinstance(similarity, bool):
            if math.isfinite(float(similarity)):
                item["similarity"] = float(similarity)
        result.append(item)
        remaining -= len(content)
    return result


def _env_flag(name: str, override: bool | None) -> bool:
    if override is not None:
        if type(override) is not bool:
            raise QuestionOutputError(f"{name} override must be boolean.")
        return override
    value = os.getenv(name, "0").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise QuestionOutputError(f"Invalid boolean configuration for {name}.")


# Conservative guard for order-sensitive content. False positives only skip
# shuffling, never change a question's meaning. This is not a semantic proof.
_POSITION_REFERENCE = re.compile(
    r"\b(?:option|choice|answer|statement|phương án|đáp án|lựa chọn|mệnh đề)"
    r"\s*[:(]?\s*(?:[a-d1-4]\b|thứ\s+\w+)|"
    r"\b(?:above|below|preceding|following|first option|last option|all of these|none of these|"
    r"các đáp án trên|các phương án trên|các lựa chọn trên|tất cả các ý trên|"
    r"tất cả đều đúng|tất cả đều sai|không có đáp án nào|không có phương án nào|"
    r"tất cả các lựa chọn|tất cả các đáp án|tất cả các phương án|các ý trên|"
    r"cả hai phương án|cả hai đáp án)\b",
    re.IGNORECASE,
)


def _can_shuffle(question: GeneratedQuestion) -> bool:
    # Standalone capital letters can be option labels, even in unexpected
    # languages. Guard them separately, without treating ordinary lowercase
    # English articles as labels.
    text = "\n".join((question.question, *question.options, question.explanation))
    return not (re.search(r"\b[A-D]\b", text) or _POSITION_REFERENCE.search(text))


def shuffle_question_answers(
    questions: tuple[GeneratedQuestion, ...],
    *, rng: random.Random | None = None,
) -> tuple[GeneratedQuestion, ...]:
    """Randomize distractors and distribute correct positions in groups of four.

    Preserve the correct option content, all metadata and position-sensitive
    questions. No semantic correctness is implied by shuffling.
    """
    rng = rng if rng is not None else random.SystemRandom()
    eligible_count = sum(_can_shuffle(question) for question in questions)
    slots: list[int] = []
    while len(slots) < eligible_count:
        block = list(range(4))
        rng.shuffle(block)
        slots.extend(block)
    targets = iter(slots)
    result = []
    for question in questions:
        if not _can_shuffle(question):
            result.append(question)
            continue
        correct = question.options[question.correct_answer_index]
        distractors = [option for i, option in enumerate(question.options)
                       if i != question.correct_answer_index]
        rng.shuffle(distractors)
        index = next(targets)
        distractors.insert(index, correct)
        result.append(replace(question, options=tuple(distractors),
                              correct_answer_index=index))
    return tuple(result)


def _verify_questions(
    gemini: GeminiService,
    questions: tuple[GeneratedQuestion, ...],
    *, title: str, topic: str, difficulty: str, language: str,
    question_blueprint: list[dict[str, Any]], chunks: list[dict[str, Any]],
) -> tuple[GeneratedQuestion, ...]:
    # Do not send draft answer indices, explanations or grounding claims to the
    # reviewer: it must derive these independently from the stem and context.
    prompt = build_verification_prompt(
        title=title, topic=topic, difficulty=difficulty, language=language,
        question_blueprint=question_blueprint, chunks=chunks,
        draft_questions=[
            {"question_number": i + 1, "question": q.question,
             "options": list(q.options)}
            for i, q in enumerate(questions)
        ],
    )
    # Keep provider exceptions (especially 429) intact for existing API mapping.
    verified_text = gemini.generate_text(prompt, system_instruction=VERIFIER_SYSTEM_INSTRUCTION)
    try:
        verified, _ = validate_question_output(
            verified_text, requested_count=len(questions), difficulty=difficulty,
            retrieved_chunks=chunks, language=language,
        )
        if not verified:
            raise QuestionOutputError("Verifier returned no questions.")
    except QuestionOutputError as exc:
        # Fail closed. Do not expose provider output or return the initial draft.
        raise QuestionOutputError("Question verification failed; no quiz was returned.") from exc
    return verified


class QuestionGenerationService:
    """Retrieve, generate and optionally review/shuffle MCQs, without persistence.

    Both optional stages default off. Enable them with QUIZPDF_VERIFY_QUESTIONS
    and QUIZPDF_SHUFFLE_ANSWERS (1/true/on/yes), or explicit constructor flags.
    The reviewer is a separate model call, not a proof of academic correctness.
    """

    def __init__(
        self, gemini: GeminiService | None = None, *,
        verify_questions: bool | None = None,
        shuffle_answers: bool | None = None,
    ) -> None:
        self._gemini = gemini
        self._owns_gemini = gemini is None
        self._verify_questions = verify_questions
        self._shuffle_answers = shuffle_answers

    def close(self) -> None:
        if self._owns_gemini and self._gemini is not None:
            self._gemini.close()
            self._gemini = None

    def generate(
        self,
        repository: QuestionRepository,
        *,
        document_id: str,
        topic: str,
        question_count: int = 5,
        difficulty: str = "medium",
        language: str = "Vietnamese",
        top_k: int = DEFAULT_TOP_K,
    ) -> QuestionGenerationResult | None:
        topic = topic.strip() if isinstance(topic, str) else ""
        language = language.strip() if isinstance(language, str) else ""
        if not topic or len(topic) > MAX_TOPIC_CHARACTERS:
            raise ValueError("topic must contain 1 to 300 characters.")
        if not language or len(language) > MAX_LANGUAGE_CHARACTERS:
            raise ValueError("language must contain 1 to 40 characters.")
        if type(question_count) is not int or not 1 <= question_count <= 10:
            raise ValueError("question_count must be from 1 to 10.")
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("difficulty must be easy, medium, or hard.")
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k must be from 1 to {MAX_TOP_K}.")

        # RLS-backed document lookup must precede Gemini calls, including embedding.
        document = repository.get_syllabus_document(document_id)
        if document is None:
            return None
        gemini = self._gemini or GeminiService()
        self._gemini = gemini
        verify_enabled = _env_flag(VERIFY_ENV, self._verify_questions)
        shuffle_enabled = _env_flag(SHUFFLE_ENV, self._shuffle_answers)
        vector = gemini.embed_text(
            topic, task_type="RETRIEVAL_QUERY", output_dimensionality=1536
        )
        matches = repository.match_syllabus_chunks(
            vector, match_count=top_k, document_id=document_id
        )
        chunks = _bounded_chunks(matches)
        title = document.get("title") if isinstance(document.get("title"), str) else ""
        if not chunks:
            return QuestionGenerationResult(
                status="insufficient_context", document_id=document_id,
                document_title=title, topic=topic, questions=(),
                message=(
                    "Không tìm thấy nội dung đề cương phù hợp để tạo câu hỏi cho chủ đề này."
                    if language.casefold().startswith("vi")
                    else "No relevant syllabus context was retrieved for this topic."
                ),
            )

        question_blueprint = build_question_blueprint(question_count, difficulty)
        prompt = build_generation_prompt(
            title=title, topic=topic, question_count=question_count,
            difficulty=difficulty, language=language,
            question_blueprint=question_blueprint, chunks=chunks,
        )
        generated_text = gemini.generate_text(
            prompt, system_instruction=SYSTEM_INSTRUCTION
        )
        questions, message = validate_question_output(
            generated_text,
            requested_count=question_count,
            difficulty=difficulty,
            retrieved_chunks=chunks,
            language=language,
        )
        if questions and verify_enabled:
            questions = _verify_questions(
                gemini, questions, title=title, topic=topic, difficulty=difficulty,
                language=language, question_blueprint=question_blueprint, chunks=chunks,
            )
        if questions and shuffle_enabled:
            questions = shuffle_question_answers(questions)
        return QuestionGenerationResult(
            status="generated" if questions else "insufficient_context",
            document_id=document_id,
            document_title=title,
            topic=topic,
            questions=questions,
            message=message,
        )