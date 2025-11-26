import streamlit as st
import pytesseract
from PIL import Image
import pdfplumber
import re

# ✅ NORMAL RANGES
NORMAL_RANGES = {
    "Hemoglobin": (11, 16),
    "RBC": (3.8, 4.8),
    "HCT": (37, 50),
    "MCV": (83, 101),
    "MCH": (27, 32),
    "MCHC": (31.5, 34.5),
    "RDW": (11.6, 14.4),
    "WBC": (4.5, 11),
    "PLT": (150, 450),
    "ESR": (0, 15)
}

# ---------------- PDF EXTRACT ----------------
def extract_pdf_text(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text


# ---------------- IMAGE OCR ----------------
def extract_image_text(file):
    img = Image.open(file)
    img = img.convert("L")
    return pytesseract.image_to_string(img)


# ---------------- FIX OCR ----------------
def normalize_text(text):
    fixes = {
        "Haemog1obin": "Haemoglobin",
        "Hem0g1obin": "Haemoglobin",
        "R8C": "RBC",
        "WB0": "WBC",
        "HCT": "HCT",
        "MCV": "MCV",
        "MCHC": "MCHC",
        "R0W": "RDW",
        "|": "1",
        "l": "1"
    }

    for k, v in fixes.items():
        text = text.replace(k, v)

    return text


# ---------------- REPORT FORMAT EXTRACT ----------------
def extract_values(text):
    results = {}

    patterns = [
        ("Hemoglobin", r"HAEMOGLOBIN\s+([\d.]+)"),
        ("RBC",        r"\bRBC\b\s+([\d.]+)"),
        ("HCT",        r"\bHCT\b\s+([\d.]+)"),
        ("MCV",        r"\bMCV\b\s+([\d.]+)"),
        ("MCH",        r"\bMCH\b\s+([\d.]+)"),
        ("MCHC",       r"\bMCHC\b\s+([\d.]+)"),
        ("WBC",        r"\bWBC\b\s+([\d.]+)"),
        ("RDW",        r"RDW[- ]?CV\s+([\d.]+)"),
        ("PLT",        r"\bPLT\b\s+([\d.]+)"),
        ("ESR",        r"\bESR\b\s+([\d.]+)")
    ]

    for test, pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(1).replace(",", "")
            try:
                value = float(val)
            except:
                continue

            # ✅ decimal correction
            if test in ["RBC", "WBC"] and value > 20:
                s = str(int(value))
                value = float(s[0] + "." + s[1:])

            # ✅ OCR guard
            if test == "MCHC" and value < 10:
                value = 32

            results[test] = value

    return results


# ---------------- ANALYZE ----------------
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


# ---------------- XRAY ----------------
def extract_xray_report(text):
    report = {}
    keywords = ["FINDINGS", "IMPRESSION", "CONCLUSION", "OPINION"]
    lines = text.split("\n")
    current = None
    buffer = ""

    for line in lines:
        u = line.upper().strip()

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


# ================= STREAMLIT =================
st.set_page_config("Lab Report Analyzer", layout="centered")
st.title("🧪 Blood & X-ray Analyzer")

file = st.file_uploader("Upload image or PDF", type=["pdf","png","jpg","jpeg"])

if file:

    if file.name.endswith(".pdf"):
        text = extract_pdf_text(file)
    else:
        text = extract_image_text(file)

    text = normalize_text(text)

    st.subheader("OCR Text")
    st.text_area("", text, height=200)

    blood = analyze(extract_values(text))
    xray  = extract_xray_report(text)

    if blood:
        st.subheader("🩸 Blood Report")
        st.table(blood)
    else:
        st.warning("❌ Blood report not detected")

    if xray:
        st.subheader("🩻 X-ray Report")
        for k, v in xray.items():
            st.write(f"**{k}**: {v}")
    else:
        st.info("ℹ No X-ray section found.")
