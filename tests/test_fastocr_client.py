import json
import unittest
from itertools import repeat
from unittest.mock import patch

from api_json import _expand_all_page_rules, _fastocr_json_by_page, _region_from_spec
from api_json import DEFAULT_ARABIC_INVOICE_RULES
from fastocr_client import FastOCRClient


class _Response:
    def __init__(self, body=b""):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FastOCRClientTests(unittest.TestCase):
    def test_fastocr_json_preserves_page_numbers(self):
        result = type("Result", (), {"text": "رقم الفاتورة: 123"})()
        data = _fastocr_json_by_page([(3, result)])[0]
        self.assertEqual(data["pages_analyzed"], [3])
        self.assertEqual(data["page_lines"][0]["page_number"], 3)

    def test_region_rules_require_normalized_positive_rectangle(self):
        self.assertEqual(_region_from_spec({"region": [0.1, 0.2, 0.3, 0.4]}), (0.1, 0.2, 0.3, 0.4))
        self.assertIsNone(_region_from_spec({"region": [0.8, 0.2, 0.3, 0.4]}))

    def test_default_arabic_template_has_page_specific_fields(self):
        self.assertIn("*", DEFAULT_ARABIC_INVOICE_RULES["pages"])

    def test_wildcard_rules_expand_to_all_pdf_pages(self):
        expanded = _expand_all_page_rules(
            {"pages": {"*": ["total"]}}, b"not-a-pdf", "invoice.png"
        )
        self.assertEqual(expanded["pages"], {"1": ["total"]})

    @patch("fastocr_client.time.sleep")
    @patch("fastocr_client.urlopen")
    def test_process_file_uses_presigned_upload_and_downloads_outputs(
        self, urlopen, _sleep
    ):
        urlopen.side_effect = [
            _Response(json.dumps({"id": "doc-1", "upload_url": "https://upload.test"}).encode()),
            _Response(),
            _Response(b'{"status":"processing"}'),
            _Response(b'{"status":"completed"}'),
            _Response(b"invoice text"),
            _Response(b"%PDF"),
        ]
        client = FastOCRClient("secret", poll_interval=0.01)

        result = client.process_file("invoice.pdf", b"pdf-bytes", "application/pdf")

        self.assertEqual(result.document_id, "doc-1")
        self.assertEqual(result.text, "invoice text")
        self.assertEqual(result.searchable_pdf, b"%PDF")
        upload_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(upload_request.get_method(), "PUT")
        self.assertEqual(upload_request.get_header("Content-type"), "application/pdf")
        self.assertNotIn("secret", repr(upload_request))

    @patch("fastocr_client.urlopen")
    def test_processing_timeout_is_bounded(self, urlopen):
        urlopen.side_effect = [
            _Response(json.dumps({"id": "doc-1", "upload_url": "https://upload.test"}).encode()),
            _Response(),
            *repeat(_Response(b'{"status":"processing"}'), 2),
        ]
        client = FastOCRClient("secret", poll_interval=0.01, poll_timeout=0.001)

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            client.process_file("invoice.pdf", b"pdf-bytes", "application/pdf")


if __name__ == "__main__":
    unittest.main()
