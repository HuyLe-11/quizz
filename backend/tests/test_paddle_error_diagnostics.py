import json
import unittest

from unittest.mock import patch

import requests

from backend.engine import paddle_client as client

from backend.engine.paddle_error_diagnostics import (
    MAX_BODY_BYTES,
    log_http_error,
)


class PaddleErrorDiagnosticsTests(unittest.TestCase):

    def response(
        self,
        status=400,
        body=None,
        media="application/json",
    ):

        response = requests.Response()

        response.status_code = status

        response.headers["Content-Type"] = media

        response._content = (
            body
            if isinstance(body, bytes)
            else json.dumps(body).encode("utf-8")
        )

        return response


    def diagnostic(self, response, stage="submit"):

        with self.assertLogs(
            "quizpdf",
            level="WARNING",
        ) as logs:

            log_http_error(
                response,
                stage,
            )

        return "\n".join(logs.output)


    def test_quota_category(self):

        log = self.diagnostic(
            self.response(
                body={
                    "errorMsg": "Insufficient quota",
                },
            )
        )

        self.assertIn(
            "status=400 category=quota",
            log,
        )


    def test_nested_message(self):

        log = self.diagnostic(
            self.response(
                body={
                    "error": {
                        "message": "Unsupported model",
                    },
                },
            )
        )

        self.assertIn(
            "category=model",
            log,
        )


    def test_sensitive_text_never_logged(self):

        log = self.diagnostic(
            self.response(
                body={
                    "message": (
                        "Invalid token Bearer secret-token "
                        "https://private.example/file.pdf?sig=secret"
                    ),
                    "requestId": "private-id",
                    "data": {
                        "text": "private PDF contents",
                    },
                },
            ),
            stage="private stage",
        )

        for value in (
            "secret",
            "private",
            "Bearer",
            "file.pdf",
        ):

            self.assertNotIn(
                value,
                log,
            )

        self.assertIn(
            "stage=other",
            log,
        )

        self.assertIn(
            "category=authentication",
            log,
        )


    def test_http_status_categories(self):

        cases = (
            (401, "authentication_or_permission"),
            (403, "authentication_or_permission"),
            (413, "payload_too_large"),
            (415, "unsupported_media_type"),
            (429, "rate_limit"),
            (503, "server_error"),
        )

        for status, category in cases:

            with self.subTest(status=status):

                self.assertIn(
                    f"category={category}",
                    self.diagnostic(
                        self.response(
                            status,
                            {},
                        )
                    ),
                )


    def test_unknown_bad_request(self):

        log = self.diagnostic(
            self.response(
                body={
                    "message": "unexpected message",
                },
            )
        )

        self.assertIn(
            "category=bad_request_unknown",
            log,
        )


    def test_html_not_logged(self):

        log = self.diagnostic(
            self.response(
                body=b"<html>secret gateway</html>",
                media="text/html",
            )
        )

        self.assertIn(
            "content_type=html body_shape=non_json",
            log,
        )

        self.assertNotIn(
            "secret",
            log,
        )


    def test_malformed_json(self):

        log = self.diagnostic(
            self.response(
                body=b"{bad",
            )
        )

        self.assertIn(
            "body_shape=invalid_json",
            log,
        )


    def test_oversized_response(self):

        log = self.diagnostic(
            self.response(
                body=b"x" * (MAX_BODY_BYTES + 1),
            )
        )

        self.assertIn(
            "body_shape=oversized",
            log,
        )


    def test_success_does_not_log(self):

        with patch(
            "backend.engine.paddle_error_diagnostics.logger.warning",
        ) as warn:

            log_http_error(
                self.response(200, {}),
                "submit",
            )

        warn.assert_not_called()


    def test_logging_failure_does_not_raise(self):

        with patch(
            "backend.engine.paddle_error_diagnostics.logger.warning",
            side_effect=RuntimeError("broken logger"),
        ):

            log_http_error(
                self.response(body={}),
                "submit",
            )


    def test_client_preserves_original_http_exception(self):

        with self.assertLogs(
            "quizpdf",
            level="WARNING",
        ) as logs:

            with self.assertRaises(
                client.PaddleOCRAPIError,
            ) as caught:

                client._check_response(
                    self.response(
                        body={
                            "message": "Invalid token",
                        },
                    ),
                    "submit",
                )

        self.assertEqual(
            caught.exception.status_code,
            400,
        )

        self.assertEqual(
            caught.exception.stage,
            "submit",
        )

        self.assertIn(
            "category=authentication",
            "\n".join(logs.output),
        )


    def test_client_success_is_unchanged(self):

        with patch(
            "backend.engine.paddle_error_diagnostics.logger.warning",
        ) as warn:

            client._check_response(
                self.response(200, {}),
                "submit",
            )

        warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()