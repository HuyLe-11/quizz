import { useState } from "react";
import { cropQuestionImage } from "../../services/api";
import type { Question } from "../../types/types";

const FORMAT_DEPENDENT =
    /\bunderlined\s+(?:part|word|phrase|letters?|characters?|sound)s?\b|\bgạch\s+chân\b/iu;

export function SourceFidelityView({
                                       question,
                                   }: {
    question: Question;
}) {
    if (!FORMAT_DEPENDENT.test(question.question)) {
        return null;
    }

    return (
        <SourceImagePanel
            key={JSON.stringify([
                question.question_record_id,
                question.question_id,
                question.question_image_url ?? null,
            ])}
            question={question}
        />
    );
}

function SourceImagePanel({
                              question,
                          }: {
    question: Question;
}) {
    const [open, setOpen] = useState(false);

    const [imageUrl, setImageUrl] = useState(
        question.question_image_url || "",
    );

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const recordId = question.question_record_id;

    const canRequest = Boolean(
        recordId && question.has_source_pdf !== false,
    );

    async function requestImage() {
        if (!recordId || !canRequest || loading) {
            return;
        }

        setLoading(true);
        setError("");

        try {
            const result = await cropQuestionImage(
                recordId,
                false,
            );

            if (
                result.crop_status !== "success" ||
                !result.image_url
            ) {
                throw new Error(
                    "Không thể lấy ảnh câu hỏi gốc.",
                );
            }

            setImageUrl(result.image_url);
        } catch {
            setError(
                "Không thể tải ảnh gốc. Hãy thử lại.",
            );
        } finally {
            setLoading(false);
        }
    }

    function toggle() {
        if (open) {
            setOpen(false);
            return;
        }

        setOpen(true);

        if (!imageUrl && canRequest) {
            void requestImage();
        }
    }

    return (
        <section
            className="mb-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4"
            aria-label="Đối chiếu định dạng PDF gốc"
        >
            <p className="text-sm font-medium text-[var(--color-text)]">
                Câu hỏi phụ thuộc vào phần gạch chân
            </p>

            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                Hãy đối chiếu ảnh gốc nếu văn bản OCR
                không thể hiện đầy đủ định dạng.
                Ảnh có thể chứa đáp án hoặc dấu chấm tay.
            </p>

            {imageUrl || canRequest ? (
                <button
                    type="button"
                    onClick={toggle}
                    aria-expanded={open}
                    className="mt-3 rounded-md border border-[var(--color-border)] px-3 py-2 text-sm font-medium text-[var(--color-primary)]"
                >
                    {open
                        ? "Ẩn ảnh gốc"
                        : "Xem phần gạch chân trên PDF"}
                </button>
            ) : (
                <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
                    Không có ảnh PDF gốc để đối chiếu.
                </p>
            )}

            {open && (
                <div
                    className="mt-3"
                    aria-busy={loading}
                >
                    {loading && (
                        <p
                            role="status"
                            className="text-sm"
                        >
                            Đang tải ảnh gốc...
                        </p>
                    )}

                    {error && (
                        <p
                            role="alert"
                            className="text-sm text-[var(--color-danger)]"
                        >
                            {error}
                        </p>
                    )}

                    {imageUrl && !error && (
                        <img
                            src={imageUrl}
                            alt={`Ảnh gốc câu ${question.question_id} gồm nội dung và các lựa chọn`}
                            className="w-full rounded-lg border border-[var(--color-border)] bg-white object-contain"
                            onError={() =>
                                setError(
                                    "Ảnh không tải được hoặc liên kết đã hết hạn.",
                                )
                            }
                        />
                    )}

                    {error && canRequest && (
                        <button
                            type="button"
                            disabled={loading}
                            onClick={() => void requestImage()}
                            className="mt-2 text-sm font-medium text-[var(--color-primary)] disabled:opacity-50"
                        >
                            Tải lại ảnh
                        </button>
                    )}
                </div>
            )}
        </section>
    );
}