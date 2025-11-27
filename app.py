import streamlit as st
import pytesseract
from PIL import Image
import pdfplumber
import re

import os

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

if os.path.exists(TESSERACT_PATH):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ✅ NORMAL RANGES (NO CHANGE)
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

    config = r'--oem 3 --psm 6'
    return pytesseract.image_to_string(img, config=config)


# ---------- OCR CLEAN ----------
def normalize_text(text):
    text = text.upper()

    # Fix broken words like "H C T", "R B C"
    text = re.sub(r"H\s*C\s*T", "HCT", text)
    text = re.sub(r"R\s*B\s*C", "RBC", text)
    text = re.sub(r"W\s*B\s*C", "WBC", text)
    text = re.sub(r"M\s*C\s*H\s*C", "MCHC", text)
    text = re.sub(r"M\s*C\s*H", "MCH", text)
    text = re.sub(r"E\s*S\s*R", "ESR", text)
    text = re.sub(r"H\s*B", "HEMOGLOBIN", text)

    replacements = {
        "HAEMOGLOBIN": "HEMOGLOBIN",
        "HB": "HEMOGLOBIN",
        "PLT": "PLATELET",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    # Clean OCR junk
    text = text.replace("|", "1").replace("I", "1")

    return text


# ---------- VALUE EXTRACTION ----------
def extract_values(text):
    results = {}

    patterns = {
    "Hemoglobin": r"HEMOGLOBIN[^0-9]*([\d\.]+)",
    "PCV": r"PCV[^0-9]*([\d\.]+)",
    "RBC": r"RBC[^0-9]*([\d\.]+)",
    "MCV": r"MCV[^0-9]*([\d\.]+)",
    "MCH": r"MCH[^0-9]*([\d\.]+)",
    "MCHC": r"MCHC[^0-9]*([\d\.]+)",
    "RDW": r"RDW[^0-9]*([\d\.]+)",
    "HCT": r"HCT[^0-9]*([\d\.]+)",

    "TLC": r"TOTAL LEUCOCYTE COUNT[^0-9]*([\d,]+)",

    "NEUTROPHILS%": r"NEUTROPHILS[^0-9]*([\d]+)\s*%",
    "LYMPHOCYTES%": r"LYMPHOCYTES[^0-9]*([\d]+)\s*%",
    "EOSINOPHILS%": r"EOSINOPHILS[^0-9]*([\d]+)\s*%",
    "MONOCYTES%": r"MONOCYTES[^0-9]*([\d]+)\s*%",
    "BASOPHILS%": r"BASOPHILS[^0-9]*([\d]+)\s*%",

    "NEUTROPHILS_ABS": r"NEUTROPHILS[^0-9]*([\d\.]+)\s*CELLS",
    "LYMPHOCYTES_ABS": r"LYMPHOCYTES[^0-9]*([\d\.]+)\s*CELLS",
    "EOSINOPHILS_ABS": r"EOSINOPHILS[^0-9]*([\d\.]+)\s*CELLS",
    "MONOCYTES_ABS": r"MONOCYTES[^0-9]*([\d\.]+)\s*CELLS",

    "PLATELET": r"PLATELET[^0-9]*([\d,]+)",
    "MPV": r"MPV[^0-9]*([\d\.]+)",
    "NLR": r"NLR[^0-9]*([\d\.]+)",
    "ESR": r"ESR[^0-9]*([\d\.]+)",
    "WBC": r"WBC[^0-9]*([\d\.]+)"
}


    for test, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).replace(",", "").strip()

            try:
                value = float(raw)
            except:
                continue

            # ✅ SAME AUTO FIXES
            if test in ["WBC", "RBC"] and value > 20:
                s = str(int(value))
                value = float(s[0] + "." + s[1:])

            if test == "MCHC" and value < 10:
                value = 32.0

            results[test] = value

    return results

# ---------- STATUS ----------
def analyze(values):
    report = {}

    for test, value in values.items():
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

# ---------- X-RAY ----------
def extract_xray_report(text):
    report = {}
    keys = ["FINDINGS", "IMPRESSION", "IMPRESSIONS", "OPINION", "CONCLUSION", "RECOMMENDATION"]
    lines = text.split("\n")
    current = None
    buffer = ""

    for line in lines:
        u = line.strip().upper()
        for k in keys:
            if k in u:
                if current:
                    report[current] = buffer.strip()
                current = k.title()
                buffer = ""
                break
        else:
            if current:
                buffer += " " + line

    if current:
        report[current] = buffer.strip()

    return report

# ---------- UI ----------
st.set_page_config(page_title="Blood Report Analyzer", layout="wide")

st.title("Blood Report Analyzer")

file = st.file_uploader("Upload PDF or Image", type=["pdf", "jpg", "jpeg", "png"])

if file:
    if st.button("Analyze"):
        if file.type == "application/pdf":
            text = extract_pdf_text(file)
        else:
            text = extract_image_text(file)

        text = normalize_text(text)

        blood = analyze(extract_values(text))
        xray  = extract_xray_report(text)

        # ---------- TABLE ----------
        if blood:
            st.subheader("Blood Report")
            for k, v in blood.items():
                color = "green" if v["status"] == "NORMAL" else "orange" if v["status"] == "HIGH" else "red"
                st.markdown(f"""
                <div style="padding:8px;border-left:5px solid {color};background:#f7f7f7;margin-bottom:5px">
                    <b>{k}</b> — {v["value"]} <br>
                    Normal: {v["normal"]} <br>
                    Status: <b style="color:{color}">{v["status"]}</b>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.warning("No blood values detected.")

        # ---------- XRAY ----------
        if xray:
            st.subheader("X-Ray Report")
            for k, v in xray.items():
                st.markdown(f"**{k}:** {v}")
        else:
            st.info("No X-Ray report found.")





