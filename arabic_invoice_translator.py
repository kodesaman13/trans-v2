# !/usr/bin/env python3

import os
import sys
import re

import pymupdf
import pytesseract
import pandas as pd

from PIL import Image
from docx import Document
from argostranslate import translate, package

# ============================================================
# CONFIGURATION
# ============================================================

OCR_LANGUAGES = "ara+eng"

# True = reverse Arabic extracted from PDF
REVERSE_PDF_ARABIC = True


# ============================================================
# ARABIC DETECTION
# ============================================================

def contains_arabic(text):
    return bool(
        re.search(
            r"[\u0600-\u06FF]",
            text
        )
    )


# ============================================================
# REVERSE ARABIC WORD
# ============================================================

def reverse_arabic_word(word):
    """
    Reverse only Arabic characters.
    English text and normal numbers are preserved.
    """

    parts = re.findall(
        r"[\u0600-\u06FF]+|[^\u0600-\u06FF]+",
        word
    )

    result = []

    for part in parts:
        if re.search(r"[\u0600-\u06FF]", part):
            result.append(part[::-1])
        else:
            result.append(part)

    return "".join(result)


# ============================================================
# REVERSE ARABIC LINE
# ============================================================

def reverse_arabic_line(line):
    """
    Reverse Arabic characters AND Arabic word order.
    """

    if not contains_arabic(line):
        return line

    tokens = re.findall(
        r"\S+|\s+",
        line
    )

    words = []

    for token in tokens:
        if token.isspace():
            words.append(token)
        else:
            word = reverse_arabic_word(token)

            # Reverse Arabic-Indic numbers (e.g. ٦٢٠٢ -> ٢٠٢٦)
            word = re.sub(
                r"[٠-٩]+",
                lambda match: match.group(0)[::-1],
                word
            )

            words.append(word)

    non_space = [
        word for word in words if not word.isspace()
    ]

    arabic_indexes = [
        index for index, word in enumerate(non_space) if contains_arabic(word)
    ]

    if len(arabic_indexes) >= 2:
        arabic_words = [non_space[index] for index in arabic_indexes]
        arabic_words.reverse()

        for index, word in zip(arabic_indexes, arabic_words):
            non_space[index] = word

    result = []
    index = 0

    for token in words:
        if token.isspace():
            result.append(token)
        else:
            result.append(non_space[index])
            index += 1

    return "".join(result)


# ============================================================
# REVERSE ENTIRE PDF TEXT
# ============================================================

def reverse_pdf_arabic(text):
    if not REVERSE_PDF_ARABIC:
        return text

    lines = text.splitlines()
    output = []

    for line in lines:
        if contains_arabic(line):
            line = reverse_arabic_line(line)
        output.append(line)

    return "\n".join(output)


# ============================================================
# OCR IMAGE (UPDATED WITH CONFIDENCE SCORING)
# ============================================================

def ocr_image(image):
    print("Running OCR with confidence scoring...")

    ocr_data = pytesseract.image_to_data(
        image,
        lang=OCR_LANGUAGES,
        config="--psm 3",
        output_type=pytesseract.Output.DICT
    )

    # Filter out layout block markers (-1 confidence)
    confidences = [
        int(conf) for conf in ocr_data["conf"] if int(conf) != -1
    ]

    avg_confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0

    extracted_text = " ".join([
        ocr_data["text"][i] for i in range(len(ocr_data["text"]))
        if ocr_data["text"][i].strip()
    ])

    return extracted_text, round(avg_confidence, 2)


# ============================================================
# PDF TO IMAGE
# ============================================================

def pdf_page_to_image(page):
    matrix = pymupdf.Matrix(2, 2)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)

    return Image.frombytes(
        "RGB",
        (pixmap.width, pixmap.height),
        pixmap.samples
    )


# ============================================================
# PDF EXTRACTION (UPDATED WITH CONFIDENCE SCORING)
# ============================================================

def extract_pdf(file_path):
    print(f"Reading PDF: {file_path}")

    pdf = pymupdf.open(file_path)
    pages = []
    page_confidences = []

    try:
        for page_number, page in enumerate(pdf, start=1):
            print(f"Processing page {page_number}/{len(pdf)}")

            text = page.get_text("text")

            if not text.strip():
                print("No embedded text. Using OCR.")
                image = pdf_page_to_image(page)
                text, confidence = ocr_image(image)
                page_confidences.append(confidence)
            else:
                # Digital PDF text extraction is deterministic (100% accurate extraction)
                page_confidences.append(1.0)

            if contains_arabic(text) and REVERSE_PDF_ARABIC:
                print("Reversing Arabic PDF text...")
                text = reverse_pdf_arabic(text)

            pages.append(text)

    finally:
        pdf.close()

    overall_confidence = round(sum(page_confidences) / len(page_confidences), 2) if page_confidences else 0.0
    return "\n".join(pages), overall_confidence


# ============================================================
# IMAGE EXTRACTION
# ============================================================

def extract_image(file_path):
    print(f"Reading image: {file_path}")
    image = Image.open(file_path)
    return ocr_image(image)


# ============================================================
# TXT EXTRACTION
# ============================================================

def extract_txt(file_path):
    print(f"Reading TXT: {file_path}")
    encodings = ["utf-8", "utf-8-sig", "utf-16", "cp1256"]

    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as file:
                return file.read(), 1.0  # Native text has 1.0 confidence
        except UnicodeDecodeError:
            continue

    raise RuntimeError("Unable to read TXT file.")


