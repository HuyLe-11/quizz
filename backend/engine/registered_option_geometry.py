"""Review-only option association in registered OCR pixel coordinates."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median

Box = tuple[float, float, float, float]

HEADING = re.compile(
    r"^\s*(?:Question|Câu|Bài)\s*(\d+)(?=\b|[A-H][.):])",
    re.I,
)

PREFIX = re.compile(
    r"^(?:\(([A-H])\)|([A-H])(?:[.):]|\s+))"
)

MERGED = re.compile(
    r"^\s*(Question|Câu|Bài)\s*(\d+)\s*[.:)]?\s*([A-H])[.):]\s*\S",
    re.I,
)

EMBEDDED_OPTION = re.compile(
    r"(?<!\S)(?:\([A-H]\)|[A-H][.):])"
)


@dataclass(frozen=True)
class OptionRegion:
    label: str | None
    box: Box


@dataclass(frozen=True)
class OptionSuggestion:
    question_number: int
    label: str
    evidence: str


def _contains(box, x, y):
    a, b, c, d = box
    return a <= x <= c and b <= y <= d


def _valid_box(value, width, height):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Invalid recognition box")

    if any(
        type(v) not in (int, float) or not math.isfinite(v)
        for v in value
    ):
        raise ValueError("Invalid recognition coordinates")

    a, b, c, d = value

    if not (0 <= a < c <= width and 0 <= b < d <= height):
        raise ValueError("Recognition box outside OCR image")

    return tuple(map(float, value))


class RegisteredOptionIndex:
    def __init__(self, root, labels_by_question, *, width, height):
        if (
            type(width) is not int
            or type(height) is not int
            or min(width, height) <= 0
        ):
            raise ValueError("Invalid OCR dimensions")

        self.width, self.height = width, height
        self.labels = {}

        for number, labels in labels_by_question.items():
            labels = tuple(labels)

            if (
                type(number) is not int
                or number < 1
                or not 2 <= len(labels) <= 8
                or len(set(labels)) != len(labels)
                or any(l not in tuple("ABCDEFGH") for l in labels)
            ):
                raise ValueError("Invalid question labels")

            self.labels[number] = labels

        texts, boxes, scores = (
            root.get(k)
            for k in ("rec_texts", "rec_boxes", "rec_scores")
        )

        if (
            not all(isinstance(a, list) for a in (texts, boxes, scores))
            or not texts
            or len(texts) != len(boxes)
            or len(texts) != len(scores)
            or len(texts) > 5000
        ):
            raise ValueError("Invalid recognition arrays")

        entries = []

        for text, box, score in zip(texts, boxes, scores, strict=True):
            box = _valid_box(box, width, height)

            if (
                not isinstance(text, str)
                or type(score) not in (int, float)
                or not math.isfinite(score)
                or not 0 <= score <= 1
            ):
                raise ValueError("Invalid recognition entry")

            entries.append((text.strip(), box, score))

        self.unit = (
            median(
                b[3] - b[1]
                for _, b, s in entries
                if s >= .7
            )
            if any(s >= .7 for _, _, s in entries)
            else 0
        )

        if self.unit <= 0:
            raise ValueError("No reliable text geometry")

        self.groups = defaultdict(list)

        headings = []
        merged = {}

        number = None
        seen = set()

        for text, box, score in entries:
            heading = HEADING.match(text)

            if heading:
                number = int(heading.group(1))

                if (
                    score < .7
                    or number in seen
                    or number not in self.labels
                ):
                    raise ValueError(
                        "Unknown, duplicate or unreliable heading"
                    )

                if headings and (
                    number <= headings[-1][0]
                    or box[1] <= headings[-1][1][1]
                ):
                    raise ValueError("Unsupported reading order")

                headings.append((number, box))
                seen.add(number)

                # A merged heading is never an exact option box.
                # Save a coarse label estimate only for explicit
                # first-option markers.

                match = MERGED.match(text)

                if (
                    match
                    and match.group(3) == self.labels[number][0]
                    and not EMBEDDED_OPTION.search(
                        text[match.end():]
                    )
                ):
                    a, b, c, d = box

                    anchor = (
                        a + (c - a) * match.start(3) / len(text)
                    )

                    merged[number] = (
                        box,
                        anchor,
                        match.group(1).casefold(),
                        len(match.group(2)),
                    )

                continue

            if number is None:
                continue

            if score < .7:
                continue

            match = PREFIX.match(text)

            label = (
                match.group(1) or match.group(2)
                if match
                else None
            )

            if label not in self.labels[number]:
                label = None

            self.groups[number].append(
                OptionRegion(label, box)
            )

        if seen != set(self.labels):
            raise ValueError(
                "Missing expected question headings"
            )

        if headings and (
            max(b[0] for _, b in headings)
            - min(b[0] for _, b in headings)
            > 2 * self.unit
        ):
            raise ValueError(
                "Multiple heading lanes require explicit ownership"
            )

        self.headings = tuple(
            box for _, box in headings
        )

        self.complete = {}
        self.ambiguous = set()

        for number, regions in self.groups.items():
            labels = self.labels[number]

            observed = [
                r for r in regions
                if r.label
            ]

            counts = Counter(
                r.label for r in observed
            )

            if (
                len(counts) < 2
                or any(v > 1 for v in counts.values())
            ):
                self.ambiguous.add(number)
                continue

            if set(counts) != set(labels):
                continue

            by_label = {
                r.label: r.box
                for r in observed
            }

            xs = tuple(
                by_label[l][0]
                for l in labels
            )

            ys = [
                (by_label[l][1] + by_label[l][3]) / 2
                for l in labels
            ]

            if (
                all(
                    b - a > 3 * self.unit
                    for a, b in zip(xs, xs[1:])
                )
                and max(ys) - min(ys) <= self.unit
            ):
                self.complete[number] = xs

        # Build a separate index for merged heading/option rows.

        self.merged_rows = {}

        for number, (
            box,
            anchor,
            kind,
            digits,
        ) in merged.items():

            labels = self.labels[number]
            regions = self.groups[number]

            peers = [
                r for r in regions
                if r.label
            ]

            if (
                number in self.ambiguous
                or len(labels) < 3
                or len(peers) != len(labels) - 1
                or {r.label for r in peers} != set(labels[1:])
            ):
                continue

            ordered = [
                next(
                    r.box for r in peers
                    if r.label == label
                )
                for label in labels[1:]
            ]

            u = self.unit

            centers = [
                (b[1] + b[3]) / 2
                for b in [box, *ordered]
            ]

            if (
                max(centers) - min(centers) > u
                or ordered[0][0] - box[2] < .25 * u
                or any(
                    b[0] - a[0] <= 3 * u
                    for a, b in zip(ordered, ordered[1:])
                )
            ):
                continue

            self.merged_rows[number] = (
                box,
                anchor,
                kind,
                digits,
                tuple(ordered),
            )

    def _associate_merged_heading(self, x, y):
        """Review-only estimate corroborated by two nearby merged rows."""

        if sum(
            _contains(b, x, y)
            for b in self.headings
        ) != 1:
            return None

        if any(
            _contains(r.box, x, y)
            for regions in self.groups.values()
            for r in regions
        ):
            return None

        u = self.unit

        candidates = []

        for number, (
            box,
            anchor,
            kind,
            digits,
            peers,
        ) in self.merged_rows.items():

            if (
                not _contains(box, x, y)
                or not all(
                    b[1] <= y <= b[3]
                    for b in peers
                )
            ):
                continue

            refs = [
                other_anchor
                for n, (
                    other_box,
                    other_anchor,
                    other_kind,
                    other_digits,
                    other_peers,
                ) in self.merged_rows.items()
                if (
                    0 < abs(n - number) <= 3
                    and self.labels[n] == self.labels[number]
                    and (kind, digits) == (
                        other_kind,
                        other_digits,
                    )
                    and abs(
                        box[0] - other_box[0]
                    ) <= .5 * u
                    and abs(
                        anchor - other_anchor
                    ) <= .5 * u
                    and all(
                        abs(a[0] - b[0]) <= u
                        for a, b in zip(peers, other_peers)
                    )
                )
            ]

            if len(refs) < 2:
                continue

            anchors = [
                anchor,
                *refs,
            ]

            if max(anchors) - min(anchors) > .5 * u:
                continue

            # Restrict to the corroborated leading label, not
            # the whole heading or its answer text.
            # This remains a heuristic.

            if (
                max(anchors) - .2 * u
                <= x
                <= min(anchors) + .65 * u
            ):
                candidates.append(
                    OptionSuggestion(
                        number,
                        self.labels[number][0],
                        "repeated_columns",
                    )
                )

        return (
            candidates[0]
            if len(candidates) == 1
            else None
        )

    def associate(self, x, y):
        if not (
            math.isfinite(x)
            and math.isfinite(y)
            and 0 <= x < self.width
            and 0 <= y < self.height
        ):
            return None

        if any(
            _contains(box, x, y)
            for box in self.headings
        ):
            return self._associate_merged_heading(x, y)

        direct = []

        for number, regions in self.groups.items():
            for region in regions:

                a, b, c, d = region.box

                if (
                    region.label
                    and _contains(region.box, x, y)
                    and x - a <= 1.5 * (d - b)
                ):
                    direct.append(
                        OptionSuggestion(
                            number,
                            region.label,
                            "leading_region",
                        )
                    )

        if direct:
            if (
                len(direct) != 1
                or direct[0].question_number in self.ambiguous
            ):
                return None

            return direct[0]

        candidates = []

        u = self.unit

        for number, regions in self.groups.items():
            if number in self.ambiguous:
                continue

            labels = self.labels[number]

            if len(labels) < 3:
                continue

            peers = [
                r for r in regions
                if r.label and r.box[1] <= y <= r.box[3]
            ]

            if len(peers) < 2:
                continue

            refs = [
                row
                for n, row in self.complete.items()
                if (
                    0 < abs(n - number) <= 6
                    and self.labels[n] == labels
                )
            ]

            if len(refs) < 2:
                continue

            columns = tuple(
                map(median, zip(*refs))
            )

            refs = [
                row
                for row in refs
                if max(
                    abs(a - b)
                    for a, b in zip(row, columns)
                ) <= u
            ]

            if len(refs) < 2:
                continue

            columns = tuple(
                map(median, zip(*refs))
            )

            possible = [
                label
                for label, col in zip(labels, columns)
                if -.25 * u <= x - col <= 1.5 * u
            ]

            if len(possible) != 1:
                continue

            target = possible[0]

            if any(
                abs(
                    r.box[0]
                    - columns[labels.index(r.label)]
                ) > u
                for r in peers
                if r.label != target
            ):
                continue

            known = [
                r for r in regions
                if r.label == target
            ]

            if known and not any(
                _contains(r.box, x, y)
                for r in peers
                if r.label == target
            ):
                continue

            if any(
                r.label
                and r.label != target
                and _contains(r.box, x, y)
                for r in regions
            ):
                continue

            if len(peers) < len(labels) - 1:
                covered = set()

                for r in regions:
                    a, b, c, d = r.box

                    if b <= y <= d:
                        near = [
                            label
                            for label, col in zip(labels, columns)
                            if abs(a - col) <= u
                        ]

                        if len(near) == 1:
                            covered.add(near[0])

                if covered != set(labels):
                    continue

            candidates.append(
                OptionSuggestion(
                    number,
                    target,
                    "repeated_columns",
                )
            )

        return (
            candidates[0]
            if len(candidates) == 1
            else None
        )