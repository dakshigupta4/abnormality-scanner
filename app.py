import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
from io import BytesIO
import re


# ------------------ Test names we trust EXACTLY ------------------
TEST_NAMES = [
    "Hemoglobin", "RBC", "HCT", "MCV", "MCH", "MCHC", "RDW-CV", "RDW-SD",
    "WBC", "NEU%", "LYM%", "MON%", "EOS%", "BAS%", "LYM#", "GRA#", "PLT", "ESR"
]


# -------------- Clean a line --------------
def clean(text):
    return re.sub(r"\s+", " ", text).strip()


# -------------- Extract row from table --------------
def parse_row(row_text):
    text = clean(row_text)

    # extract first number (value)
    value_match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not value_match:
        return None, None, None
    value = float(value_match.group(1))

    # extract range: number-number
    range_match = re.search(r"(\d+(?:\.\d+)?)[ ]*[-–][ ]*(\d+(?:\.\d+)?)", text)
    if range_match:
        min_r = float(range_match.group(1))
        max_r = float(range_match.group(2))
    else:
        min_r = max_r = None

    return value, min_r, max_r


# ----------------- Extract from PDF -----------------
def extract_from_pdf(uploaded):
    rows = []
    with pdfplumber.open(uploaded) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                for row in table:
                    rows.append(" ".join([str(c) for c in row if c]))
    return rows


# ----------------- Extract from Image -----------------
def extract_from_image(uploaded):
    img = Image.open(uploaded)
    text = pytesseract.image_to_string(img)
    return text.split("\n")


# ----------------- Main extraction logic -----------------
def extract_tests(rows):
    results = {}

    for line in rows:
        line_clean = clean(line)

        for test in TEST_NAMES:
            if re.search(rf"\b{re.escape(test)}\b", line_clean, re.IGNORECASE):
                value, mn, mx = parse_row(line_clean)
                if value is not None:
                    results[test] = {
                        "value": value,
                        "min": mn,
                        "max": mx,
                        "line": line_clean
                    }

    return results


# ----------------- Abnormal Checker -----------------
def detect_abnormal(results):
    abnormal = []
    for test, data in results.items():
        val = data["value"]
        mn = data["min"]
        mx = data["max"]

        if mn is None or mx is None:
            continue

        if val < mn:
            data["status"] = "Low"
            abnormal.append(data)
        elif val > mx:
            data["status"] = "High"
            abnormal.append(data)

    return abnormal


# ----------------- UI -----------------
st.title("🧾 Accurate Lab Report OCR Scanner")

uploaded = st.file_uploader("Upload PDF or Image", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    ext = uploaded.name.lower()

    if ext.endswith(".pdf"):
        rows = extract_from_pdf(uploaded)
    else:
        rows = extract_from_image(uploaded)

    results = extract_tests(rows)
    abnormal = detect_abnormal(results)

    st.subheader("Detected Results")
    st.json(results)

    st.subheader("Abnormal Values")
    if abnormal:
        st.error(abnormal)
    else:
        st.success("No abnormalities found!")
