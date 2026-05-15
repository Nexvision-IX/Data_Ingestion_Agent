import os
from pathlib import Path

import pdfplumber
import pytesseract

from PIL import Image
import fitz  # PyMuPDF
import cv2
import numpy as np

# -----------------------------------
# OCR QUALITY SCORE
# -----------------------------------

def calculate_ocr_score(text):

    score = 0

    keywords = [
        "invoice",
        "total",
        "subtotal",
        "vat",
        "gst",
        "currency",
        "qty",
        "amount",
        "po"
    ]

    text_lower = text.lower()

    for keyword in keywords:

        if keyword in text_lower:
            score += 10

    score += len(text.split())

    return score

# -----------------------------------
# IMAGE PREPROCESSING
# -----------------------------------

def preprocess_image(image_path):

    image = cv2.imread(str(image_path))

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    thresh = cv2.threshold(
        gray,
        150,
        255,
        cv2.THRESH_BINARY
    )[1]

    denoised = cv2.fastNlMeansDenoising(thresh)

    processed_path = OUTPUT_DIR / "processed_temp.png"

    cv2.imwrite(str(processed_path), denoised)

    return processed_path


# -----------------------------------
# TESSERACT PATH (WINDOWS)
# -----------------------------------

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# -----------------------------------
# PATHS
# -----------------------------------

BASE_DIR = Path(__file__).parent

INPUT_DIR = BASE_DIR / "unstructured_inputs"
OUTPUT_DIR = BASE_DIR / "extracted_text"

OUTPUT_DIR.mkdir(exist_ok=True)

# -----------------------------------
# PDF TEXT EXTRACTION
# -----------------------------------

def extract_text_from_pdf(pdf_path):

    extracted_text = ""

    try:

        with pdfplumber.open(pdf_path) as pdf:

            for page in pdf.pages:

                text = page.extract_text()

                if text:
                    extracted_text += text + "\n"

    except Exception as e:

        print(f"PDF extraction failed for {pdf_path.name}: {e}")

    return extracted_text


# -----------------------------------
# OCR FOR IMAGE
# -----------------------------------

def extract_text_from_image(image_path):

    best_text = ""
    best_score = -1

    try:

        # ---------------------------
        # TRY 1 -> NORMAL OCR
        # ---------------------------

        image = Image.open(image_path)

        text1 = pytesseract.image_to_string(
            image,
            config="--oem 3 --psm 6"
        )

        score1 = calculate_ocr_score(text1)

        if score1 > best_score:

            best_score = score1
            best_text = text1

        # ---------------------------
        # TRY 2 -> PREPROCESSED OCR
        # ---------------------------

        processed_path = preprocess_image(image_path)

        processed_image = Image.open(processed_path)

        text2 = pytesseract.image_to_string(
            processed_image,
            config="--oem 3 --psm 6"
        )

        score2 = calculate_ocr_score(text2)

        if score2 > best_score:

            best_score = score2
            best_text = text2

        os.remove(processed_path)

    except Exception as e:

        print(f"OCR failed for {image_path.name}: {e}")

    return best_text


# -----------------------------------
# OCR SCANNED PDF
# -----------------------------------

def extract_scanned_pdf(pdf_path):

    extracted_text = ""

    try:

        pdf_document = fitz.open(pdf_path)

        for page_num in range(len(pdf_document)):

            page = pdf_document.load_page(page_num)

            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))

            image_path = OUTPUT_DIR / f"temp_page_{page_num}.png"

            pix.save(str(image_path))

            image = Image.open(image_path)

            text = extract_text_from_image(image_path)

            extracted_text += text + "\n"

            os.remove(image_path)

    except Exception as e:

        print(f"Scanned PDF OCR failed for {pdf_path.name}: {e}")

    return extracted_text


# -----------------------------------
# MAIN PROCESSOR
# -----------------------------------

def process_documents():

    files = list(INPUT_DIR.iterdir())

    print(f"\nFiles Found: {len(files)}")

    for file_path in files:

        print(f"\nProcessing -> {file_path.name}")

        extracted_text = ""

        # ---------------------------
        # PDF FILE
        # ---------------------------

        if file_path.suffix.lower() == ".pdf":

            extracted_text = extract_text_from_pdf(file_path)

            # fallback to OCR if empty
            if not extracted_text.strip():

                print("No readable text found. Running OCR...")

                extracted_text = extract_scanned_pdf(file_path)

        # ---------------------------
        # IMAGE FILE
        # ---------------------------

        elif file_path.suffix.lower() in [".png", ".jpg", ".jpeg"]:

            extracted_text = extract_text_from_image(file_path)

        else:

            print(f"Unsupported file type: {file_path.name}")
            continue

        # ---------------------------
        # SAVE TEXT
        # ---------------------------

        output_file = OUTPUT_DIR / f"{file_path.stem}.txt"

        with open(output_file, "w", encoding="utf-8") as f:

            f.write(extracted_text)

        print(f"Extracted text saved -> {output_file.name}")

    print("\nOCR EXTRACTION COMPLETED")


# -----------------------------------
# ENTRY POINT
# -----------------------------------

if __name__ == "__main__":

    process_documents()