import { useQuiz } from "../../contexts/QuizContext";
import { QuestionContent } from "./QuestionContent";
import { ExplanationBox } from "./ExplanationBox";
import { McpQuestion } from "./McpQuestion";
import { TrueFalseQuestion } from "./TrueFalseQuestion";
import { MatchingQuestion } from "./MatchingQuestion";
import { FillInQuestion } from "./FillInQuestion";
import { QuestionTools } from "./QuestionTools";
import { splitQuestionSections } from "../../utils/questionSections";
import type { Question } from "../../types/types";


interface QuestionCardProps {
  question: Question;
  questionIndex: number;
}


export function QuestionCard({
                               question,
                               questionIndex,
                             }: QuestionCardProps) {

  const { state } = useQuiz();

  const {
    instruction,
    body,
  } = splitQuestionSections(
      question.question,
  );


  const getBadgeStyle = (
      type: string,
  ) => {
    switch (type) {
      case "mcp":
        return "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400 border-indigo-200 dark:border-indigo-800";

      case "true_false_ng":
        return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";

      case "matching":
        return "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400 border-cyan-200 dark:border-cyan-800";

      case "fill_in":
        return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";

      default:
        return "bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] border-[var(--color-border)]";
    }
  };


  const formatTypeLabel = (
      type: string,
  ) => {
    switch (type) {
      case "mcp":
        return "Trắc nghiệm";

      case "true_false_ng":
        return "Đúng / Sai / Không có thông tin";

      case "matching":
        return "Nối đáp án";

      case "fill_in":
        return "Điền vào chỗ trống";

      default:
        return type;
    }
  };


  const renderQuestionInput = () => {
    switch (question.type) {
      case "mcp":
        return (
            <McpQuestion
                question={question}
            />
        );

      case "true_false_ng":
        return (
            <TrueFalseQuestion
                question={question}
            />
        );

      case "matching":
        return (
            <MatchingQuestion
                question={question}
            />
        );

      case "fill_in":
        return (
            <FillInQuestion
                question={question}
            />
        );

      default:
        return (
            <div className="text-red-500">
              Dạng câu hỏi chưa được hỗ trợ
            </div>
        );
    }
  };


  return (
      <div className="w-full bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-6 md:p-8 shadow-sm animate-fade-in-up">

        {/* Section instructions */}
        {instruction && (
            <section
                aria-label="Hướng dẫn làm bài"
                className="mb-6 border-b border-[var(--color-border)] pb-6"
            >
              <h3 className="mb-2 text-sm font-semibold text-[var(--color-text-muted)]">
                Hướng dẫn làm bài
              </h3>

              <QuestionContent
                  content={instruction}
                  className="text-sm md:text-base font-normal text-[var(--color-text-secondary)]"
              />
            </section>
        )}


        {/* Question header */}
        <div className="flex flex-wrap items-center gap-3 mb-6">

                <span className="text-lg font-bold text-[var(--color-text)]">
                    Câu {questionIndex + 1}
                </span>

          <span
              className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wider border ${getBadgeStyle(question.type)}`}
          >
                    {formatTypeLabel(question.type)}
                </span>

          {question.type === "mcp"
              && question.multiple_choice
              && (
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border border-[var(--color-border)]">
                            Chọn nhiều đáp án
                        </span>
              )
          }

        </div>


        {/* Question body */}
        <div className="mb-8">
          <QuestionContent
              content={body}
              className="text-lg md:text-xl font-medium"
          />
        </div>


        {/* Question answer input */}
        <div className="mb-4">
          {renderQuestionInput()}
        </div>


        {/* Source PDF and question tools */}
        {question.question_record_id && (
            <QuestionTools
                question={question}
            />
        )}


        {/* Explanation after submission */}
        {state.is_submitted
            && question.explanation
            && (
                <ExplanationBox
                    explanation={question.explanation}
                />
            )
        }

      </div>
  );
}