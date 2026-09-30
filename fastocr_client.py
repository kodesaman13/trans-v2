"""Small synchronous client for the FastOCR asynchronous document API."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class FastOCRError(RuntimeError):
    """Raised when a FastOCR request fails or returns an invalid response."""


@dataclass(frozen=True)
class FastOCRResult:
    document_id: str
    text: str
    searchable_pdf: bytes


class FastOCRClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.fastocr.org/v1",
        request_timeout: float = 30.0,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
    ) -> None:
        if not api_key:
            raise FastOCRError("FASTOCR_API_KEY is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    @classmethod
    def from_environment(cls) -> "FastOCRClient":
        def number(name: str, default: float) -> float:
            raw = os.getenv(name, str(default))
            try:
                value = float(raw)
            except ValueError as exc:
                raise FastOCRError(f"{name} must be a number") from exc
            if value <= 0:
                raise FastOCRError(f"{name} must be greater than zero")
            return value

        return cls(
            api_key=os.getenv("FASTOCR_API_KEY") or os.getenv("FASTOCR_KEY", ""),
            base_url=os.getenv("FASTOCR_BASE_URL", "https://api.fastocr.org/v1"),
            request_timeout=number("FASTOCR_REQUEST_TIMEOUT", 30),
            poll_interval=number("FASTOCR_POLL_INTERVAL", 2),
            poll_timeout=number("FASTOCR_POLL_TIMEOUT", 300),
        )

    def process_file(self, filename: str, content: bytes, content_type: str) -> FastOCRResult:
        created = self._request(
            "POST",
            "/documents",
            payload={"filename": filename, "size_bytes": len(content)},
            idempotency_key=str(uuid.uuid4()),
        )
        document_id = created.get("id")
        upload_url = created.get("upload_url")
        if not isinstance(document_id, str) or not document_id:
            raise FastOCRError("FastOCR did not return a document ID")
        if not isinstance(upload_url, str) or not upload_url:
            raise FastOCRError("FastOCR did not return an upload URL")

        self._upload(upload_url, content, content_type)
        self._request("POST", f"/documents/{quote(document_id, safe='')}/start")
        self._wait_for_completion(document_id)

        text = self._request_bytes(
            "GET",
            f"/documents/{quote(document_id, safe='')}/output?format=text",
        ).decode("utf-8", errors="replace")
        searchable_pdf = self._request_bytes(
            "GET",
            f"/documents/{quote(document_id, safe='')}/output?format=pdf",
        )
        return FastOCRResult(document_id, text, searchable_pdf)

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": self.api_key,
            "Content-Type": content_type,
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self._headers()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            return json.loads(self._request_bytes(method, path, body, headers))
        except json.JSONDecodeError as exc:
            raise FastOCRError("FastOCR returned invalid JSON") from exc

    def _request_bytes(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers or self._headers(),
            method=method,
        )
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise FastOCRError(f"FastOCR request failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise FastOCRError("FastOCR request timed out or could not connect") from exc
        except (OSError, ValueError) as exc:
            raise FastOCRError("FastOCR returned an invalid response") from exc

    def _upload(self, upload_url: str, content: bytes, content_type: str) -> None:
        request = Request(
            upload_url,
            data=content,
            headers={"Content-Type": content_type},
            method="PUT",
        )
        try:
            with urlopen(request, timeout=self.request_timeout):
                return
        except HTTPError as exc:
            raise FastOCRError(f"FastOCR upload failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise FastOCRError("FastOCR upload timed out or could not connect") from exc

    def _wait_for_completion(self, document_id: str) -> None:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            status_response = self._request(
                "GET", f"/documents/{quote(document_id, safe='')}"
            )
            status = status_response.get("status")
            if status == "completed":
                return
            if status in {"failed", "error"}:
                raise FastOCRError("FastOCR document processing failed")
            time.sleep(self.poll_interval)
        raise FastOCRError("FastOCR document processing timed out")
