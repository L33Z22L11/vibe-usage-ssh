import errno
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from vibe_usage_ssh.diagnostics import configure_logging, log_failure, logger


class DiagnosticsTests(unittest.TestCase):
    def test_http_failure_omits_credentials_and_exception_text(self):
        secret = "private-test-api-key"
        try:
            raise HTTPError(
                f"https://example.com/usage?key={secret}",
                401,
                f"rejected {secret}",
                {"Authorization": f"Bearer {secret}"},
                None,
            )
        except HTTPError as error:
            with self.assertLogs(logger, level="WARNING") as captured:
                log_failure("usage request", error)
        output = "\n".join(captured.output)
        self.assertIn("HTTP 401", output)
        self.assertIn("test_diagnostics.py:", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("https://", output)
        self.assertNotIn("Authorization", output)
        self.assertNotIn(os.path.dirname(__file__), output)

    def test_network_and_calculation_failures_keep_actionable_details(self):
        errors = [
            URLError(ConnectionRefusedError(errno.ECONNREFUSED, "private destination")),
            ValueError("private usage data"),
        ]
        with self.assertLogs(logger, level="WARNING") as captured:
            for error in errors:
                log_failure("usage aggregation", error)
        output = "\n".join(captured.output)
        self.assertIn("ConnectionRefusedError", output)
        self.assertIn(f"errno={errno.ECONNREFUSED}", output)
        self.assertIn("ValueError", output)
        self.assertNotIn("private", output)

    def test_logging_uses_service_descriptor_not_terminal_stderr(self):
        with tempfile.TemporaryFile(mode="w+") as service_log:
            descriptor = os.dup(service_log.fileno())
            with patch.dict(os.environ, {"VIBE_SSH_LOG_FD": str(descriptor)}):
                handler = configure_logging()
            try:
                with patch("sys.stderr") as terminal:
                    log_failure("usage request", TimeoutError())
                    terminal.write.assert_not_called()
                handler.flush()
                service_log.seek(0)
                self.assertIn("usage request failed: TimeoutError", service_log.read())
            finally:
                logger.removeHandler(handler)
                handler.stream.close()
                handler.close()
