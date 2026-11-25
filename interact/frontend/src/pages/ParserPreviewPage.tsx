import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { QuestionContent } from "../components/quiz/QuestionContent";

type TextBlock = {
    type: string;
    value: string;
};

type PreviewOption = {
    label: string;
    content: TextBlock[];
};

type PreviewQuestion = {
    question_id: number;
    page_number?: number | null;
    type: string;
    question_content: TextBlock[];
    mcp_options: PreviewOption[];
    section_instruction?: string;
    shared_passage?: string;
    parse_fallback?: boolean;
    requires_source_image?: boolean;
    parser_warning?: string | null;
};

type PreviewData = {
    filename: string;
    model: string;
    page_count: number;
    page_texts: string[];
    raw_page_texts?: string[];
    question_count: number;
    questions: PreviewQuestion[];
    diagnostics: {
        incomplete_options: number[];
        contaminated_options: number[];
        needs_review: number[];
        normalized_repairs?: Array<{
            question_number: number;
            kind: string;
            page_number: number;
        }>;
    };
    timings: {
        ocr_seconds: number | null;
        parser_seconds: number;
        preprocess_seconds?: number | null;
    };
};

type ApiEnvelope = {
    success: boolean;
    data?: PreviewData;
    detail?: string;
    message?: string;
};

const API = (import.meta.env.VITE_PARSER_PREVIEW_API_URL || "http://127.0.0.1:8001/api/parser-preview").replace(/\/+$/, "");

function blockText(blocks: TextBlock[] = []): string {
    return blocks.map((item) => item.value ?? "").join("\n");
}

async function requestPreview(
    endpoint: string,
    body: FormData,
): Promise<PreviewData> {
    let response: Response;

    try {
        response = await fetch(`${API}/${endpoint}`, {
            method: "POST",
            body,
        });
    } catch (cause) {
        throw new Error(
            "Không kết nối được Parser Preview backend tại 127.0.0.1:8000.",
            { cause },
        );
    }

    const result = (await response.json().catch(() => null)) as
        | ApiEnvelope
        | null;

    if (!response.ok || !result?.success || !result.data) {
        throw new Error(
            result?.detail ||
            result?.message ||
            `Parser Preview thất bại (HTTP ${response.status}).`,
        );
    }

    return result.data;
}

