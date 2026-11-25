import test from "node:test";

import assert from "node:assert/strict";

import {
    readUploadTracking,
    saveUploadTracking,
    clearUploadTracking,
    followUploadJob,
    abortableDelay,
} from "../src/services/uploadTracking.ts";


function storage() {
    const data = new Map();

    return {
        getItem: key =>
            data.get(key) ?? null,

        setItem: (key, value) =>
            data.set(key, value),

        removeItem: key =>
            data.delete(key),
    };
}


const pending = {
    jobId: null,

    fileName: "vatlli.pdf",

    fileSize: 1024,

    model: "PaddleOCR-VL-1.6",

    startedAt: 1000,
};


function job(
    status,
    id = "j1",
) {
    return {
        job_id: id,

        status,

        phase:
            status === "completed"
                ? "completed"
                : "ocr",

        result:
            status === "completed"
                ? {
                    exam_id: "e1",
                    title: "Test",
                    question_count: 9,
                }
                : null,

        error:
            status === "failed"
                ? "OCR failed"
                : null,

        error_status: null,
    };
}


function setup(replies) {
    const controller =
        new AbortController();

    const ids = [];

    const updates = [];

    const retries = [];

    const delays = [];


    const run = () =>
        followUploadJob({
            jobId: "j1",

            signal: controller.signal,

            getJob: async id => {
                ids.push(id);

                if (!replies.length) {
                    throw new Error(
                        "unexpected GET",
                    );
                }

                const value =
                    replies.shift();

                if (value instanceof Error) {
                    throw value;
                }

                return value;
            },

            onUpdate: value =>
                updates.push(value),

            onRetry: (count, ms) =>
                retries.push([count, ms]),

            delay: async ms => {
                delays.push(ms);
            },
        });


    return {
        run,
        controller,
        ids,
        updates,
        retries,
        delays,
    };
}


test(
    "pending record survives reload before Job ID",
    () => {
        const s = storage();

        saveUploadTracking(
            s,
            "u1",
            pending,
        );

        assert.deepEqual(
            readUploadTracking(s, "u1"),
            pending,
        );
    },
);


test(
    "acknowledged Job ID is restored for its owner only",
    () => {
        const s = storage();

        const tracked = {
            ...pending,
            jobId: "j1",
        };

        saveUploadTracking(
            s,
            "u1",
            tracked,
        );

        assert.deepEqual(
            readUploadTracking(s, "u1"),
            tracked,
        );

        assert.equal(
            readUploadTracking(s, "u2"),
            null,
        );

        saveUploadTracking(
            s,
            "u2",
            {
                ...pending,
                jobId: "j2",
            },
        );

        clearUploadTracking(
            s,
            "u1",
        );

        assert.equal(
            readUploadTracking(s, "u1"),
            null,
        );

        assert.equal(
            readUploadTracking(s, "u2").jobId,
            "j2",
        );
    },
);


test(
    "invalid stored record is not silently accepted",
    () => {
        const s = storage();

        saveUploadTracking(
            s,
            "u1",
            {
                ...pending,
                startedAt: -1,
            },
        );

        assert.throws(
            () => readUploadTracking(s, "u1"),
            /không hợp lệ/,
        );
    },
);


test(
    "storage write failure is visible",
    () => {
        const s = {
            setItem: () => {
                throw new Error(
                    "quota",
                );
            },
        };

        assert.throws(
            () =>
                saveUploadTracking(
                    s,
                    "u1",
                    pending,
                ),
            /quota/,
        );
    },
);


test(
    "reconnect uses GET and same ID until completed",
    async () => {
        const s = setup([
            job("queued"),
            job("processing"),
            job("completed"),
        ]);

        assert.equal(
            (await s.run()).result.exam_id,
            "e1",
        );

        assert.deepEqual(
            s.ids,
            ["j1", "j1", "j1"],
        );

        assert.deepEqual(
            s.updates.map(
                x => x.status,
            ),
            [
                "queued",
                "processing",
                "completed",
            ],
        );

        assert.deepEqual(
            s.delays,
            [2000, 2000],
        );
    },
);


test(
    "temporary status errors recover and reset backoff",
    async () => {
        const e =
            new Error("offline");

        const s = setup([
            e,
            e,
            job("processing"),
            e,
            job("completed"),
        ]);

        await s.run();

        assert.deepEqual(
            s.retries,
            [
                [1, 2000],
                [2, 4000],
                [1, 2000],
            ],
        );

        assert.deepEqual(
            s.ids,
            Array(5).fill("j1"),
        );
    },
);


test(
    "five consecutive GET failures pause without declaring job failed",
    async () => {
        const s = setup(
            Array(5).fill(
                new Error("offline"),
            ),
        );

        await assert.rejects(
            s.run(),
            /offline/,
        );

        assert.deepEqual(
            s.delays,
            [
                2000,
                4000,
                8000,
                16000,
            ],
        );

        assert.equal(
            s.ids.length,
            5,
        );

        assert.equal(
            s.updates.length,
            0,
        );
    },
);


test(
    "server-confirmed failed job is terminal",
    async () => {
        const s = setup([
            job("failed"),
        ]);

        assert.equal(
            (await s.run()).status,
            "failed",
        );

        assert.equal(
            s.ids.length,
            1,
        );

        assert.equal(
            s.retries.length,
            0,
        );
    },
);


test(
    "mismatched ID and invalid completion cannot be accepted",
    async () => {
        const invalid = {
            ...job("completed"),

            result: null,
        };

        const s = setup([
            job(
                "completed",
                "wrong",
            ),

            invalid,

            job("completed"),
        ]);

        await s.run();

        assert.equal(
            s.updates.length,
            1,
        );

        assert.equal(
            s.retries.length,
            2,
        );
    },
);


test(
    "abort while GET is in flight ignores late response",
    async () => {
        const c =
            new AbortController();

        let resolve;

        const updates = [];

        const running = followUploadJob({
            jobId: "j1",

            signal: c.signal,

            getJob: () =>
                new Promise(r => {
                    resolve = r;
                }),

            onUpdate: v =>
                updates.push(v),

            onRetry: () =>
                assert.fail(),
        });

        c.abort();

        resolve(
            job("completed"),
        );

        await assert.rejects(
            running,
            {
                name: "AbortError",
            },
        );

        assert.equal(
            updates.length,
            0,
        );
    },
);


test(
    "abort cancels backoff delay",
    async () => {
        const c =
            new AbortController();

        const running =
            abortableDelay(
                60_000,
                c.signal,
            );

        c.abort();

        await assert.rejects(
            running,
            {
                name: "AbortError",
            },
        );
    },
);