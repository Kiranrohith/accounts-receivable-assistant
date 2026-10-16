"""Download the RapidOCR ONNX models from Hugging Face into backend/models/rapidocr.

RapidOCR's own registry points at modelscope.cn, which is unreachable from some
networks. Run once locally, and during the Docker image build.
"""

from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = "SWHL/RapidOCR"
FILES = (
    "PP-OCRv4/ch_PP-OCRv4_det_infer.onnx",
    "PP-OCRv4/ch_PP-OCRv4_rec_infer.onnx",
    "PP-OCRv1/ch_ppocr_mobile_v2.0_cls_infer.onnx",
)
TARGET = Path(__file__).resolve().parents[1] / "models" / "rapidocr"


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = hf_hub_download(REPO, name, local_dir=TARGET)
        print(f"ok  {path}")


if __name__ == "__main__":
    main()
