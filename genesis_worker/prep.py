"""Page preparation: text layer, OCR for scanned pages, page images. Numbered lines so the coverage can be checked."""
import base64
import re
import fitz  # PyMuPDF

_ocr = None

def _ocr_engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr

def _ocr_lines(png_bytes):
    """Returns text lines of an image in reading order (top to bottom, then left to right)."""
    res, _ = _ocr_engine()(png_bytes)
    rows = []
    for box, text, conf in (res or []):
        ys = [p[1] for p in box]; xs = [p[0] for p in box]
        rows.append((min(ys), min(xs), max(ys) - min(ys), text.strip()))
    rows.sort(key=lambda r: (round(r[0] / max(r[2], 1)), r[1]))
    # join boxes that sit on the same line
    lines, cur, cur_y, cur_h = [], [], None, None
    for y, x, h, t in rows:
        if not t:
            continue
        if cur_y is not None and abs(y - cur_y) < 0.6 * max(cur_h, h, 1):
            cur.append(t)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur, cur_y, cur_h = [t], y, h
    if cur:
        lines.append(" ".join(cur))
    return lines

def prepare(pdf_bytes, file_label, max_pages, image_dpi=110):
    """Returns {pages:[{page,file,has_text,source,lines:[{id,text}],image_b64}], n_pages}.
    Line ids are p<page>-l<k> with the page counted across all files of the proposal (set by the caller via offset)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if len(doc) > max_pages:
        raise RuntimeError(f"{file_label} has {len(doc)} pages; the limit is {max_pages}.")
    pages = []
    for i, pg in enumerate(doc):
        text = pg.get_text("text") or ""
        raw = [l.strip() for l in text.splitlines() if l.strip()]
        pix = pg.get_pixmap(dpi=image_dpi)
        png = pix.tobytes("png")
        if len(" ".join(raw)) >= 40:
            lines, source = raw, "text_layer"
        else:
            hi = pg.get_pixmap(dpi=200).tobytes("png")
            lines, source = _ocr_lines(hi), "ocr"
        pages.append({"file": file_label, "page_in_file": i + 1, "source": source,
                      "lines": lines, "image_b64": base64.b64encode(png).decode()})
    return pages

def number_pages(all_pages):
    """Gives each page a number across the whole proposal and each line an id."""
    for n, p in enumerate(all_pages, 1):
        p["page"] = n
        p["lines"] = [{"id": f"p{n}-l{k}", "text": t} for k, t in enumerate(p["lines"], 1)]
    return all_pages

def page_text_block(p):
    head = f"=== PAGE {p['page']} (file: {p['file']}, page {p['page_in_file']}, source: {p['source']}{' - OCR is an aid only; confirm every number on the image' if p['source']=='ocr' else ''}) ==="
    return head + "\n" + "\n".join(f"[{l['id']}] {l['text']}" for l in p["lines"])

_num = re.compile(r"\$?\s*\(?-?\d[\d,]*\.?\d*\)?")

def numbers_in(text):
    """All amounts found in a text, as floats (commas removed)."""
    out = set()
    for m in _num.finditer(text):
        s = re.sub(r"[^\d.\-]", "", m.group(0))
        if s in ("", "-", "."):
            continue
        try:
            out.add(round(float(s), 2))
        except ValueError:
            pass
    return out
