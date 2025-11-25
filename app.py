import streamlit as st
import pdfplumber
from PIL import Image
import pytesseract
import re
from io import BytesIO


TEST_NAMES = [
    "Hemoglobin", "RBC", "HCT", "MCV", "MCH", "MCHC",
    "RDW-CV", "RDW-SD", "WBC", "NEU%", "LYM%", "MON%",
    "EOS%", "BAS%", "LYM#", "GRA#", "PLT", "ESR"
]


def clean(t):
    return re.sub(r"\s+", " ", t).strip()


# --------------------- COLUMN PARSER ---------------------
def parse_table_row(line):
    """
    Extracts: TestName | Value | Range | Units
    Handles OCR errors safely.
    """

    line = clean(line)

    # split by 2+ spaces (PDF tables preserve spacing)
    cols = re.split(r"\s{2,}", line)

    if len(cols) < 2:
        return None, None, None

    # column 0 = test name
    name = cols[0].strip()

    # column 1 = value
    value = None
    if len(cols) > 1:
        match_val = re.search(r"(\d+(?:\.\d+)?)", cols[1])
        if match_val:
            value = float(match_val.group(1))

    # column 2 = range
    mn = mx = None
    if len(cols) > 2:
        match_range = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", cols[2])
        if match_range:
            mn = float(match_range.group(1))
            mx = float(match_range.group(2))

    return name, value, (mn, mx)


# --------------------- READ PDF ---------------------
def extract_from_pdf(file):
    rows = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                for row in table:
                    line = "  ".join([str(c) for c in row if c])
                    rows.append(line)
    return rows


# --------------------- READ IMAGE ---------------------
def extract_from_image(file):
    img = Image.open(file)
    text = pytesseract.image_to_string(img)
    return text.split("\n")


# --------------------- EXTRACT TEST DATA ---------------------
def extract_tests(rows):
    results = {}

    for raw in rows:
        name, value, range_data = parse_table_row(raw)
        if not name:
            continue

        for test in TEST_NAMES:
            if name.lower() == test.lower():
                if value is not None and range_data:
                    mn, mx = range_data
                    results[test] = {
                        "value": value,
                        "min": mn,
                        "max": mx,
                        "line": raw
                    }

    return results


# --------------------- ABNORMAL CHECK ---------------------
def detect_abnormal(results):
    ab = []
    for test, d in results.items():
        if d["min"] is None or d["max"] is None:
            continue
        if d["value"] < d["min"]:
            d["status"] = "Low"
            ab.append(d)
        elif d["value"] > d["max"]:
            d["status"] = "High"
            ab.append(d)
    return ab


# --------------------- UI ---------------------
st.title("🧾 Accurate Lab Report OCR Scanner")

uploaded = st.file_uploader("Upload PDF/Image", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    if uploaded.name.endswith(".pdf"):
        rows = extract_from_pdf(uploaded)
    else:
        rows = extract_from_image(uploaded)

    results = extract_tests(rows)
    abnormal = detect_abnormal(results)

    st.subheader("Extracted Results")
    st.json(results)

    st.subheader("Abnormal Values")
    if abnormal:
        st.error(abnormal)
    else:
        st.success("No abnormalities found!")
