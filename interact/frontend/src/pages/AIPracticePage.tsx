import { useEffect, useState, type FormEvent } from "react";

import {
    generateSyllabusQuestions,
    getSyllabi,
    uploadSyllabus,
    type GeneratedSyllabusQuestion,
    type SyllabusDocument,
    type SyllabusQuestionGenerationResult,
} from "../services/api";

const fieldClassName = "mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]";
const cardClassName = "rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5";

export function AIPracticePage() {
    const [syllabi, setSyllabi] = useState<SyllabusDocument[]>([]);
    const [selectedId, setSelectedId] = useState("");
    const [loadingSyllabi, setLoadingSyllabi] = useState(true);
    const [syllabusError, setSyllabusError] = useState<string | null>(null);
    const [refreshKey, setRefreshKey] = useState(0);

    const [file, setFile] = useState<File | null>(null);
    const [title, setTitle] = useState("");
    const [subject, setSubject] = useState("");
    const [uploading, setUploading] = useState(false);
    const [uploadMessage, setUploadMessage] = useState<string | null>(null);

    const [topic, setTopic] = useState("");
    const [questionCount, setQuestionCount] = useState(5);
    const [difficulty, setDifficulty] = useState<"easy" | "medium" | "hard">("medium");
    const [generating, setGenerating] = useState(false);
    const [generationError, setGenerationError] = useState<string | null>(null);
    const [result, setResult] = useState<SyllabusQuestionGenerationResult | null>(null);
    const [answers, setAnswers] = useState<Record<number, number>>({});

    useEffect(() => {
        let active = true;
        setLoadingSyllabi(true);
        setSyllabusError(null);
        void getSyllabi().then((documents) => {
            if (!active) return;
            setSyllabi(documents);
            setSelectedId((current) =>
                documents.some((document) => document.document_id === current)
                    ? current
                    : documents[0]?.document_id ?? "",
            );
        }).catch((error: unknown) => {
            if (active) {
                setSyllabusError(
                    error instanceof Error ? error.message : "Không thể tải danh sách đề cương.",
                );
            }
        }).finally(() => {
            if (active) setLoadingSyllabi(false);
        });
        return () => { active = false; };
    }, [refreshKey]);

    async function handleUpload(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (!file || !title.trim()) {
            setUploadMessage("Chọn file PDF và nhập tiêu đề đề cương.");
            return;
        }
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            setUploadMessage("Vui lòng chọn file PDF.");
            return;
        }

        setUploading(true);
        setUploadMessage(null);
        setSyllabusError(null);
        try {
            const uploaded = await uploadSyllabus(file, title.trim(), subject.trim() || undefined);
            setSelectedId(uploaded.document_id);
            setUploadMessage(`Đã tải đề cương lên (${uploaded.chunk_count} đoạn).`);
            setFile(null);
            setTitle("");
            setSubject("");
            const input = document.getElementById("syllabus-pdf") as HTMLInputElement | null;
            if (input) input.value = "";
            setRefreshKey((value) => value + 1);
        } catch (error) {
            setUploadMessage(
                error instanceof Error ? error.message : "Không thể tải đề cương lên.",
            );
        } finally {
            setUploading(false);
        }
    }

    async function handleGenerate(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (!selectedId || !topic.trim()) return;
        setGenerating(true);
        setGenerationError(null);
        setResult(null);
        setAnswers({});
        try {
            const generated = await generateSyllabusQuestions(selectedId, {
                topic: topic.trim(),
                question_count: questionCount,
                difficulty,
                language: "vi",
            });
            setResult(generated);
        } catch (error) {
            setGenerationError(
                error instanceof Error ? error.message : "Không thể tạo câu hỏi lúc này.",
            );
        } finally {
            setGenerating(false);
        }
    }

    return (
        <div className="mx-auto min-h-[calc(100vh-4rem)] w-full max-w-5xl px-4 py-8 text-[var(--color-text)] sm:px-6">
            <header className="mb-7">
                <p className="text-sm font-medium uppercase tracking-[0.2em] text-[var(--color-primary)]">
                    Học tập với AI
                </p>
                <h1 className="mt-2 text-3xl font-semibold">AI Practice</h1>
                <p className="mt-2 text-[var(--color-text-secondary)]">
                    Tạo câu hỏi trắc nghiệm từ phạm vi môn học trong đề cương của bạn.
                </p>
            </header>

            <div className="grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
                <section className={cardClassName} aria-labelledby="syllabus-heading">
                    <h2 id="syllabus-heading" className="text-lg font-semibold">Đề cương</h2>
                    {loadingSyllabi ? (
                        <p role="status" className="mt-3 text-sm text-[var(--color-text-secondary)]">Đang tải danh sách đề cương…</p>
                    ) : syllabusError ? (
                        <p role="alert" className="mt-3 text-sm text-[var(--color-danger)]">{syllabusError}</p>
                    ) : syllabi.length === 0 ? (
                        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">Bạn chưa có đề cương. Tải PDF lên bên dưới để bắt đầu.</p>
                    ) : (
                        <label className="mt-4 block text-sm font-medium">
                            Chọn đề cương
                            <select className={fieldClassName} value={selectedId} onChange={(event) => {
                                setSelectedId(event.target.value);
                                setResult(null);
                                setAnswers({});
                            }}>
                                {syllabi.map((document) => (
                                    <option key={document.document_id} value={document.document_id}>
                                        {document.title}{document.subject ? ` · ${document.subject}` : ""}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}

                    <form className="mt-6 space-y-3 border-t border-[var(--color-border)] pt-5" onSubmit={handleUpload}>
                        <h3 className="font-medium">Tải đề cương PDF</h3>
                        <label className="block text-sm font-medium" htmlFor="syllabus-title">
                            Tiêu đề
                            <input id="syllabus-title" className={fieldClassName} value={title} maxLength={300} required onChange={(event) => setTitle(event.target.value)} />
                        </label>
                        <label className="block text-sm font-medium" htmlFor="syllabus-subject">
                            Môn học (không bắt buộc)
                            <input id="syllabus-subject" className={fieldClassName} value={subject} maxLength={200} onChange={(event) => setSubject(event.target.value)} />
                        </label>
                        <label className="block text-sm font-medium" htmlFor="syllabus-pdf">
                            File PDF
                            <input id="syllabus-pdf" className={`${fieldClassName} file:mr-3 file:rounded-md file:border-0 file:bg-[var(--color-primary-light)] file:px-3 file:py-1`} type="file" accept="application/pdf,.pdf" required onChange={(event) => {
                                const nextFile = event.target.files?.[0] ?? null;
                                setFile(nextFile);
                                if (nextFile && !title.trim()) {
                                    setTitle(nextFile.name.replace(/\.pdf$/i, ""));
                                }
                            }} />
                        </label>
                        {uploadMessage && <p role="status" className="text-sm text-[var(--color-text-secondary)]">{uploadMessage}</p>}
                        <button type="submit" disabled={uploading} className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-wait disabled:opacity-60">
                            {uploading ? "Đang tải và xử lý…" : "Tải đề cương lên"}
                        </button>
                    </form>
                </section>

                <section className={cardClassName} aria-labelledby="generate-heading">
                    <h2 id="generate-heading" className="text-lg font-semibold">Tạo câu hỏi ôn tập</h2>
                    <form className="mt-4 space-y-4" onSubmit={handleGenerate}>
                        <label className="block text-sm font-medium" htmlFor="practice-topic">
                            Chủ đề
                            <input id="practice-topic" className={fieldClassName} value={topic} maxLength={300} required placeholder="Ví dụ: Minimax và alpha-beta pruning" onChange={(event) => setTopic(event.target.value)} />
                        </label>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <label className="block text-sm font-medium" htmlFor="practice-count">
                                Số câu (1–10)
                                <input id="practice-count" className={fieldClassName} type="number" min={1} max={10} value={questionCount} onChange={(event) => setQuestionCount(Number(event.target.value))} />
                            </label>
                            <label className="block text-sm font-medium" htmlFor="practice-difficulty">
                                Độ khó
                                <select id="practice-difficulty" className={fieldClassName} value={difficulty} onChange={(event) => setDifficulty(event.target.value as typeof difficulty)}>
                                    <option value="easy">Dễ</option>
                                    <option value="medium">Trung bình</option>
                                    <option value="hard">Khó</option>
                                </select>
                            </label>
                        </div>
                        {generationError && <p role="alert" className="text-sm text-[var(--color-danger)]">{generationError}</p>}
                        <button type="submit" disabled={generating || !selectedId || !topic.trim()} className="rounded-lg bg-[var(--color-primary)] px-4 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
                            {generating ? "AI đang tạo câu hỏi…" : "Tạo câu hỏi"}
                        </button>
                    </form>
                    {result?.status === "insufficient_context" && (
                        <p role="status" className="mt-5 rounded-lg border border-[var(--color-warning)] bg-[var(--color-warning-light)] p-4 text-sm">
                            {result.message || "Đề cương chưa có đủ nội dung phù hợp cho chủ đề này."}
                        </p>
                    )}
                </section>
            </div>

            {result?.status === "generated" && (
                <section className="mt-7 space-y-4" aria-label="Câu hỏi được tạo">
                    <div>
                        <h2 className="text-xl font-semibold">{result.questions.length} câu hỏi · {result.document_title}</h2>
                        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Chọn một đáp án để xem kết quả và giải thích.</p>
                    </div>
                    {result.questions.map((question, index) => (
                        <QuestionResultCard
                            key={`${result.document_id}-${index}`}
                            question={question}
                            index={index}
                            selectedAnswer={answers[index]}
                            onSelect={(optionIndex) => setAnswers((previous) => ({
                                ...previous,
                                [index]: optionIndex,
                            }))}
                        />
                    ))}
                </section>
            )}
        </div>
    );
}

function QuestionResultCard({
    question,
    index,
    selectedAnswer,
    onSelect,
}: {
    question: GeneratedSyllabusQuestion;
    index: number;
    selectedAnswer: number | undefined;
    onSelect: (optionIndex: number) => void;
}) {
    const revealed = selectedAnswer !== undefined;
    const isCorrect = selectedAnswer === question.correct_answer_index;
    const pages = [...new Set(question.source_references.flatMap((source) => source.source_pages))];

    return (
        <article className={cardClassName}>
            <h3 className="font-semibold">Câu {index + 1}. {question.question}</h3>
            <div className="mt-4 grid gap-2">
                {question.options.map((option, optionIndex) => {
                    const isAnswer = optionIndex === question.correct_answer_index;
                    const isSelected = optionIndex === selectedAnswer;
                    const stateClass = !revealed
                        ? "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
                        : isAnswer
                            ? "border-[var(--color-success)] bg-[var(--color-success-light)]"
                            : isSelected
                                ? "border-[var(--color-danger)] bg-[var(--color-danger-light)]"
                                : "border-[var(--color-border)] opacity-75";
                    return (
                        <button
                            key={option.label}
                            type="button"
                            disabled={revealed}
                            onClick={() => onSelect(optionIndex)}
                            className={`flex items-start gap-3 rounded-lg border p-3 text-left text-sm transition-colors disabled:cursor-default ${stateClass}`}
                        >
                            <span className="font-semibold">{option.label}.</span>
                            <span>{option.content}</span>
                        </button>
                    );
                })}
            </div>
            {revealed && (
                <div className="mt-4 space-y-3 border-t border-[var(--color-border)] pt-4 text-sm">
                    <p className={`font-semibold ${isCorrect ? "text-[var(--color-success)]" : "text-[var(--color-danger)]"}`}>
                        {isCorrect ? "Chính xác!" : `Chưa chính xác. Đáp án: ${question.correct_answer}.`}
                    </p>
                    <p><span className="font-medium">Giải thích: </span>{question.explanation}</p>
                    <p className="text-[var(--color-text-secondary)]">
                        <span className="font-medium">Mức grounding: </span>
                        {question.grounding_type === "source_supported"
                            ? "Đáp án được nội dung đề cương hỗ trợ trực tiếp."
                            : "Chủ đề thuộc đề cương; câu trả lời dùng thêm kiến thức học thuật."}
                    </p>
                    <p className="text-[var(--color-text-secondary)]">
                        <span className="font-medium">Trang nguồn: </span>
                        {pages.length ? pages.join(", ") : "Không có thông tin trang"}
                    </p>
                </div>
            )}
        </article>
    );
}
