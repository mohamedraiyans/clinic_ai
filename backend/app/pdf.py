import io
import os

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def build(rx, doctor, patient) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, doctor.clinic)
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"{doctor.name}  |  Reg. No: {doctor.reg_no}")
    c.line(50, y - 8, w - 50, y - 8)
    y -= 32
    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Patient: {patient.name}   Age: {patient.age}   Sex: {patient.sex}   Weight: {patient.weight_kg} kg")
    y -= 16
    c.drawString(50, y, f"Date: {rx.issued_at}")
    y -= 16
    if patient.allergies:
        c.drawString(50, y, f"Allergies: {', '.join(patient.allergies)}")
        y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Complaint / findings:")
    c.setFont("Helvetica", 10)
    for line in _wrap(rx.complaint, w - 60 - 50)[:6]:
        y -= 13
        c.drawString(60, y, line)
    y -= 30
    c.setFont("Helvetica-Bold", 22)
    c.drawString(50, y, "Rx")
    y -= 24
    max_w = w - 75 - 50  # text area for item details, inside the right margin

    def room(need: float) -> float:
        # keep the lower part of the last page free for the signature block
        if y - need < 215:
            c.showPage()
            return h - 60
        return y

    for i, it in enumerate(rx.items, 1):
        y = room(60)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(60, y, f"{i}. {it['name']}")
        y -= 14
        c.setFont("Helvetica", 10)
        detail = "  -  ".join(p for p in (it.get("dose", ""), it.get("frequency", ""), it.get("duration", "")) if p)
        for line in _wrap(detail, max_w) + _wrap(it.get("instructions", ""), max_w):
            y = room(13)
            c.setFont("Helvetica", 10)
            c.drawString(75, y, line)
            y -= 13
        y -= 14

    # signature block
    sy = 150
    c.setFont("Helvetica-Oblique", 24)
    c.drawString(60, sy + 20, doctor.name.replace("Dr. ", ""))
    c.line(50, sy + 14, 260, sy + 14)
    c.setFont("Helvetica", 9)
    c.drawString(50, sy, f"Digitally signed by {doctor.name} ({doctor.reg_no})")
    c.drawString(50, sy - 12, f"Signature: {rx.signature[:32]}...")
    c.drawString(50, sy - 24, f"ID: {rx.id}")

    url = f"{os.getenv('PUBLIC_BASE_URL', 'http://localhost:8000')}/verify/{rx.id}"
    c.setFont("Helvetica", 7)
    c.drawString(50, sy - 38, f"Verify: {url}")

    code = qr.QrCodeWidget(url)
    x0, y0, x1, y1 = code.getBounds()
    size = 110
    qx, qy = w - 50 - size, 118
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(code)
    renderPDF.draw(d, c, qx, qy)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(qx + size / 2, qy - 11, "Scan to verify")
    c.setFont("Helvetica", 7)
    c.drawCentredString(qx + size / 2, qy - 20, "authenticity of this prescription")

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 40, "AI-assisted draft, reviewed, edited and approved by the prescribing doctor.")
    c.save()
    return buf.getvalue()


def _wrap(text: str, max_width: float, font: str = "Helvetica", size: int = 10) -> list[str]:
    """Wrap by real rendered width, so long text never runs past the margin."""
    lines, cur = [], ""
    for word in text.split():
        while stringWidth(word, font, size) > max_width:  # one word wider than a line: hard-split it
            if cur:
                lines.append(cur)
                cur = ""
            cut = len(word) - 1
            while cut > 1 and stringWidth(word[:cut], font, size) > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        trial = f"{cur} {word}".strip()
        if cur and stringWidth(trial, font, size) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines
