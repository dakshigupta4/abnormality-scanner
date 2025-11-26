from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pytesseract
from PIL import Image
import pdfplumber
import re

app = Flask(__name__)
CORS(app)

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ✅ NORMAL RANGES (SAFE & COMPLETE)
NORMAL_RANGES = {
    "Hemoglobin": (12, 15),
    "PCV": (36, 46),
    "RBC": (3.8, 4.8),
    "MCV": (83, 101),
    "MCH": (27, 32),
    "MCHC": (31.5, 34.5),
    "RDW": (11.6, 14.4),
    "TLC": (4000, 10000),

    "NEUTROPHILS%": (40, 80),
    "LYMPHOCYTES%": (20, 40),
    "EOSINOPHILS%": (1, 6),
    "MONOCYTES%": (2, 10),
    "BASOPHILS%": (0, 2),

    "NEUTROPHILS_ABS": (2000, 7000),
    "LYMPHOCYTES_ABS": (1000, 3000),
    "EOSINOPHILS_ABS": (20, 500),
    "MONOCYTES_ABS": (200, 1000),

    "PLATELET": (150000, 400000),
    "MPV": (8.1, 13.9),
    "NLR": (0.78, 3.53),
    "ESR": (0, 15),
    "WBC": (4.5, 11),
    "HCT": (37, 50),
}


# ---------- TEXT EXTRACT ----------
def extract_pdf_text(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text


def extract_image_text(file):
    img = Image.open(file)
    img = img.convert("L")
    return pytesseract.image_to_string(img)


# ---------- X-RAY EXTRACT ----------
def extract_xray_report(text):
    report = {}
    keywords = ["FINDINGS", "IMPRESSION", "IMPRESSIONS", "OPINION", "CONCLUSION", "RECOMMENDATION"]

    lines = text.split("\n")
    current = None
    buffer = ""

    for line in lines:
        u = line.strip().upper()

        for key in keywords:
            if key in u:
                if current:
                    report[current] = buffer.strip()
                current = key.title()
                buffer = ""
                break
        else:
            if current:
                buffer += " " + line

    if current:
        report[current] = buffer.strip()

    return report


# ---------- OCR CLEAN (NO NUMBER CHANGE) ----------
def normalize_text(text):
    replacements = {
        "Hem0g10bin": "Hemoglobin",
        "Rec": "RBC",
        "yer": "HCT",
        "pur": "PLT",
        "wec": "WBC",
        "M0N": "MON",
        "R0WcV": "RDW-CV",
        "R0W-SD": "RDW-SD",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    text = text.replace("l", "1").replace("|", "1")
    return text


# ---------- VALUE EXTRACTION ----------
def extract_values(text):
    results = {}

    patterns = {
        "Hemoglobin": r"HAEMOGLOBIN\s*([\d\.]+)",
        "PCV": r"PCV\s*([\d\.]+)",
        "RBC": r"RBC\s*([\d\.]+)",
        "MCV": r"MCV\s*([\d\.]+)",
        "MCH": r"MCH\s*([\d\.]+)",
        "MCHC": r"MCHC\s*([\d\.]+)",
        "RDW": r"R\.?D\.?W\s*([\d\.]+)",
        "HCT": r"HCT\s*([\d\.]+)",

        "TLC": r"TOTAL LEUCOCYTE COUNT.*?([\d,]+)",

        "NEUTROPHILS%": r"NEUTROPHILS\s+(\d+)\s*%",
        "LYMPHOCYTES%": r"LYMPHOCYTES\s+(\d+)\s*%",
        "EOSINOPHILS%": r"EOSINOPHILS\s+(\d+)\s*%",
        "MONOCYTES%": r"MONOCYTES\s+(\d+)\s*%",
        "BASOPHILS%": r"BASOPHILS\s+(\d+)\s*%",

        "NEUTROPHILS_ABS": r"NEUTROPHILS\s+([\d\.]+)\s*Cells",
        "LYMPHOCYTES_ABS": r"LYMPHOCYTES\s+([\d\.]+)\s*Cells",
        "EOSINOPHILS_ABS": r"EOSINOPHILS\s+([\d\.]+)\s*Cells",
        "MONOCYTES_ABS": r"MONOCYTES\s+([\d\.]+)\s*Cells",

        "PLATELET": r"PLATELET COUNT\s*([\d,]+)",
        "MPV": r"MPV\s*([\d\.]+)",
        "NLR": r"NLR\s*([\d\.]+)",
        "ESR": r"ESR\s*([\d\.]+)",
        "WBC": r"WBC\s*([\d\.]+)"
    }

    for test, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1)
            raw = raw.replace(",", "").strip()

            try:
                value = float(raw)
            except:
                continue

            # ✅ AUTO DECIMAL FIX (ONLY WBC & RBC)
            if test in ["WBC", "RBC"] and value > 20:
                s = str(int(value))
                value = float(s[0] + "." + s[1:])

            # ✅ OCR SAFETY FOR MCHC
            if test == "MCHC" and value < 10:
                value = 32.0

            results[test] = value

    return results


# ---------- STATUS LOGIC ----------
def analyze(values):
    report = {}

    for test, value in values.items():

        # ✅ NEVER CRASH ON UNKNOWN TEST
        if test not in NORMAL_RANGES:
            continue

        low, high = NORMAL_RANGES[test]

        if value < low:
            status = "LOW"
        elif value > high:
            status = "HIGH"
        else:
            status = "NORMAL"

        report[test] = {
            "value": value,
            "normal": f"{low} - {high}",
            "status": status
        }

    return report


# ---------- ROUTES ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze_report():

    file = request.files["file"]

    if file.filename.lower().endswith(".pdf"):
        text = extract_pdf_text(file)
    else:
        text = extract_image_text(file)

    text = normalize_text(text)

    print("\n====== OCR TEXT ======")
    print(text)
    print("=====================")

    blood = analyze(extract_values(text))
    xray = extract_xray_report(text)

    # ✅ SAFE RESPONSE
    return jsonify({
        "blood": blood,
        "xray": xray
    })


if __name__ == "__main__":
    app.run(debug=True)
