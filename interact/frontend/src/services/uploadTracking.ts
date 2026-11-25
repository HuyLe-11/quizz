import type { UploadJob } from "../types/types";

export interface UploadTracking {
    jobId: string | null;
    fileName: string;
    fileSize: number;
    model: string;
    startedAt: number;
}

type TrackingStorage = Pick<
    Storage,
    "getItem" | "setItem" | "removeItem"
>;

function trackingKey(ownerId: string): string {
    return `quizpdf.upload.v1:${ownerId}`;
}

export function readUploadTracking(
    storage: TrackingStorage,
    ownerId: string,
): UploadTracking | null {
    const raw = storage.getItem(
        trackingKey(ownerId),
    );

    if (raw === null) return null;

    const value = JSON.parse(raw) as UploadTracking | null;

    if (
        !value ||
        typeof value.fileName !== "string" ||
        !value.fileName ||
        !Number.isFinite(value.fileSize) ||
        value.fileSize <= 0 ||
        typeof value.model !== "string" ||
        !value.model ||
        !Number.isFinite(value.startedAt) ||
        value.startedAt <= 0 ||
        (
            value.jobId !== null &&
            (
                typeof value.jobId !== "string" ||
                !value.jobId.trim()
            )
        )
    ) {
        throw new Error(
            "Bản ghi theo dõi PDF không hợp lệ.",
        );
    }

    return value;
}

export function saveUploadTracking(
    storage: TrackingStorage,
    ownerId: string,
    record: UploadTracking,
): void {
    storage.setItem(
        trackingKey(ownerId),
        JSON.stringify(record),
    );
}

export function clearUploadTracking(
    storage: TrackingStorage,
    ownerId: string,
): void {
    storage.removeItem(
        trackingKey(ownerId),
    );
}

export function isTerminalUploadJob(
    job: UploadJob | null,
): boolean {
    return (
        job?.status === "completed" ||
        job?.status === "failed"
    );
}

export function validateUploadJob(
    job: UploadJob,
    expectedId?: string,
): void {
    if (
        !job ||
        typeof job.job_id !== "string" ||
        !job.job_id.trim() ||
        (
            expectedId !== undefined &&
            job.job_id !== expectedId
        ) ||
        ![
            "queued",
            "processing",
            "completed",
            "failed",
        ].includes(job.status) ||
        (
            job.status === "completed" &&
            (
                typeof job.result?.exam_id !== "string" ||
                !job.result.exam_id.trim()
            )
        )
    ) {
        throw new Error(
            "Máy chủ trả về thông tin tác vụ không hợp lệ.",
        );
    }
}

export function abortableDelay(
    ms: number,
    signal: AbortSignal,
): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal.aborted) {
            reject(signal.reason);
            return;
        }

        const onAbort = () => {
            clearTimeout(timer);
            reject(signal.reason);
        };

        const timer = setTimeout(() => {
            signal.removeEventListener(
                "abort",
                onAbort,
            );

            resolve();
        }, ms);

        signal.addEventListener(
            "abort",
            onAbort,
            { once: true },
        );
    });
}

interface FollowOptions {
    jobId: string;

    signal: AbortSignal;

    getJob: (
        jobId: string,
        signal: AbortSignal,
    ) => Promise<UploadJob>;

    onUpdate: (
        job: UploadJob,
    ) => void;

    onRetry: (
        count: number,
        delayMs: number,
    ) => void;

    delay?: (
        ms: number,
        signal: AbortSignal,
    ) => Promise<void>;
}

/**
 * GET only.
 * A failed status query is never a failed processing job.
 */
export async function followUploadJob({
                                          jobId,
                                          signal,
                                          getJob,
                                          onUpdate,
                                          onRetry,
                                          delay = abortableDelay,
                                      }: FollowOptions): Promise<UploadJob> {
    let failures = 0;

    while (true) {
        signal.throwIfAborted();

        let job: UploadJob;

        try {
            job = await getJob(
                jobId,
                signal,
            );

            signal.throwIfAborted();

            validateUploadJob(
                job,
                jobId,
            );
        } catch (error) {
            signal.throwIfAborted();

            failures += 1;

            if (failures >= 5) {
                throw error;
            }

            const delayMs =
                2000 * 2 ** (failures - 1);

            onRetry(
                failures,
                delayMs,
            );

            await delay(
                delayMs,
                signal,
            );

            continue;
        }

        failures = 0;

        onUpdate(job);

        if (isTerminalUploadJob(job)) {
            return job;
        }

        await delay(
            2000,
            signal,
        );
    }
}