export function ParserPreviewPage() {
    const [file, setFile] = useState<File | null>(null);
    const [sourceFile, setSourceFile] = useState<File | null>(null);
    const [model, setModel] = useState("PaddleOCR-VL-1.6");
    const [preview, setPreview] = useState<PreviewData | null>(null);
    const [activeIndex, setActiveIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    const [operation, setOperation] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [showRawOcr, setShowRawOcr] = useState(false);

    async function runOcr(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (!file || loading) return;

        setLoading(true);
        setOperation("Đang gọi PaddleOCR và phân tích đề...");
        setError(null);

        try {
            const form = new FormData();
            form.append("file", file);
            form.append("model", model);

            const data = await requestPreview("ocr", form);

            setPreview(data);
            setSourceFile(file);
            setActiveIndex(0);
        } catch (cause) {
            setError(
                cause instanceof Error
                    ? cause.message
                    : "Có lỗi không xác định.",
            );
        } finally {
            setLoading(false);
            setOperation("");
        }
    }

    async function reprocess() {
        if (!preview || !sourceFile || loading) return;

        const originalOcr = preview.raw_page_texts;

        if (!originalOcr?.length) {
            setError(
                "Kết quả hiện tại không chứa OCR nguyên bản để đối chiếu lại PDF.",
            );
            return;
        }

        setLoading(true);
        setOperation(
            "Đang đọc lại tọa độ PDF và chạy parser, KHÔNG gọi PaddleOCR...",
        );
        setError(null);

        try {
            const form = new FormData();
            form.append("file", sourceFile);
            form.append("model", preview.model);
            form.append(
                "raw_page_texts_json",
                JSON.stringify(originalOcr),
            );

            const data = await requestPreview("reprocess", form);

            setPreview(data);
            setActiveIndex((previous) =>
                Math.min(
                    previous,
                    Math.max(0, data.questions.length - 1),
                ),
            );
        } catch (cause) {
            setError(
                cause instanceof Error
                    ? cause.message
                    : "Có lỗi không xác định.",
            );
        } finally {
            setLoading(false);
            setOperation("");
        }
    }

    function exportJson() {
        if (!preview) return;

        const blob = new Blob(
            [JSON.stringify(preview, null, 2)],
            { type: "application/json" },
        );

        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");

        anchor.href = url;
        anchor.download = "quizpdf-parser-preview.json";
        anchor.click();

        URL.revokeObjectURL(url);
    }

    const question = preview?.questions[activeIndex] ?? null;
    const pageNumber = question?.page_number ?? 1;

    const rawPage =
        preview?.raw_page_texts?.[pageNumber - 1] ??
        preview?.page_texts[pageNumber - 1] ??
        "";

    const problems = preview
        ? new Set([
            ...preview.diagnostics.incomplete_options,
            ...preview.diagnostics.contaminated_options,
            ...preview.diagnostics.needs_review,
        ])
        : new Set<number>();

    return (
        <div className="mx-auto max-w-7xl px-5 py-8 text-[var(--color-text)]">
            <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">
                        Parser Preview
                    </h1>
                    <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                        PaddleOCR thật · Không lưu database · Chạy lại toàn bộ parser và tọa độ PDF không cần OCR
                    </p>
                </div>

                <Link
                    to="/"
                    className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm"
                >
                    Về trang chủ
                </Link>
            </div>

            <form
                onSubmit={runOcr}
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5"
            >
                <div className="grid gap-4 md:grid-cols-[1fr_260px]">
                    <label className="block text-sm">
            <span className="mb-2 block font-medium">
              File PDF
            </span>
                        <input
                            type="file"
                            accept=".pdf,application/pdf"
                            onChange={(event) =>
                                setFile(event.target.files?.[0] ?? null)
                            }
                            className="w-full rounded-md border border-[var(--color-border)] p-2"
                        />
                    </label>

                    <label className="block text-sm">
            <span className="mb-2 block font-medium">
              Mô hình OCR
            </span>
                        <select
                            value={model}
                            onChange={(event) =>
                                setModel(event.target.value)
                            }
                            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-2"
                        >
                            <option value="PaddleOCR-VL-1.6">
                                PaddleOCR-VL-1.6
                            </option>
                            <option value="PP-OCRv6">
                                PP-OCRv6
                            </option>
                        </select>
                    </label>
                </div>

                <div className="mt-4 flex flex-wrap gap-3">
                    <button
                        type="submit"
                        disabled={!file || loading}
                        className="rounded-lg bg-[var(--color-primary)] px-5 py-2.5 font-medium text-white disabled:opacity-50"
                    >
                        Chạy PaddleOCR + Parser
                    </button>

                    <button
                        type="button"
                        disabled={!preview || !sourceFile || loading}
                        onClick={() => void reprocess()}
                        className="rounded-lg border border-[var(--color-primary)] px-5 py-2.5 font-medium text-[var(--color-primary)] disabled:opacity-50"
                    >
                        Đối chiếu PDF + phân tích lại — không OCR
                    </button>

                    <button
                        type="button"
                        disabled={!preview || loading}
                        onClick={exportJson}
                        className="rounded-lg border border-[var(--color-border)] px-5 py-2.5 disabled:opacity-50"
                    >
                        Xuất kết quả JSON
                    </button>
                </div>

                {loading && (
                    <p className="mt-3 text-sm text-[var(--color-primary)]">
                        {operation}
                    </p>
                )}

                {error && (
                    <p
                        role="alert"
                        className="mt-3 rounded-lg border border-red-300 p-3 text-sm text-red-600"
                    >
                        {error}
                    </p>
                )}
            </form>

            {preview && (
                <>
                    <div className="my-5 grid gap-3 sm:grid-cols-4">
                        {[
                            ["Số trang OCR", String(preview.page_count)],
                            ["Câu nhận diện", String(preview.question_count)],
                            ["Câu cần kiểm tra", String(problems.size)],
                            [
                                "Thời gian parser",
                                `${preview.timings.parser_seconds.toFixed(4)}s`,
                            ],
                        ].map(([label, value]) => (
                            <div
                                key={label}
                                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
                            >
                                <p className="text-xs text-[var(--color-text-muted)]">
                                    {label}
                                </p>
                                <p className="mt-1 text-xl font-bold">
                                    {value}
                                </p>
                            </div>
                        ))}
                    </div>

                    <p className="mb-5 text-sm text-[var(--color-text-secondary)]">
                        {preview.timings.ocr_seconds === null
                            ? "Lượt này không gọi PaddleOCR."
                            : `PaddleOCR: ${preview.timings.ocr_seconds.toFixed(2)} giây.`}

                        {preview.timings.preprocess_seconds != null
                            ? ` Đối chiếu PDF: ${preview.timings.preprocess_seconds.toFixed(3)} giây.`
                            : ""}

                        {` Thiếu A–D: ${
                            preview.diagnostics.incomplete_options.join(", ") || "Không"
                        }.`}

                        {` Tiêu đề lẫn vào đáp án: ${
                            preview.diagnostics.contaminated_options.join(", ") || "Không"
                        }.`}
                    </p>

                    {Boolean(
                        preview.diagnostics.normalized_repairs?.length,
                    ) && (
                        <p className="mb-5 text-sm text-[var(--color-text-secondary)]">
                            Đã đối chiếu lại:{" "}
                            {preview.diagnostics.normalized_repairs
                                ?.map(
                                    (item) =>
                                        `Câu ${item.question_number} (${item.kind})`,
                                )
                                .join(", ")}
                            .
                        </p>
                    )}

                    <div className="grid gap-5 lg:grid-cols-[230px_minmax(0,1fr)]">
                        <aside className="self-start rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 lg:sticky lg:top-20">
                            <h2 className="mb-3 font-semibold">
                                Danh sách câu hỏi
                            </h2>

                            <div className="grid max-h-[65vh] grid-cols-5 gap-2 overflow-y-auto lg:grid-cols-4">
                                {preview.questions.map((item, index) => (
                                    <button
                                        key={`${item.question_id}-${index}`}
                                        type="button"
                                        onClick={() => setActiveIndex(index)}
                                        title={
                                            problems.has(item.question_id)
                                                ? "Cần kiểm tra nội dung"
                                                : "Đã nhận diện"
                                        }
                                        className={`rounded-md border px-2 py-2 text-sm ${
                                            activeIndex === index
                                                ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] font-bold"
                                                : problems.has(item.question_id)
                                                    ? "border-amber-500/50"
                                                    : "border-[var(--color-border)]"
                                        }`}
                                    >
                                        {item.question_id}
                                    </button>
                                ))}
                            </div>
                        </aside>

                        <section className="min-w-0 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
                            {!question ? (
                                <p>
                                    Parser chưa nhận diện được câu hỏi nào.
                                </p>
                            ) : (
                                <>
                                    <div className="mb-5 flex flex-wrap items-center gap-3 border-b border-[var(--color-border)] pb-4">
                                        <h2 className="text-xl font-bold">
                                            Câu {question.question_id}
                                        </h2>

                                        <span className="text-sm text-[var(--color-text-muted)]">
                      Trang {pageNumber} · {question.type}
                    </span>

                                        {question.parse_fallback && (
                                            <span className="rounded-md border border-amber-500/50 px-2 py-1 text-xs">
                        Cần đối chiếu PDF gốc
                      </span>
                                        )}
                                    </div>

                                    {question.section_instruction && (
                                        <div className="mb-5 rounded-lg border border-[var(--color-border)] p-4">
                                            <p className="mb-2 text-xs font-semibold uppercase text-[var(--color-text-muted)]">
                                                Hướng dẫn nhóm câu
                                            </p>
                                            <QuestionContent
                                                content={question.section_instruction}
                                            />
                                        </div>
                                    )}

                                    {question.shared_passage && (
                                        <div className="mb-5 rounded-lg border border-[var(--color-border)] p-4">
                                            <p className="mb-2 text-xs font-semibold uppercase text-[var(--color-text-muted)]">
                                                Đoạn đọc chung
                                            </p>
                                            <QuestionContent
                                                content={question.shared_passage}
                                            />
                                        </div>
                                    )}

                                    <div className="mb-5 text-base font-medium">
                                        <QuestionContent
                                            content={blockText(
                                                question.question_content,
                                            )}
                                        />
                                    </div>

                                    <div className="space-y-3">
                                        {question.mcp_options.map((option) => (
                                            <div
                                                key={option.label}
                                                className="flex gap-3 rounded-lg border border-[var(--color-border)] px-4 py-3"
                                            >
                        <span className="font-bold">
                          {option.label}.
                        </span>

                                                <div className="min-w-0 flex-1">
                                                    <QuestionContent
                                                        content={blockText(
                                                            option.content,
                                                        )}
                                                    />
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="mt-6 border-t border-[var(--color-border)] pt-4">
                                        <button
                                            type="button"
                                            onClick={() =>
                                                setShowRawOcr(!showRawOcr)
                                            }
                                            className="text-sm font-medium text-[var(--color-primary)]"
                                        >
                                            {showRawOcr
                                                ? "Ẩn OCR gốc"
                                                : "Xem OCR gốc của trang"}
                                        </button>

                                        {showRawOcr && (
                                            <pre className="mt-3 max-h-[440px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--color-bg)] p-4 text-xs">
                        {rawPage ||
                            "Trang không có văn bản OCR."}
                      </pre>
                                        )}
                                    </div>

                                    <div className="mt-6 flex justify-between gap-3">
                                        <button
                                            type="button"
                                            disabled={activeIndex === 0}
                                            onClick={() =>
                                                setActiveIndex(activeIndex - 1)
                                            }
                                            className="rounded-lg border border-[var(--color-border)] px-4 py-2 disabled:opacity-40"
                                        >
                                            Câu trước
                                        </button>

                                        <button
                                            type="button"
                                            disabled={
                                                activeIndex >=
                                                preview.questions.length - 1
                                            }
                                            onClick={() =>
                                                setActiveIndex(activeIndex + 1)
                                            }
                                            className="rounded-lg border border-[var(--color-border)] px-4 py-2 disabled:opacity-40"
                                        >
                                            Câu tiếp
                                        </button>
                                    </div>
                                </>
                            )}
                        </section>
                    </div>
                </>
            )}
        </div>
    );
}