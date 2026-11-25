"""Read-only access to the import-time parser review report."""

from fastapi import APIRouter, HTTPException, Response

try:
    from backend.cloud_repository import CloudRepository
    from backend.network.cloud_api import CurrentIdentity

except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from cloud_repository import CloudRepository
    from network.cloud_api import CurrentIdentity


router = APIRouter()


def unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "reason": reason,
    }


def review_response(exam: dict) -> dict:
    extra = exam.get("extra")

    if (
        not isinstance(extra, dict)
        or "parser_review" not in extra
    ):
        return unavailable("missing")

    snapshot = extra["parser_review"]

    if not isinstance(snapshot, dict):
        return unavailable("invalid")

    if (
        snapshot.get("schema_version") != 1
        or snapshot.get("stage") != "import"
    ):
        return unavailable("unsupported")

    summary = snapshot.get("review_summary")

    items = snapshot.get("review_items")

    if (
        not isinstance(summary, dict)
        or not isinstance(items, list)
    ):
        return unavailable("invalid")

    total = summary.get("total_questions")
    needs = summary.get("needs_review")
    structural = summary.get("structural_issues")

    if any(
        type(n) is not int or n < 0
        for n in (total, needs, structural)
    ):
        return unavailable("invalid")

    if (
        not structural <= needs <= total
        or needs != len(items)
    ):
        return unavailable("invalid")

    records = {
        question["id"]

        for question in exam.get(
            "questions",
            [],
        )
    }

    if len(records) != total:
        return unavailable("stale")

    seen = set()

    clean_items = []

    for item in items:
        if not isinstance(item, dict):
            return unavailable("invalid")

        record_id = item.get(
            "question_record_id"
        )

        if (
            not isinstance(record_id, str)
            or record_id in seen
        ):
            return unavailable("invalid")

        if record_id not in records:
            return unavailable("stale")

        seen.add(record_id)

        codes = item.get("issue_codes")

        if (
            not isinstance(codes, list)
            or not codes
            or not all(
                isinstance(code, str)
                for code in codes
            )
        ):
            return unavailable("invalid")

        warning = item.get("warning")

        clean_items.append({
            "question_record_id": record_id,

            "issue_codes": codes,

            "warning": (
                warning

                if isinstance(warning, str)

                else None
            ),

            "requires_source_image": (
                item.get(
                    "requires_source_image"
                ) is True
            ),
        })

    return {
        "status": "available",

        "schema_version": 1,

        "stage": "import",

        "review_summary": {
            "total_questions": total,

            "needs_review": needs,

            "structural_issues": structural,
        },

        "review_items": clean_items,
    }


@router.get(
    "/api/exams/{exam_id}/parser-review"
)
def get_parser_review(
    exam_id: str,
    identity: CurrentIdentity,
    response: Response,
) -> dict:
    with CloudRepository(
        *identity
    ) as repository:

        exam = repository.get_exam_record(
            exam_id
        )

        if (
            exam is None
            or exam.get("id") != exam_id
            or exam.get("owner_id") != identity[0]
        ):
            raise HTTPException(
                404,
                "Không tìm thấy đề thi.",
            )

        response.headers[
            "Cache-Control"
        ] = "private, no-store"

        return {
            "success": True,

            "data": review_response(exam),
        }