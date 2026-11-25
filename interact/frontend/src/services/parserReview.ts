import { resolveApiUrl } from "./api";

import { requireSupabase } from "./supabase";

import type {
    ApiResponse,
} from "../types/types";


export interface ParserReviewItem {
    question_record_id: string;

    issue_codes: string[];

    warning: string | null;

    requires_source_image: boolean;
}


export type ParserReview =
    | {
    status: "available";

    schema_version: 1;

    stage: "import";

    review_summary: {
        total_questions: number;

        needs_review: number;

        structural_issues: number;
    };

    review_items: ParserReviewItem[];
}

    | {
    status: "unavailable";

    reason:
        | "missing"
        | "invalid"
        | "unsupported"
        | "stale";
};


export async function getParserReview(
    examId: string,

    signal: AbortSignal,
): Promise<ParserReview> {

    const {
        data: { session },

        error,
    } = await requireSupabase()
        .auth.getSession();

    if (
        error
        || !session?.access_token
    ) {
        throw new Error(
            "Không thể xác thực phiên đăng nhập.",
        );
    }

    const url = resolveApiUrl(
        `/api/exams/${encodeURIComponent(examId)}/parser-review`,
    );

    if (!url) {
        throw new Error(
            "Địa chỉ API không hợp lệ.",
        );
    }

    const response = await fetch(
        url,

        {
            headers: {
                Authorization:
                    `Bearer ${session.access_token}`,
            },

            signal: AbortSignal.any([
                signal,

                AbortSignal.timeout(
                    120_000,
                ),
            ]),

            cache: "no-store",
        },
    );

    if (!response.ok) {
        throw new Error(
            `Không thể tải báo cáo parser (${response.status}).`,
        );
    }

    const payload =
        await response.json() as ApiResponse<ParserReview>;

    if (
        !payload.success
        || !payload.data
    ) {
        throw new Error(
            "Báo cáo parser không hợp lệ.",
        );
    }

    const report = payload.data;

    if (
        report.status === "available"
        && report.schema_version === 1
        && report.stage === "import"
        && Array.isArray(
            report.review_items,
        )
        && report.review_summary
    ) {
        return report;
    }

    if (
        report.status === "unavailable"
    ) {
        return report;
    }

    throw new Error(
        "Phiên bản báo cáo parser không được hỗ trợ.",
    );
}