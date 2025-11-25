import re
import logging
import traceback
import shutil
import subprocess
import numpy as np
import pdfplumber
from PIL import Image
import pytesseract
from thefuzz import process
import streamlit as st

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="OCR", page_icon="🚨", layout="centered")

# ------------ helper: check tesseract availability ------------
def check_tesseract():
    tpath = shutil.which("tesseract")
    if not tpath:
        return False, None
    try:
        out = subprocess.check_output(["tesseract", "--version"], stderr=subprocess.STDOUT, text=True)
        return True, out.splitlines()[0]
    except Exception as e:
        return True, f"tesseract present at {tpath} but failed to get version: {e}"

# ------------ Keywords ------------
TEST_MAPPING = {
    "TSH": ["Thyroid Stimulating Hormone", "TSH", "TSH Ultra", "T.S.H"],
    "Total T3": ["Total T3", "Triiodothyronine", "T3"],
    "Total T4": ["Total T4", "Thyroxine", "T4"],
    "Vitamin D": ["Vitamin D", "25-OH Vitamin D", "Total 25 OH Vitamin D"],
    "Vitamin B12": ["Vitamin B12", "Cyanocobalamin", "Vit B12"],
    "HbA1c": ["HbA1c", "Glycosylated Hemoglobin"],
    "Glucose Fasting": ["Fasting Blood Sugar", "FBS", "Glucose Fasting"],
    "Glucose PP": ["Post Prandial", "PPBS", "Glucose PP"],
    "Hemoglobin": ["Hemoglobin", "Hb", "Haemoglobin"],
    "PCV": ["PCV", "Packed Cell Volume", "Hematocrit", "HCT"],
    "RBC Count": ["RBC Count", "Red Blood Cell Count", "Total RBC"],
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW", "R.D.W"],
    "TLC": ["TLC", "WBC", "Total Leucocyte Count", "White Blood Cell"],
    "Platelet Count": ["Platelet Count", "PLT", "Platelets"],
    "Neutrophils": ["Neutrophils", "Polymorphs"],
    "Lymphocytes": ["Lymphocytes"],
    "Monocytes": ["Monocytes"],
    "Eosinophils": ["Eosinophils"],
    "Basophils": ["Basophils"],
    "Urea": ["Urea", "Blood Urea"],
    "Creatinine": ["Creatinine", "Serum Creatinine"],
    "Uric Acid": ["Uric Acid"],
    "Cholesterol": ["Cholesterol", "Total Cholesterol"],
    "Triglycerides": ["Triglycerides"],
    "HDL": ["HDL Cholesterol", "H.D.L"],
    "LDL": ["LDL Cholesterol", "L.D.L"]
}
ALL_KEYWORDS = [alias for sublist in TEST_MAPPING.values() for alias in sublist]

