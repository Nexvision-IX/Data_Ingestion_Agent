from pathlib import Path
import os
import json
import fitz

# IMPORTANT:
# DO NOT initialize PaddleOCR globally

ocr = None

# =========================================================
# OCR LOADER
# =========================================================

def get_ocr():

    global ocr

    if ocr is None:

        from paddleocr import PaddleOCR

        ocr = PaddleOCR(

            use_angle_cls=False,

            lang="en",

            show_log=False
        )

    return ocr

# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).parent

INPUT_DIR = (
    BASE_DIR / "unstructured_inputs"
)

OUTPUT_DIR = (
    BASE_DIR / "extracted_text"
)

DEBUG_DIR = (
    BASE_DIR / "structured_debug"
)

OUTPUT_DIR.mkdir(exist_ok=True)
DEBUG_DIR.mkdir(exist_ok=True)

# =========================================================
# HEADING KEYWORDS
# =========================================================

HEADING_KEYWORDS = [

    "invoice",
    "invoice #",
    "invoice no",
    "invoice number",
    "invoice date",
    "date",
    "due date",
    "bill to",
    "ship to",
    "vendor",
    "supplier",
    "po number",
    "p.o. no",
    "purchase order",
    "terms",
    "description",
    "qty",
    "quantity",
    "rate",
    "unit price",
    "amount",
    "subtotal",
    "tax",
    "vat",
    "gst",
    "total",
    "grand total",
    "balance due",
    "amount due",
    "currency"
]

# =========================================================
# TEXT CLEANER
# =========================================================

def normalize_text(text):

    return (
        str(text)
        .replace("\n", " ")
        .strip()
    )

# =========================================================
# OCR EXTRACTION
# =========================================================

def extract_text(image_path):

    ocr_engine = get_ocr()

    result = ocr_engine.ocr(
        str(image_path)
    )

    words = []

    for page in result:

        if page is None:
            continue

        for item in page:

            try:

                box = item[0]

                text = normalize_text(
                    item[1][0]
                )

                confidence = item[1][1]

                if confidence < 0.50:
                    continue

                if not text:
                    continue

                x_min = min(
                    point[0]
                    for point in box
                )

                y_min = min(
                    point[1]
                    for point in box
                )

                words.append({

                    "text": text,

                    "x": x_min,

                    "y": y_min
                })

            except Exception:
                continue

    # -----------------------------------------------------
    # SORT WORDS
    # -----------------------------------------------------

    words.sort(
        key=lambda w: (
            w["y"],
            w["x"]
        )
    )

    # -----------------------------------------------------
    # GROUP INTO LINES
    # -----------------------------------------------------

    lines = []

    current_line = []

    current_y = None

    Y_THRESHOLD = 18

    for word in words:

        if current_y is None:

            current_line.append(word)

            current_y = word["y"]

            continue

        if abs(word["y"] - current_y) <= Y_THRESHOLD:

            current_line.append(word)

        else:

            lines.append(current_line)

            current_line = [word]

            current_y = word["y"]

    if current_line:

        lines.append(current_line)

    # -----------------------------------------------------
    # REBUILD LINES
    # -----------------------------------------------------

    structured_lines = []

    for line_words in lines:

        line_words.sort(
            key=lambda w: w["x"]
        )

        line_text = " ".join(

            word["text"]

            for word in line_words
        )

        line_text = line_text.strip()

        if line_text:

            structured_lines.append(
                line_text
            )

    # -----------------------------------------------------
    # SEMANTIC STRUCTURE
    # -----------------------------------------------------

    semantic_lines = []

    for line in structured_lines:

        lower_line = line.lower()

        heading_found = False

        for keyword in HEADING_KEYWORDS:

            if keyword in lower_line:

                semantic_lines.append("")
                semantic_lines.append(
                    line.upper()
                )

                heading_found = True

                break

        if not heading_found:

            semantic_lines.append(line)

    final_text = "\n".join(
        semantic_lines
    )

    return final_text.strip()

# =========================================================
# PROCESS DOCUMENT
# =========================================================

def process_document(file_path):

    print(
        f"\nProcessing -> {file_path.name}"
    )

    final_text = ""

    try:

        # -------------------------------------------------
        # PDF
        # -------------------------------------------------

        if file_path.suffix.lower() == ".pdf":

            pdf_document = fitz.open(
                file_path
            )

            for page_num in range(
                len(pdf_document)
            ):

                page = pdf_document.load_page(
                    page_num
                )

                pix = page.get_pixmap(

                    matrix=fitz.Matrix(
                        3,
                        3
                    )
                )

                temp_image_path = (

                    OUTPUT_DIR /

                    f"temp_{page_num}.png"
                )

                pix.save(
                    str(temp_image_path)
                )

                page_text = extract_text(
                    temp_image_path
                )

                final_text += (
                    f"\n\n===== PAGE {page_num + 1} =====\n\n"
                )

                final_text += (
                    page_text + "\n"
                )

                os.remove(
                    temp_image_path
                )

        # -------------------------------------------------
        # IMAGE
        # -------------------------------------------------

        else:

            final_text = extract_text(
                file_path
            )

        # -------------------------------------------------
        # SAVE OUTPUT
        # -------------------------------------------------

        output_file = (

            OUTPUT_DIR /

            f"{file_path.stem}.txt"
        )

        with open(

            output_file,
            "w",
            encoding="utf-8"

        ) as f:

            f.write(final_text)

        debug_file = (

            DEBUG_DIR /

            f"{file_path.stem}_structured.txt"
        )

        with open(

            debug_file,
            "w",
            encoding="utf-8"

        ) as f:

            f.write(final_text)

        print(
            f"Text saved -> {output_file.name}"
        )

        return output_file

    except Exception as e:

        print(
            f"Failed -> {file_path.name}: {e}"
        )

        return None
