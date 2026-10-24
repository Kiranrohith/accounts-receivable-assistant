"""Run the (patched) payment-advice generator on a COPY of the seed, with pdftoppm replaced by pdfium
(poppler is not installed here). Only the facts matter (references, amounts, expected key): the
rasterised pixels of the scanned variants are not compared. Usage: run_gen.py <seed copy dir> <generator>"""

import importlib.util
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

seed, gen_path = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("genpay", gen_path)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def rasterize(pdf: Path, dpi: int) -> Image.Image:
    page = pdfium.PdfDocument(str(pdf))[0]
    return page.render(scale=dpi / 72).to_pil().convert("RGB")


gen.rasterize = rasterize
sys.argv = ["generate_payments.py", "--seed", str(seed), "--sender-base", "ar.sender.test@gmail.com",
            "--ar-inbox", "genworx.ar.test@gmail.com"]
gen.main()