# ------------ extractors ------------
def extract_range(text):
    if not text:
        return None, None, None

    text = re.sub(r'\s+', ' ', text).strip()

    # 5-10, 3.2 - 4.5 etc
    dash_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–to]\s*(\d+(?:\.\d+)?)", text)
    if dash_match:
        return float(dash_match.group(1)), float(dash_match.group(2)), dash_match.group(0)

    # <5, less than 10
    less = re.search(r"(?:<|less than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if less:
        return 0.0, float(less.group(1)), less.group(0)

    # >10, more than 8
    more = re.search(r"(?:>|more than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)

    # (Low), (High)
    if re.search(r"[\(\[]\s*(Low|L)\s*[\)\]]", text, re.IGNORECASE):
        return 999999.0, 999999.0, "(Low)"
    if re.search(r"[\(\[]\s*(High|H)\s*[\)\]]", text, re.IGNORECASE):
        return -999999.0, -999999.0, "(High)"

    return None, None, None

def extract_value(line, range_str):
    if not line:
        return None

    clean = line
    if range_str:
        clean = clean.replace(range_str, "")

    clean = clean.replace(",", "")

    # Only match proper lab values (1-3 digits, decimals allowed)
    nums = re.findall(r"(?<!\d)(\d{1,3}(?:\.\d{1,3})?)(?!\d)", clean)

    if not nums:
        return None

    try:
        return float(nums[0])
    except:
        return None

# ------------ parsing logic ------------
def parse_text_block(full_text):
    results = []

    lines = [l.strip() for l in full_text.split("\n") if len(l.strip()) > 1]

    for line in lines:
        # Skip common non-test lines
        skip_terms = ["test name", "result", "unit", "reference", "page", "date", "time", "remark", "method"]
        if any(x in line.lower() for x in skip_terms):
            continue

        # Extract letters only for matching
        clean_line = re.sub(r'[^A-Za-z]+', ' ', line).strip()

        if len(clean_line) < 3:
            continue

        match = process.extractOne(clean_line, ALL_KEYWORDS, score_cutoff=92)
        if not match:
            continue

        keyword = match[0]
        std_name = next((k for k, v in TEST_MAPPING.items() if keyword in v), None)
        if not std_name:
            continue

        # Extract range + value from same line only
        min_r, max_r, range_txt = extract_range(line)
        val = extract_value(line, range_txt)

        if val is None or min_r is None:
            continue

        results.append({
            "test_name": std_name,
            "value": val,
            "min": min_r,
            "max": max_r,
            "range": range_txt
        })

    return results

# ------------ file analyzer ------------
def analyze_file(uploaded_file):
    raw_text = ""
    filename = uploaded_file.name.lower()

    try:
        if filename.endswith(".pdf"):
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        raw_text += "\n" + text

                    tables = page.extract_tables()
                    for tb in tables:
                        for row in tb:
                            raw_text += "\n" + " ".join(str(x) for x in row if x)
        else:
            image = Image.open(uploaded_file).convert("RGB")
            raw_text = pytesseract.image_to_string(image)

    except Exception:
        logger.exception("Error reading file")
        raise

    return parse_text_block(raw_text)

# ------------ abnormal checker ------------
def get_abnormals(data):
    unique = {}
    for item in data:
        name = item["test_name"]
        val = item["value"]
        if name not in unique:
            if val < item["min"]:
                item["status"] = "Low"
                unique[name] = item
            elif val > item["max"]:
                item["status"] = "High"
                unique[name] = item
    return list(unique.values())

# ------------ Streamlit UI ------------
def main():
    st.markdown("<h2 style='text-align:center;'>🚨 OCR Report Scanner</h2>", unsafe_allow_html=True)

    ok, tver = check_tesseract()
    if ok:
        st.success(f"Tesseract OK: {tver}")
    else:
        st.error("Tesseract is missing. Add packages.txt with tesseract-ocr and tesseract-ocr-eng.")
        st.stop()

    st.write("Upload a lab report (PDF / PNG / JPG).")

    f = st.file_uploader("Upload file", type=["pdf","png","jpg","jpeg"])
    if not f:
        return

    try:
        with st.spinner("Scanning..."):
            all_data = analyze_file(f)
            abn = get_abnormals(all_data)

        if not abn:
            st.success("✔ No abnormalities detected")
            return

        for item in abn:
            color = "#ef4444" if item["status"] == "High" else "#3b82f6"
            st.markdown(f"""
                <div style="padding:12px; border-left:5px solid {color}; background:white; border-radius:6px;">
                    <b>{item['test_name']}</b>
                    <br>Value: <b>{item['value']}</b>
                    <span style="background:{color};color:white;padding:2px 6px;border-radius:4px;">
                        {item['status']}
                    </span>
                    <br><small>Ref: {item['range']}</small>
                </div>
            """, unsafe_allow_html=True)

    except Exception:
        tb = traceback.format_exc()
        st.error("App crashed. See details below:")
        st.code(tb)

if __name__ == "__main__":
    main()
