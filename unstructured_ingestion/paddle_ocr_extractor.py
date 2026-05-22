from pathlib import Path
import os
import json
import fitz
from paddleocr import PaddleOCR

# =========================================================
# OCR INITIALIZATION
# =========================================================

ocr = PaddleOCR(
    use_angle_cls=True,
    lang="en"
)

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

OUTPUT_DIR.mkdir(
    exist_ok=True
)

DEBUG_DIR.mkdir(
    exist_ok=True
)

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
# UTILITIES
# =========================================================

def normalize_text(text):

    return (
        str(text)
        .replace("\n", " ")
        .strip()
    )

# =========================================================
# OCR STRUCTURED EXTRACTION
# =========================================================

def extract_text(image_path):

    result = ocr.ocr(
        str(image_path)
    )

    words = []

    # -----------------------------------------------------
    # EXTRACT WORDS + COORDINATES
    # -----------------------------------------------------

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

                # skip weak OCR
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

                x_max = max(
                    point[0]
                    for point in box
                )

                y_max = max(
                    point[1]
                    for point in box
                )

                words.append({

                    "text": text,

                    "x_min": x_min,

                    "y_min": y_min,

                    "x_max": x_max,

                    "y_max": y_max
                })

            except Exception:

                continue

    # -----------------------------------------------------
    # SORT WORDS TOP TO BOTTOM
    # -----------------------------------------------------

    words.sort(

        key=lambda w: (
            w["y_min"],
            w["x_min"]
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

            current_y = word["y_min"]

            continue

        # SAME VISUAL LINE
        if abs(
            word["y_min"] - current_y
        ) <= Y_THRESHOLD:

            current_line.append(word)

        else:

            # SAVE PREVIOUS LINE
            lines.append(current_line)

            # START NEW LINE
            current_line = [word]

            current_y = word["y_min"]

    # APPEND LAST LINE
    if current_line:

        lines.append(current_line)

    # -----------------------------------------------------
    # REBUILD LINES LEFT TO RIGHT
    # -----------------------------------------------------

    structured_lines = []

    for line_words in lines:

        line_words.sort(
            key=lambda w: w["x_min"]
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
    # SEMANTIC HEADING STRUCTURE
    # -----------------------------------------------------

    semantic_lines = []

    previous_line_y = None

    for idx, line in enumerate(structured_lines):

        lower_line = line.lower()

        # ---------------------------------------------
        # ADD HEADING BREAKS
        # ---------------------------------------------

        heading_found = False

        for keyword in HEADING_KEYWORDS:

            if keyword in lower_line:

                semantic_lines.append(
                    ""
                )

                semantic_lines.append(
                    line.upper()
                )

                heading_found = True

                break

        if not heading_found:

            semantic_lines.append(
                line
            )

    # -----------------------------------------------------
    # FINAL CLEANUP
    # -----------------------------------------------------

    final_text = "\n".join(
        semantic_lines
    )

    final_text = "\n".join(

        line.strip()

        for line in final_text.split("\n")
    )

    # REMOVE EXTRA BLANK LINES
    cleaned_lines = []

    previous_blank = False

    for line in final_text.split("\n"):

        if not line.strip():

            if not previous_blank:

                cleaned_lines.append("")

            previous_blank = True

        else:

            cleaned_lines.append(line)

            previous_blank = False

    final_text = "\n".join(
        cleaned_lines
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
        # PDF FILE
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
        # IMAGE FILE
        # -------------------------------------------------

        else:

            final_text = extract_text(
                file_path
            )

        # -------------------------------------------------
        # SAVE OCR TEXT
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

        # -------------------------------------------------
        # SAVE DEBUG STRUCTURED FILE
        # -------------------------------------------------

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

        print(
            f"Structured debug saved -> "
            f"{debug_file.name}"
        )

        return output_file

    except Exception as e:

        print(
            f"Failed -> "
            f"{file_path.name}: {e}"
        )

        return None

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    processed_file_path = (

        BASE_DIR /

        "processed_files.json"
    )

    # -----------------------------------------------------
    # LOAD PROCESSED FILES
    # -----------------------------------------------------

    with open(

        processed_file_path,
        "r",
        encoding="utf-8"

    ) as f:

        processed_files = json.load(f)

    # -----------------------------------------------------
    # GET NEW FILES ONLY
    # -----------------------------------------------------

    files = [

        f for f in INPUT_DIR.iterdir()

        if f.name not in processed_files
    ]

    print(
        f"\nNew Files Found: {len(files)}"
    )

    # -----------------------------------------------------
    # PROCESS FILES
    # -----------------------------------------------------

    for file_path in files:

        result = process_document(
            file_path
        )

        if result:

            processed_files.append(
                file_path.name
            )

    # -----------------------------------------------------
    # SAVE UPDATED LIST
    # -----------------------------------------------------

    with open(

        processed_file_path,
        "w",
        encoding="utf-8"

    ) as f:

        json.dump(

            processed_files,
            f,
            indent=4
        )

    print(
        "\nNEW FILE OCR COMPLETED"
    )