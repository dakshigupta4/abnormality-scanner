import streamlit as st
import pytesseract
from PIL import Image
import pdfplumber
import re
from io import BytesIO


# ------------------ Tests we care about ------------------
TESTS = {
    "Hemoglobin": r"(hemoglobin|hb)\b",
    "RBC": r"\brbc\b",
    "HCT": r"\b(hct|pcv)\b",
    "MCV": r"\bMCV\b",
    "MCH": r"\bMCH\b",
    "MCHC": r"\bMCHC\b",
    "RDW-CV": r"RDW.?CV",
    "RDW-SD": r"RDW.?SD",
    "WBC": r"\b(WBC|TLC)\b",
    "NEU%": r"(Neutrophils|NEU%)",
    "LYM%": r"(Lymphocytes|LYM%)",
    "MON%": r"(Monocytes|MON%)",
    "EOS%": r"(Eosinophils|EOS%)",
    "BAS%": r"(Basophils|BAS%)",
    "LYM#": r"LYM#",
    "GRA#": r"GRA#",
    "PLT": r"(Platelet|PLT)",
    "ESR": r"\bESR\b"
}


def clean(t):
    return re.sub(r"\s+", " ", t).strip()


# ---------- Extract value and range from a line ----------
def extract_value_and_range(line):
    line = clean(line)

    # Value = first number
    val_match = re.search(r"\b(\d+(?:\.\d+)?)\b", line)
    value = float(val_match.group(1)) if val_match else None

    # Range = X-Y
    range_match = re.search(r"(\d+(\.\d+)?)\s*[-–]\s*(\d+(\.\d+)?)", line)
    if range_match:
        mn = float(range_match.group(1))
        mx = float(range_match.group(3))
    else:
        mn = mx = None

    return value, mn, mx


# ---------- Read PDF ----------
def read_pdf(file):
    rows = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                rows.extend(text.split("\n"))
    return rows


# ---------- Read Image ----------
def read_image(file):
    img = Image.open(file)
    text = pytesseract.image_to_string(img)
    return text.split("\n")


# ---------- Extract all tests ----------
def extract_tests(rows):
    results = {}

    for line in rows:
        l = clean(line).lower()

        for test_name, pattern in TESTS.items():
            if re.search(pattern, l):
                value, mn, mx = extract_value_and_range(line)

                if value is not None and mn is not None:
                    results[test_name] = {
                        "value": value,
                        "min": mn,
                        "max": mx,
                        "line": line
                    }

    return results


# ---------- Detect abnormal ----------
def detect_abnormal(results):
    ab = []
    for test, d in results.items():
        val = d["value"]
        if d["min"] <= val <= d["max"]:
            continue

        d["status"] = "Low" if val < d["min"] else "High"
        ab.append(d)

    return ab


# ------------------ UI ------------------
st.title("🧾 Universal Abnormal Value OCR Scanner")

uploaded = st.file_uploader("Upload PDF / Image", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    if uploaded.name.lower().endswith(".pdf"):
        rows = read_pdf(uploaded)
    else:
        rows = read_image(uploaded)

    results = extract_tests(rows)
    abnormal = detect_abnormal(results)

    st.subheader("Extracted Tests")
    st.json(results)

    st.subheader("Abnormal Values")
    if abnormal:
        st.error(abnormal)
    else:
        st.success("No Abnormalities Found!")