# ============================================================
# DOCX EXTRACTION
# ============================================================

def extract_docx(file_path):
    print(f"Reading DOCX: {file_path}")
    document = Document(file_path)
    output = []

    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            output.append(paragraph.text)

    for table in document.tables:
        output.append("\n--- TABLE ---")
        for row in table.rows:
            values = [cell.text.strip() for cell in row.cells]
            output.append(" | ".join(values))

    return "\n".join(output), 1.0


# ============================================================
# EXCEL EXTRACTION
# ============================================================

def extract_excel(file_path):
    print(f"Reading Excel: {file_path}")
    extension = os.path.splitext(file_path)[1].lower()
    engine = "openpyxl" if extension == ".xlsx" else "xlrd"

    excel = pd.ExcelFile(file_path, engine=engine)
    output = []

    for sheet in excel.sheet_names:
        print(f"Reading sheet: {sheet}")
        dataframe = pd.read_excel(file_path, sheet_name=sheet, header=None, engine=engine)
        output.append(f"\n--- SHEET: {sheet} ---")

        for row in dataframe.itertuples(index=False, name=None):
            values = [str(value) for value in row if pd.notna(value)]
            if values:
                output.append(" | ".join(values))

    return "\n".join(output), 1.0


# ============================================================
# CSV EXTRACTION
# ============================================================

def extract_csv(file_path):
    print(f"Reading CSV: {file_path}")
    dataframe = pd.read_csv(file_path, header=None)
    output = []

    for row in dataframe.itertuples(index=False, name=None):
        values = [str(value) for value in row if pd.notna(value)]
        if values:
            output.append(" | ".join(values))

    return "\n".join(output), 1.0


# ============================================================
# FILE ROUTER
# ============================================================

def extract_text(file_path):
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":
        return extract_pdf(file_path)
    elif extension in [".png", ".jpg", ".jpeg"]:
        return extract_image(file_path)
    elif extension == ".txt":
        return extract_txt(file_path)
    elif extension == ".docx":
        return extract_docx(file_path)
    elif extension in [".xlsx", ".xls"]:
        return extract_excel(file_path)
    elif extension == ".csv":
        return extract_csv(file_path)
    else:
        raise RuntimeError(f"Unsupported file type: {extension}")


# ============================================================
# ARGOS TRANSLATION
# ============================================================

def get_translation_model():
    package.update_package_index()
    available_packages = package.get_available_packages()

    languages = translate.get_installed_languages()
    arabic = next((lang for lang in languages if lang.code == "ar"), None)
    english = next((lang for lang in languages if lang.code == "en"), None)

    if arabic is None or english is None:
        print("Arabic -> English model not found. Downloading package...")
        package_to_install = next(
            filter(lambda x: x.from_code == "ar" and x.to_code == "en", available_packages),
            None
        )

        if package_to_install:
            package_to_install.install()
            languages = translate.get_installed_languages()
            arabic = next((lang for lang in languages if lang.code == "ar"), None)
            english = next((lang for lang in languages if lang.code == "en"), None)
        else:
            raise RuntimeError("Arabic -> English Argos package could not be located online.")

    model = arabic.get_translation(english) if arabic else None

    if model is None:
        raise RuntimeError("Arabic -> English Argos model could not be initialized.")

    return model


def translate_text(text):
    print("\nLoading Arabic -> English Translation...")
    model = get_translation_model()

    output = []
    blocks = text.split("\n\n")

    for number, block in enumerate(blocks, start=1):
        print(f"Translating block {number}/{len(blocks)}", end="\r")

        if not block.strip():
            output.append("")
            continue

        if contains_arabic(block):
            try:
                english = model.translate(block)
                output.append(english)
            except Exception as error:
                print(f"\nTranslation error: {error}")
                output.append(block)
        else:
            output.append(block)

    print()
    return "\n\n".join(output)


# ============================================================
# SAVE FILE
# ============================================================

def save_file(filename, text):
    with open(filename, "w", encoding="utf-8") as file:
        file.write(text)
    print(f"Saved: {filename}")


# ============================================================
# MAIN
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("\nUsage: python trans.py <input_file>")
        return

    input_file = sys.argv[1]

    if not os.path.isfile(input_file):
        print(f"File not found: {input_file}")
        return

    print("\n========================================")
    print("ARABIC -> ENGLISH TRANSLATOR & OCR")
    print("========================================")
    print(f"Input: {input_file}")

    # STEP 1: EXTRACTION & CONFIDENCE SCORING
    print("\n[1] Extracting text & computing confidence score...")
    try:
        extracted_text, confidence_score = extract_text(input_file)
        print(f"--> Overall Extraction Confidence Score: {confidence_score} ({int(confidence_score * 100)}%)")
    except Exception as error:
        print(f"\nExtraction error:\n{error}")
        return

    if not extracted_text.strip():
        print("No text found.")
        return

    save_file("extracted_text.txt", extracted_text)

    # STEP 2: TRANSLATION
    print("\n[2] Translating text...")
    try:
        english_text = translate_text(extracted_text)
    except Exception as error:
        print(f"\nTranslation error:\n{error}")
        return

    # STEP 3: SAVE
    save_file("english_translation.txt", english_text)

    print("\n========================================")
    print("COMPLETED")
    print("========================================")
    print(f"Confidence Score: {confidence_score}")
    print("Outputs generated: extracted_text.txt, english_translation.txt")


if __name__ == "__main__":
    main()