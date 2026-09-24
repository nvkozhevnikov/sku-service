from datetime import timezone
from pathlib import Path
import unittest
from urllib.error import URLError

from universal_supplier.http_capture import CaptureStatus, HttpResponse, capture_public_html, sanitise_html


ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, *, timeout_seconds):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingEvidenceStore:
    def __init__(self):
        self.saved = []

    def save(self, *, evidence_dir, final_url, evidence_sha256, body):
        self.saved.append((evidence_dir, final_url, evidence_sha256, body))
        return f"capture://example.test/{evidence_sha256}.html"


class HttpCaptureTests(unittest.TestCase):
    URL = "https://example.test/card"

    def capture(self, client):
        self.evidence = RecordingEvidenceStore()
        return capture_public_html(self.URL, evidence_dir=ROOT, client=client, evidence_store=self.evidence)

    def test_success_writes_only_sanitised_html_and_persistable_metadata(self):
        raw = b'<html><input name="sessid" value="secret"><a href="/x?sessid=abc">x</a></html>'
        result = self.capture(FakeClient(HttpResponse(self.URL, 200, "text/html; charset=utf-8", raw)))
        self.assertEqual(result.status, CaptureStatus.SUCCESS)
        self.assertEqual(result.capture.http_status, 200)
        self.assertEqual(result.capture.observed_at.tzinfo, timezone.utc)
        saved = self.evidence.saved[0][3].decode("utf-8")
        self.assertNotIn("secret", saved)
        self.assertNotIn("sessid=abc", saved)
        self.assertIn("[REDACTED]", saved)

    def test_statuses_do_not_create_evidence(self):
        cases = [
            (HttpResponse(self.URL, 403, "text/html", b"forbidden"), CaptureStatus.BLOCKED),
            (HttpResponse(self.URL, 404, "text/html", b"missing"), CaptureStatus.HTTP_STATUS),
            (HttpResponse(self.URL, 429, "text/html", b"slow down"), CaptureStatus.BLOCKED),
            (HttpResponse(self.URL, 200, "application/pdf", b"%PDF"), CaptureStatus.NON_HTML),
            (HttpResponse(self.URL, 200, "text/html", b"<title>CAPTCHA</title>"), CaptureStatus.BLOCKED),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                result = self.capture(FakeClient(response))
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.capture)
                self.assertEqual(self.evidence.saved, [])

    def test_transient_failure_is_bounded(self):
        client = FakeClient(URLError("offline"), HttpResponse(self.URL, 500, "text/html", b"error"))
        result = self.capture(client)
        self.assertEqual(result.status, CaptureStatus.HTTP_STATUS)
        self.assertEqual(client.calls, 2)
        self.assertEqual(result.diagnostics, ("network_error:URLError", "http_500"))

    def test_rejects_unsafe_urls_and_redirects(self):
        unsafe = capture_public_html("https://example.test/card?token=secret", evidence_dir=Path("."), client=FakeClient())
        self.assertEqual(unsafe.status, CaptureStatus.UNSAFE_URL)
        result = self.capture(FakeClient(HttpResponse("https://example.test/card?utm=x", 200, "text/html", b"ok")))
        self.assertEqual(result.status, CaptureStatus.UNSAFE_URL)

    def test_sanitiser_preserves_non_secret_markup(self):
        value = sanitise_html(b'<h1>BMS-230DG</h1><input name="model" value="BMS-230DG">').decode()
        self.assertIn("BMS-230DG", value)
