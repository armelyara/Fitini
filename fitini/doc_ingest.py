"""
doc_ingest.py — extract analyzable text from a source file.

Server-side, offline, deterministic tooling (no LLM):
  * text (.txt/.md/.csv/.log/.json)  -> read directly
  * PDF (.pdf)                       -> PyMuPDF text layer; if scanned, OCR pages (RapidOCR)
  * DOCX (.docx)                     -> python-docx
  * images (.png/.jpg/...)           -> OCR (RapidOCR)

Optional libs are lazy-loaded with a clear error naming what to install.
"""
import os
import tempfile

TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json"}
DOC_EXT = {".pdf", ".docx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

_OCR = None
_OCR_TRIED = False


def is_text_like(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (TEXT_EXT | DOC_EXT | IMAGE_EXT)


def _ocr_engine():
    """Lazy RapidOCR (ONNX, CPU, offline). None if not installed."""
    global _OCR, _OCR_TRIED
    if _OCR_TRIED:
        return _OCR
    _OCR_TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _OCR = RapidOCR()
    except Exception:
        _OCR = None
    return _OCR


def _ocr_file(path: str) -> str:
    ocr = _ocr_engine()
    if ocr is None:
        raise RuntimeError("OCR non disponible : installer rapidocr-onnxruntime "
                           "(pip install rapidocr-onnxruntime) cote serveur")
    result, _ = ocr(path)
    return "\n".join(line[1] for line in (result or []))


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ext in IMAGE_EXT:
        return _ocr_file(path)
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
        except Exception:
            raise RuntimeError("PDF non lu : installer PyMuPDF (pip install pymupdf) cote serveur")
        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        if len(text.strip()) >= 20:
            return text  # has a real text layer
        # scanned PDF -> OCR each page via RapidOCR
        ocr = _ocr_engine()
        if ocr is None:
            if text.strip():
                return text
            raise RuntimeError("PDF scanne : installer rapidocr-onnxruntime pour l'OCR")
        out = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            tmp.close()
            try:
                pix.save(tmp.name)
                res, _ = ocr(tmp.name)
                out.append("\n".join(line[1] for line in (res or [])))
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
        return "\n".join(out)
    if ext == ".docx":
        try:
            import docx  # python-docx
        except Exception:
            raise RuntimeError("DOCX non lu : installer python-docx cote serveur")
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    raise RuntimeError("Format non supporte : " + ext)


def to_lines(text: str):
    return [ln.strip() for ln in text.splitlines() if ln.strip()]
