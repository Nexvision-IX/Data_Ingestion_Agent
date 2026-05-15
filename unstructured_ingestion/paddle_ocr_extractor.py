from pathlib import Path
import os

import fitz
from paddleocr import PaddleOCR
from PIL import Image

ocr = PaddleOCR(
    use_angle_cls=True,
    lang="en"
)

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "unstructured_inputs"
OUTPUT_DIR = BASE_DIR / "extracted_text"
OUTPUT_DIR.mkdir(exist_ok=True)

def extract_text(image_path):
    result = ocr.ocr(str(image_path))

    extracted_text = ""
    for line in result:
        for word_info in line:
            extracted_text += word_info[1][0] + " "

    return extracted_text.strip()

def process_documents():
    files = list(INPUT_DIR.iterdir())
    print(f"\nFiles Found: {len(files)}")

    for file_path in files:
        print(f"\nProcessing -> {file_path.name}")
        final_text = ""

        try:
            if file_path.suffix.lower() == ".pdf":
                pdf_document = fitz.open(file_path)

                for page_num in range(len(pdf_document)):
                    page = pdf_document.load_page(page_num)
                    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))

                    temp_image_path = OUTPUT_DIR / f"temp_{page_num}.png"
                    pix.save(str(temp_image_path))

                    page_text = extract_text(temp_image_path)
                    final_text += page_text + "\n"

                    os.remove(temp_image_path)

            else:
                final_text = extract_text(file_path)

            output_file = OUTPUT_DIR / f"{file_path.stem}.txt"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_text)

            print(f"Text saved -> {output_file.name}")

        except Exception as e:
            print(f"Failed -> {file_path.name}: {e}")

    print("\nPADDLE OCR EXTRACTION COMPLETED")

if __name__ == "__main__":
    process_documents()