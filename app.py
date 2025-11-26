import streamlit as st
import easyocr
from PIL import Image
import pdfplumber
import re
import numpy as np
from collections import Counter

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
    "ESR": (0, 15),
}

# For matching lines in different style reports
TEST_PATTERNS = {
    "Hemoglobin": ["HEMOGLOBIN", "HAEMOGLOBIN"],
    "PCV": ["PCV"],
    "RBC": ["RBC COUNT", "RBC"],
    "HCT": ["HCT"],
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW", "R.D.W"],
    "TLC": ["TOTAL LEUCOCYTE COUNT", "TLC"],
    "WBC": ["WBC"],
    "PLATELET": ["PLATELET COUNT", "PLATELET"],
    "MPV": ["MPV"],
    "NLR": ["NLR"],
    "ESR": ["ESR"],
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


# ---------------- X-RAY TEXT ----------------
def extract_xray_report(text):
    report = {}
    keywords = ["FINDINGS", "IMPRESSION", "OPINION", "CONCLUSION", "RECOMMENDATION"]

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
        "R0W": "RDW",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    text = text.replace("|", "1").replace("l", "1")
    return text


# ---------------- HELPER: PICK RESULT FROM LINE ----------------
def get_result_number_from_line(line: str):
    """
    Line example: 'HCT 36 37.0-50.0 %'
    Numbers = [36, 37.0, 50.0]
    Range pattern = 37.0-50.0
    -> we remove 37.0 & 50.0 and keep 36
    """

    # remove commas for things like 14,330
    line_clean = line.replace(",", " ")

    # all numbers in line
    nums = re.findall(r"\d+\.?\d*", line_clean)
    if not nums:
        return None

    # numbers which are part of ranges (a-b)
    ranges = re.findall(r"(\d+\.?\d*)\s*-\s*(\d+\.?\d*)", line_clean)
    to_remove = []
    for a, b in ranges:
        to_remove.append(a)
        to_remove.append(b)

    remove_counts = Counter(to_remove)
    filtered = []
    for n in nums:
        if remove_counts[n] > 0:
            remove_counts[n] -= 1
        else:
            filtered.append(n)

    # prefer numbers not in range; if none, use first number
    target_list = filtered if filtered else nums
    if not target_list:
        return None

    try:
        return float(target_list[0])
    except:
        return None


# ---------------- VALUE EXTRACTION ----------------
def extract_values(text):
    results = {}

    lines = text.splitlines()
    upper_lines = [l.upper() for l in lines]

    for test, names in TEST_PATTERNS.items():
        for line, uline in zip(lines, upper_lines):
            if any(name in uline for name in names):
                value = get_result_number_from_line(line)
                if value is None:
                    continue

                # ✅ DECIMAL FIX ONLY IF OBVIOUS WRONG (like 67 -> 6.7)
                if test in ["RBC", "WBC"] and value > 20:
                    s = str(int(value))
                    if len(s) == 2:
                        value = float(s[0] + "." + s[1])
                    elif len(s) == 3:
                        value = float(s[0] + "." + s[1:])

                # ✅ OCR PROTECTION FOR MCHC (like 3 -> 33)
                if test == "MCHC" and value < 10:
                    value = 32.0

                results[test] = value
                break

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
            # else normal
        else:
            status = "NORMAL"

        report[test] = {
            "value": value,
            "normal": f"{low} - {high}",
            "status": status,
        }

    return report


# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="Blood & X-Ray Analyzer", layout="centered")

st.title("🧪 Blood & X-Ray Report Analyzer")

file = st.file_uploader("Upload PDF / Image", type=["pdf", "jpg", "jpeg", "png"])

if file:
    with st.spinner("Reading report..."):
        if file.name.lower().endswith(".pdf"):
            text = extract_pdf_text(file)
        else:
            text = extract_image_text(file)

        text = normalize_text(text)

    st.subheader("📄 Extracted OCR Text")
    st.text_area("OCR Output", text, height=250)

    values = extract_values(text)
    blood = analyze(values)
    xray = extract_xray_report(text)

    # BLOOD TABLE
    if blood:
        st.subheader("🩸 Blood Report")
        st.table(blood)
    else:
        st.warning("No blood values detected.")

    # X-RAY
    if xray:
        st.subheader("🩻 X-Ray Report")
        for k, v in xray.items():
            st.write(f"**{k}:** {v}")
    else:
        st.info("No X-ray sections found.")

