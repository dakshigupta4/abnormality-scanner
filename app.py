import streamlit as st
import easyocr
from PIL import Image
import pdfplumber
import re
import numpy as np

# ---------------- OCR INIT ----------------
reader = easyocr.Reader(["en"], gpu=False)

# ---------------- NORMAL RANGES ----------------
NORMAL_RANGES = {
    "Hemoglobin": (12, 15),
    "PCV": (36, 46),
    "RBC": (3.8, 4.8),
    "HCT": (37, 50),
    "MCV": (83, 101),
    "MCH": (27, 32),
    "MCHC": (31.5, 34.5),
    "RDW": (11.6, 14.4),
    "TLC": (4000, 10000),
    "WBC": (4.5, 11),
    "PLATELET": (150000, 400000),
    "MPV": (8.1, 13.9),
    "NLR": (0.78, 3.53),
    "ESR": (0, 15)
}

# ---------------- TEXT EXTRACT ----------------
def extract_pdf_text(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text


def extract_image_text(file):
    img = Image.open(file)
    img_np = np.array(img)
    results = reader.readtext(img_np, detail=0)
    return "\n".join(results)

# ---------------- XRAY TEXT ----------------
def extract_xray_report(text):
    report = {}
    keywords = ["FINDINGS", "IMPRESSION", "OPINION", "CONCLUSION"]

    lines = text.split("\n")
    current = None
    buffer = ""

    for line in lines:
        line_u = line.upper()
        for key in keywords:
            if key in line_u:
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

# ---------------- OCR CLEAN ----------------
def normalize_text(text):
    replacements = {
        "Hem0g10bin": "Hemoglobin",
        "Rec": "RBC",
        "yer": "HCT",
        "pur": "PLT",
        "wec": "WBC",
        "M0N": "MON",
        "R0W": "RDW"
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    text = text.replace("|", "1").replace("l", "1")
    return text

# ---------------- VALUE EXTRACTION ----------------
def extract_values(text):
    results = {}

    patterns = {
        "Hemoglobin": r"HEMOGLOBIN\s*([\d\.]+)",
        "PCV": r"PCV\s*([\d\.]+)",
        "RBC": r"RBC\s*([\d\.]+)",
        "HCT": r"HCT\s*([\d\.]+)",
        "MCV": r"MCV\s*([\d\.]+)",
        "MCH": r"MCH\s*([\d\.]+)",
        "MCHC": r"MCHC\s*([\d\.]+)",
        "RDW": r"RDW\s*([\d\.]+)",
        "WBC": r"WBC\s*([\d\.]+)",
        "TLC": r"TLC\s*([\d,]+)",
        "PLATELET": r"PLATELET\s*([\d,]+)",
        "MPV": r"MPV\s*([\d\.]+)",
        "NLR": r"NLR\s*([\d\.]+)",
        "ESR": r"ESR\s*([\d\.]+)"
    }

    for test, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue

        raw = match.group(1).replace(",", "").strip()

        try:
            value = float(raw)
        except:
            continue

        # ✅ Decimal Fix For RBC / WBC Errors
        if test in ["RBC", "WBC"] and value > 20:
            s = str(int(value))
            value = float(s[0] + "." + s[1:])

        # ✅ MCHC OCR Safety
        if test == "MCHC" and value < 10:
            value = 32.0

        results[test] = value

    return results

# ---------------- ANALYSIS ----------------
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

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="Blood & X-Ray Analyzer", layout="centered")

st.title("🧪 Blood & X-Ray Report Analyzer")

file = st.file_uploader("Upload PDF / Image", type=["pdf", "jpg", "jpeg", "png"])

if file:
    with st.spinner("Reading File..."):
        if file.name.lower().endswith(".pdf"):
            text = extract_pdf_text(file)
        else:
            text = extract_image_text(file)

        text = normalize_text(text)

    st.subheader("📄 Extracted Text")
    st.text_area("OCR Output", text, height=250)

    values = extract_values(text)
    blood = analyze(values)
    xray = extract_xray_report(text)

    # ✅ Blood Data
    if blood:
        st.subheader("🩸 Blood Report")
        st.table(blood)
    else:
        st.warning("No Blood Values Detected")

    # ✅ Xray
    if xray:
        st.subheader("🩻 X-Ray Report")
        for k, v in xray.items():
            st.write(f"**{k}:** {v}")
    else:
        st.info("No X-ray Sections Found")
