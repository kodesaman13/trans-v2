#!/usr/bin/env python3
"""Run every local Input/*.pdf through the configured FastOCR upload flow."""

import base64
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


BASE_URL = os.getenv("OCR_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
INPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Input")


def post_upload(path):
    with open(path, "rb") as source:
        payload = {
            "document_type": "arabic_invoice",
            "filename": os.path.basename(path),
            "file": base64.b64encode(source.read()).decode("ascii"),
        }
    request = urllib.request.Request(
        f"{BASE_URL}/ocr/upload",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read().decode("utf-8"))


def server_is_ready():
    try:
        with urllib.request.urlopen(f"{BASE_URL}/ocr/health", timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start_server_if_needed():
    if server_is_ready():
        return None
    if not os.getenv("FASTOCR_API_KEY") and not os.getenv("FASTOCR_KEY"):
        raise RuntimeError(
            "FASTOCR_API_KEY or FASTOCR_KEY must be set before running the batch test"
        )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api_json:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if server_is_ready():
            return process
        if process.poll() is not None:
            break
        time.sleep(0.5)
    process.terminate()
    raise RuntimeError("local OCR server did not become ready")


def main():
    paths = sorted(glob.glob(os.path.join(INPUT_DIR, "*.pdf")))
    if not paths:
        print(f"No PDF files found in {INPUT_DIR}")
        return 1

    server_process = None
    try:
        server_process = start_server_if_needed()
        passed = failed = 0
        print(f"Testing {len(paths)} local PDFs with document_type=arabic_invoice")
        for path in paths:
            name = os.path.basename(path)
            try:
                result = post_upload(path)
                summary = result.get("extraction_summary") or {}
                stats = summary.get("statistics") or {}
                if result.get("status") == "success":
                    passed += 1
                    print(
                        f"PASS | {name} | pages={summary.get('pages_processed')} | "
                        f"fields={len(result.get('validation_data') or [])} | "
                        f"pass={stats.get('passed', 0)} "
                        f"warn={stats.get('warnings', 0)} fail={stats.get('failed', 0)}"
                    )
                else:
                    failed += 1
                    print(f"FAIL | {name} | {result.get('message', 'unknown error')}")
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                failed += 1
                print(f"FAIL | {name} | {type(exc).__name__}: {exc}")

        print(f"Summary: {passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        if server_process and server_process.poll() is None:
            server_process.terminate()
            server_process.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
