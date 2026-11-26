"""Cloud OCR with bounded recovery and persistent batch results."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time

from pathlib import Path
from typing import Any, Callable

import pymupdf
import requests

from urllib3.exceptions import ReadTimeoutError

from . import paddle_client as client
from .registered_ocr_context import (
    remember_completed_batch,
    remember_supplemental_batch,
    supplemental_candidates,
)

from .ocr_job_journal import (
    DEFAULT_PATH,
    JournalStateError,
    OCRJobJournal,
    upload_scope,
)


from .ocr_submission_resilience import (
    LocalSubmissionError,
    open_submission_file,
    record_submission_issue,
    report_blocked_submission,
)

logger = logging.getLogger("quizpdf")


class UnconnectedSubmissionError(client.PaddleOCRTimeoutError):
    """All attempted submissions ended in direct ConnectTimeout."""


def _optional_payload(model: str) -> dict[str, bool]:
    if model == "PaddleOCR-VL-1.6":
        return {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
        }

    return {}


def _log_geometry(
    pages: list[dict],
    job_id: str,
) -> None:

    for index, page in enumerate(pages, 1):
        root = page.get("pruned_result")
        root = root if isinstance(root, dict) else {}

        pre = root.get("doc_preprocessor_res")
        pre = pre if isinstance(pre, dict) else {}

        settings = pre.get("model_settings")
        settings = settings if isinstance(settings, dict) else {}

        blocks = root.get("parsing_res_list")

        root_settings = root.get("model_settings")
        root_settings = (
            root_settings
            if isinstance(root_settings, dict)
            else {}
        )

        logger.info(
            "paddleocr:geometry job=%s page=%d width=%s height=%s "
            "preprocessor=%s angle=%s unwarping=%s orientation=%s blocks=%d",
            job_id,
            index,
            root.get("width"),
            root.get("height"),
            root_settings.get("use_doc_preprocessor"),
            pre.get("angle"),
            settings.get("use_doc_unwarping"),
            settings.get("use_doc_orientation_classify"),
            len(blocks) if isinstance(blocks, list) else 0,
        )


def _recoverable(error: Exception) -> bool:
    if isinstance(error, client.PaddleOCRTimeoutError):
        return True

    if isinstance(error, client.PaddleOCRAPIError):
        return error.status_code in client.RETRYABLE_GET_STATUSES

    cause = error.__cause__

    return isinstance(
        cause,
        requests.exceptions.ConnectionError,
    ) and not isinstance(
        cause,
        requests.exceptions.SSLError,
    )


def _with_recovery(
    operation: Callable[[float], Any],
    *,
    job_id: str,
    stage: str,
    seconds: float,
    recovery: float,
) -> Any:

    try:
        return operation(
            time.monotonic() + seconds
        )

    except Exception as exc:
        if not _recoverable(exc):
            raise

        logger.warning(
            "paddleocr:recover job=%s stage=%s "
            "extra_seconds=%.1f error_type=%s",
            job_id,
            stage,
            recovery,
            type(exc).__name__,
        )

    return operation(
        time.monotonic() + recovery
    )


def _poll(
    job_url,
    job_id,
    headers,
    timeout,
    deadline,
):
    interval = 3.0

    while True:
        response = client._get_with_retries(
            f"{job_url.rstrip('/')}/{job_id}",
            stage="job status polling",
            job_id=job_id,
            headers=headers,
            timeout=timeout,
            deadline=deadline,
        )

        data = response.json()

        status = (
            data.get("data")
            if isinstance(data, dict)
            else None
        )

        if not isinstance(status, dict):
            raise RuntimeError(
                "PaddleOCR returned invalid job status."
            )

        state = status.get("state")

        if state == "done":
            urls = status.get("resultUrl")

            url = (
                urls.get("jsonUrl")
                if isinstance(urls, dict)
                else None
            )

            if not isinstance(url, str) or not url:
                raise RuntimeError(
                    "PaddleOCR completed without a result URL."
                )

            return url

        if state == "failed":
            raise client.PaddleOCRJobFailedError(
                f"PaddleOCR job {job_id} failed."
            )

        if state not in {"pending", "running"}:
            raise RuntimeError(
                f"Unknown PaddleOCR job state: {state!r}"
            )

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise client.PaddleOCRTimeoutError(
                f"Timed out waiting for PaddleOCR job {job_id}."
            )

        time.sleep(min(interval, remaining))

        interval = min(
            interval * 1.5,
            15.0,
        )


def _submit_attempts() -> int:
    raw = os.getenv(
        "PADDLEOCR_SUBMIT_MAX_ATTEMPTS",
        "2",
    )

    try:
        attempts = int(raw)

    except ValueError as exc:
        raise ValueError(
            "PADDLEOCR_SUBMIT_MAX_ATTEMPTS must be 1, 2 or 3."
        ) from exc

    if attempts not in (1, 2, 3):
        raise ValueError(
            "PADDLEOCR_SUBMIT_MAX_ATTEMPTS must be 1, 2 or 3."
        )

    return attempts


def _submission_error_kind(
    error: BaseException,
) -> str:

    if isinstance(
        error,
        requests.exceptions.ConnectTimeout,
    ):
        return "connect_timeout"

    pending = [error]
    seen = set()

    write_timeout = False
    read_timeout = False

    while pending:
        item = pending.pop()

        if id(item) in seen:
            continue

        seen.add(id(item))

        if isinstance(
            item,
            (
                requests.exceptions.ReadTimeout,
                ReadTimeoutError,
            ),
        ):
            read_timeout = True

        if isinstance(item, BaseException):
            pending.extend([
                item.__cause__,
                item.__context__,
                *item.args,
            ])

        elif isinstance(item, (list, tuple)):
            pending.extend(item)

        elif isinstance(item, str):
            if "write operation timed out" in item.lower():
                write_timeout = True

    if write_timeout:
        return "write_timeout"

    if read_timeout:
        return "read_timeout"

    if client._nested_timeout(error):
        return "unknown_timeout"

    return "transport_error"


def _submit_job(
    batch,
    *,
    model,
    job_url,
    headers,
    timeout,
) -> str:

    attempts = _submit_attempts()

    upload_timeout = client._upload_timeout(
        timeout[1]
    )

    payload = _optional_payload(model)

    logger.info(
        "paddleocr:submit_options model=%s options=%s",
        model,
        json.dumps(payload),
    )

    for attempt in range(1, attempts + 1):
        started = time.monotonic()

        logger.info(
            "paddleocr:submit status=started model=%s first_page=%d "
            "attempt=%d/%d socket_timeout=%.1f read_timeout=%.1f",
            model,
            batch.start_page + 1,
            attempt,
            attempts,
            *upload_timeout,
        )

        try:
            with open_submission_file(batch.path) as pdf:
                response = requests.post(
                    job_url,
                    headers=headers,
                    data={
                        "model": model,
                        "optionalPayload": json.dumps(
                            payload
                        ),
                    },
                    files={
                        "file": (
                            batch.path.name,
                            pdf,
                            "application/pdf",
                        ),
                    },
                    timeout=upload_timeout,
                    allow_redirects=False,
                )

        except (
            requests.RequestException,
            TimeoutError,
        ) as exc:

            kind = _submission_error_kind(exc)

            can_retry = (
                kind == "connect_timeout"
                and attempt < attempts
            )

            outcome = (
                "not_connected"
                if kind == "connect_timeout"
                else "unknown"
            )

            logger.warning(
                "paddleocr:submit status=failed kind=%s "
                "outcome=%s retry=%s attempt=%d/%d "
                "first_page=%d elapsed=%.2f error_type=%s",
                kind,
                outcome,
                can_retry,
                attempt,
                attempts,
                batch.start_page + 1,
                time.monotonic() - started,
                type(exc).__name__,
            )

            if can_retry:
                delay = float(
                    2 ** (attempt - 1)
                )

                logger.info(
                    "paddleocr:submit status=backoff delay=%.1f",
                    delay,
                )

                time.sleep(delay)

                continue

            if kind == "connect_timeout":
                raise UnconnectedSubmissionError(
                    "PaddleOCR connection timed out "
                    "during PDF upload."
                ) from exc

            if kind.endswith("timeout"):
                raise client.PaddleOCRTimeoutError(
                    f"PaddleOCR PDF upload {kind}; "
                    "submission outcome unknown. "
                    "Automatic resubmission disabled."
                ) from exc

            raise RuntimeError(
                "PaddleOCR PDF upload transport error; "
                "submission outcome unknown. "
                "Automatic resubmission disabled."
            ) from exc

        status = response.status_code

        if (
            isinstance(status, int)
            and 300 <= status < 400
        ):
            logger.warning(
                "paddleocr:submit status=redirect_blocked "
                "http=%d retry=False",
                status,
            )

            raise client.PaddleOCRAPIError(
                "submit",
                status,
            )

        if not response.ok:
            logger.warning(
                "paddleocr:submit status=http_error "
                "http=%s retry=False",
                status,
            )

            client._check_response(
                response,
                "submit",
            )

        try:
            data = response.json()

        except ValueError as exc:
            logger.warning(
                "paddleocr:submit status=invalid_json "
                "outcome=unknown retry=False"
            )

            raise RuntimeError(
                "PaddleOCR returned invalid submission JSON; "
                "submission outcome unknown."
            ) from exc

        result = (
            data.get("data")
            if isinstance(data, dict)
            else None
        )

        job_id = (
            result.get("jobId")
            if isinstance(result, dict)
            else None
        )

        if (
            type(job_id) not in (str, int)
            or not str(job_id).strip()
        ):
            logger.warning(
                "paddleocr:submit status=missing_job_id "
                "outcome=unknown retry=False"
            )

            raise RuntimeError(
                "PaddleOCR did not return a job ID; "
                "submission outcome unknown."
            )

        return str(job_id)

    raise AssertionError(
        "Unreachable OCR submission state"
    )


def _process_batch(
    batch,
    *,
    model,
    job_url,
    headers,
    timeout,
    poll_timeout,
    recovery,
    result_timeout,
    journal=None,
):

    key = None
    record = None

    if journal is not None:

        _submit_attempts()

        client._upload_timeout(
            timeout[1]
        )

        key = journal.batch_key(
            batch.start_page,
            batch.page_count,
            model,
        )

        reservation = journal.reserve(key)
        record = reservation.record

        if reservation.created:

            try:
                job_id = _submit_job(
                    batch,
                    model=model,
                    job_url=job_url,
                    headers=headers,
                    timeout=timeout,
                )

            except (UnconnectedSubmissionError, LocalSubmissionError):
                journal.release_unconnected(
                    key,
                    record.claim_id,
                )
                logger.info("paddleocr:journal status=unconnected_released")
                raise

            except Exception as exc:
                record_submission_issue(journal, key, record.claim_id, exc)
                raise

            try:
                journal.accept(
                    key,
                    record.claim_id,
                    job_id,
                )

            except Exception as exc:
                record_submission_issue(
                    journal, key, record.claim_id, exc,
                    stage="accept", observed_job_id=job_id,
                )
                logger.error(
                    "paddleocr:journal status=accept_failed job=%s",
                    job_id,
                )

                raise

            logger.info(
                "paddleocr:journal status=accepted job=%s",
                job_id,
            )

        elif record.state == "submitting":
            report_blocked_submission(journal, key, record.claim_id)

            raise JournalStateError(
                "Previous OCR submission is unconfirmed; "
                "automatic resubmission disabled."
            )

        elif record.state == "failed":

            raise client.PaddleOCRJobFailedError(
                f"PaddleOCR job {record.job_id} previously failed."
            )

        else:

            job_id = record.job_id

            logger.info(
                "paddleocr:journal status=resumed state=%s job=%s",
                record.state,
                job_id,
            )

            # A completed job may already have a
            # durable, validated local result.
            if record.state == "completed":

                cached = journal.load_result(
                    key,
                    record.claim_id,
                    job_id,
                    page_count=batch.page_count,
                )

                if cached is not None:

                    pages, info = cached

                    logger.info(
                        "paddleocr:result_cache status=hit job=%s pages=%d",
                        job_id,
                        len(pages),
                    )

                    _log_geometry(
                        pages,
                        job_id,
                    )

                    remember_completed_batch(
                        journal, batch, model, record.claim_id,
                        job_id, pages,
                    )

                    return job_id, pages, info

                # Missing or corrupted cache:
                # fetch using the original Job ID.
                logger.info(
                    "paddleocr:result_cache status=miss job=%s",
                    job_id,
                )

    else:

        job_id = _submit_job(
            batch,
            model=model,
            job_url=job_url,
            headers=headers,
            timeout=timeout,
        )

    logger.info(
        "paddleocr:upload status=completed "
        "job=%s first_page=%d",
        job_id,
        batch.start_page + 1,
    )

    status_timeout = (
        timeout[0],
        min(timeout[1], 30.0),
    )

    try:
        url = _with_recovery(
            lambda deadline: _poll(
                job_url,
                job_id,
                headers,
                status_timeout,
                deadline,
            ),
            job_id=job_id,
            stage="poll",
            seconds=poll_timeout,
            recovery=recovery,
        )

    except client.PaddleOCRJobFailedError:

        if journal is not None:

            if record.state == "completed":
                raise JournalStateError(
                    "Previously completed OCR job "
                    "is no longer available."
                )

            journal.finish(
                key,
                record.claim_id,
                failed=True,
            )

        raise

    # Never forward the provider's Bearer token
    # to the result download URL.
    response = _with_recovery(
        lambda deadline: client._get_with_retries(
            url,
            stage="result download",
            job_id=job_id,
            timeout=timeout,
            deadline=deadline,
        ),
        job_id=job_id,
        stage="result",
        seconds=result_timeout,
        recovery=recovery,
    )

    lines = [
        json.loads(line)
        for line in response.text.splitlines()
        if line.strip()
    ]

    pages, info = client._normalize_pages(
        lines,
        model,
    )

    _log_geometry(
        pages,
        job_id,
    )

    if journal is not None:

        if len(pages) != batch.page_count:
            raise RuntimeError(
                f"OCR page mismatch: "
                f"expected {batch.page_count}, "
                f"got {len(pages)}."
            )

        # Save the result and completed state
        # in one SQLite transaction.
        saved = journal.finish_with_result(
            key,
            record.claim_id,
            job_id,
            pages,
            info,
            page_count=batch.page_count,
        )

        logger.info(
            "paddleocr:result_cache status=%s job=%s pages=%d",
            "saved" if saved else "too_large",
            job_id,
            len(pages),
        )

        if saved:
            remember_completed_batch(
                journal, batch, model, record.claim_id,
                job_id, pages,
            )

    return job_id, pages, info


def recognize_pdf(
    path: str,
    model: str,
    *,
    user_id: str | None = None,
    filename: str | None = None,
    journal_path: Path = DEFAULT_PATH,
) -> tuple[str, dict[str, Any]]:

    if (user_id is None) != (filename is None):
        raise ValueError(
            "OCR journal requires both user_id and filename."
        )

    token = os.getenv(
        "PADDLEOCR_ACCESS_TOKEN"
    )

    if not token:
        raise RuntimeError(
            "Backend is missing PADDLEOCR_ACCESS_TOKEN."
        )

    job_url = os.getenv("JOB_URL") or (
        os.getenv(
            "PADDLEOCR_BASE_URL",
            "https://paddleocr.aistudio-app.com",
        ).rstrip("/")
        + "/api/v2/ocr/jobs"
    )

    request_timeout = client._positive_number(
        "PADDLEOCR_REQUEST_TIMEOUT",
        "180",
    )

    poll_timeout = client._positive_number(
        "PADDLEOCR_POLL_TIMEOUT",
        "300",
    )

    result_timeout = client._positive_number(
        "PADDLEOCR_RESULT_TIMEOUT",
        "300",
    )

    recovery = client._positive_number(
        "PADDLEOCR_RECOVERY_TIMEOUT",
        "120",
    )

    if recovery > 300:
        raise ValueError(
            "PADDLEOCR_RECOVERY_TIMEOUT "
            "must not exceed 300 seconds."
        )

    max_pages = int(
        client._positive_number(
            "PADDLEOCR_MAX_PAGES_PER_JOB",
            "2",
        )
    )

    max_bytes = int(
        client._positive_number(
            "PADDLEOCR_MAX_UPLOAD_MB",
            "1.5",
        ) * 1024 * 1024
    )

    if max_pages < 1 or max_bytes < 1:
        raise ValueError(
            "Invalid OCR batch size."
        )

    alternate = os.getenv(
        "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL",
        "PP-OCRv6",
    ).strip()

    if alternate not in {"", "PP-OCRv6"}:
        raise ValueError(
            "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL "
            "must be PP-OCRv6 or empty."
        )

    headers = {
        "Authorization": f"Bearer {token}",
    }

    timeout = (
        min(10.0, request_timeout),
        request_timeout,
    )

    supplemental_attempted = False
    all_pages = []
    jobs = []
    data_info = {}

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-ocr-",
    ) as directory:

        workdir = Path(directory)

        normalized, converted = (
            client._normalize_vector_pdf(
                Path(path),
                workdir / "normalized.pdf",
            )
        )

        batches = client._batches(
            normalized,
            workdir,
            max_pages=max_pages,
            max_bytes=max_bytes,
        )

        journal = None

        if user_id is not None:

            if not batches or any(
                batch.page_count is None
                for batch in batches
            ):
                raise ValueError(
                    "Cannot journal an unknown PDF batch layout."
                )

            plan = {
                "version": 1,

                "job_url": job_url,

                "credential_sha256":
                    hashlib.sha256(
                        token.encode()
                    ).hexdigest(),

                "pymupdf": pymupdf.VersionBind,

                "vector_dpi": os.getenv(
                    "PADDLEOCR_VECTOR_DPI",
                    "220",
                ),

                "vector_quality": os.getenv(
                    "PADDLEOCR_VECTOR_JPEG_QUALITY",
                    "88",
                ),

                "max_pages": max_pages,

                "max_bytes": max_bytes,

                "rasterized_pages": converted,

                "batches": [
                    [
                        batch.start_page,
                        batch.page_count,
                    ]
                    for batch in batches
                ],

                "options": _optional_payload(model),

                "alternate": alternate,

                "alternate_options":
                    _optional_payload(alternate),
            }

            fingerprint = hashlib.sha256(
                json.dumps(
                    plan,
                    sort_keys=True,
                ).encode()
            ).hexdigest()

            scope = upload_scope(
                user_id=user_id,
                filename=filename,

                source_sha256=hashlib.sha256(
                    Path(path).read_bytes()
                ).hexdigest(),

                model=model,

                plan_fingerprint=fingerprint,
            )

            journal = OCRJobJournal(
                scope,
                journal_path,
            )

        for index, batch in enumerate(
            batches,
            1,
        ):

            logger.info(
                "paddleocr:batch status=started "
                "index=%d total=%d first_page=%d",
                index,
                len(batches),
                batch.start_page + 1,
            )

            def run(selected_model):
                return _process_batch(
                    batch,
                    model=selected_model,
                    job_url=job_url,
                    headers=headers,
                    timeout=timeout,
                    poll_timeout=poll_timeout,
                    recovery=recovery,
                    result_timeout=result_timeout,
                    journal=journal,
                )

            def check_count(pages):
                if (
                    batch.page_count is not None
                    and len(pages) != batch.page_count
                ):
                    raise RuntimeError(
                        f"OCR page mismatch for batch {index}: "
                        f"expected {batch.page_count}, "
                        f"got {len(pages)}."
                    )

            used_fallback = False
            supplemental_pages = None

            try:
                job_id, pages, info = run(model)

                history = [
                    {
                        "job_id": job_id,
                        "model": model,
                    }
                ]

            except client.PaddleOCRJobFailedError:

                if (
                    model != "PaddleOCR-VL-1.6"
                    or not alternate
                ):
                    raise

                job_id, pages, info = run(
                    alternate
                )

                used_fallback = True

                history = [
                    {
                        "job_id": job_id,
                        "model": alternate,
                    }
                ]

            check_count(pages)

            missing = [
                offset
                for offset, page in enumerate(pages)
                if not client._text_from(page)
            ]

            missing_vector = any(
                batch.start_page + offset + 1
                in converted
                for offset in missing
            )

            if (
                missing
                and not used_fallback
                and model == "PaddleOCR-VL-1.6"
                and alternate
                and (
                    len(missing) == len(pages)
                    or missing_vector
                )
            ):

                other_job, other_pages, other_info = (
                    run(alternate)
                )

                check_count(other_pages)
                supplemental_pages = other_pages

                if len(other_pages) != len(pages):
                    raise RuntimeError(
                        "Fallback OCR returned mismatched page count."
                    )

                for offset in missing:
                    if client._text_from(
                        other_pages[offset]
                    ):
                        pages[offset] = (
                            other_pages[offset]
                        )

                info.update(other_info)

                history.append({
                    "job_id": other_job,
                    "model": alternate,
                })

            # Geometry-only OCR: never replace primary text or alter raw history.
            if journal is not None and model == "PaddleOCR-VL-1.6":
                offsets = supplemental_candidates(path, batch, pages, converted)
                if offsets:
                    try:
                        if supplemental_pages is None and not supplemental_attempted:
                            supplemental_attempted = True
                            logger.info("paddleocr:supplemental status=started first_page=%d", batch.start_page + 1)
                            _, supplemental_pages, _ = run("PP-OCRv6")
                            check_count(supplemental_pages)
                        if supplemental_pages is not None:
                            linked = remember_supplemental_batch(
                                journal, batch, pages, supplemental_pages, offsets,
                            )
                            logger.info("paddleocr:supplemental status=completed linked=%d", linked)
                    except Exception as exc:
                        # Optional evidence must not fail the primary OCR.
                        # Never retry an ambiguous submission outside the journal.
                        supplemental_attempted = True
                        logger.warning("paddleocr:supplemental status=unavailable type=%s", type(exc).__name__)

            if len(batches) > 1:

                for offset, page in enumerate(pages):
                    page["source_page_number"] = (
                        batch.start_page + offset + 1
                    )

            all_pages.extend(pages)

            data_info.update(info)

            jobs.append({
                "first_page": batch.start_page + 1,
                "page_count": batch.page_count,
                "jobs": history,
            })

            logger.info(
                "paddleocr:batch status=completed "
                "index=%d total=%d",
                index,
                len(batches),
            )

    raw = {
        "job_id": jobs[0]["jobs"][0]["job_id"],
        "data_info": data_info,
        "pages": all_pages,
    }

    if len(jobs) > 1:

        raw["job_ids"] = [
            item["jobs"][-1]["job_id"]
            for item in jobs
        ]

        raw["batches"] = jobs

    elif len(jobs[0]["jobs"]) > 1:

        raw["batches"] = jobs

    if converted:
        raw["rasterized_pages"] = converted

    texts = client.extract_page_texts(raw)

    if not any(
        text.strip()
        for text in texts
    ):
        raise client.PaddleOCRNoTextError(
            "PaddleOCR returned no recognizable text."
        )

    return "\n\n".join(texts), raw