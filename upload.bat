@echo off
setlocal

set "FILE=%~1"
set "DOCUMENT_ID=%~2"

if "%FILE%"=="" (
    echo Usage: upload.bat "C:\path\to\file.pdf" 1
    exit /b 1
)

if "%DOCUMENT_ID%"=="" (
    echo Usage: upload.bat "C:\path\to\file.pdf" 1
    exit /b 1
)

if not exist "%FILE%" (
    echo File not found: %FILE%
    exit /b 1
)

for %%F in ("%FILE%") do set "FILENAME=%%~nxF"

powershell -NoProfile -Command "$b64=[Convert]::ToBase64String([IO.File]::ReadAllBytes($env:FILE)); @{document_id=[int]$env:DOCUMENT_ID;filename=$env:FILENAME;file=$b64} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 -NoNewline upload_body.json"

if errorlevel 1 (
    echo Failed to create request body.
    exit /b 1
)

echo Uploading "%FILENAME%"...
curl.exe -X POST "http://127.0.0.1:8001/ocr/upload" -H "Content-Type: application/json" --data-binary "@upload_body.json"

set "RESULT=%ERRORLEVEL%"
del /q upload_body.json >nul 2>&1

exit /b %RESULT%
