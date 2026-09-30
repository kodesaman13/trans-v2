import json
import unittest
from itertools import repeat
from unittest.mock import patch

from api_json import _fastocr_json_by_page
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
