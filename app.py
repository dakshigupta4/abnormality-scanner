import re
import logging
import numpy as np
import pdfplumber
from PIL import Image
import pytesseract
from thefuzz import process
import streamlit as st

# ---------------- CONFIGURATION ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="OCR", page_icon="🚨", layout="centered")

# ---------------- 1. MASTER KEYWORDS ----------------
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

# ---------------- 2. INTELLIGENT RANGE EXTRACTORS ----------------
def extract_range(text):
    if not text:
        return None, None, None

    text = re.sub(r'\s+', ' ', text).strip()

    dash_match = re.search(r"(\d+(?:\.\d+)?)\s*[-to]\s*(\d+(?:\.\d+)?)", text)
    if dash_match:
        return float(dash_match.group(1)), float(dash_match.group(2)), dash_match.group(0)

    less = re.search(r"<\s*(\d+(?:\.\d+)?)", text)
    if less:
        return 0.0, float(less.group(1)), less.group(0)

    more = re.search(r">\s*(\d+(?:\.\d+)?)", text)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)

    return None, None, None

def extract_value(line, range_text):
    if not line:
        return None

    if range_text:
        line = line.replace(range_text, "")

    nums = re.findall(r"(\d+(?:\.\d+)?)", line)
    if not nums:
        return None

    try:
        value = float(nums[0])
        return value
    except:
        return None


# ---------------- 3. MULTI-LINE PARSER ----------------
def parse_text_block(full_text):
    results = []
    lines = [l.strip() for l in full_text.split("\n") if len(l.strip()) > 3]

    i = 0
    while i < len(lines):
        line = lines[i]

        ignore_terms = ["Test Name", "Result", "Reference", "Unit", "Method"]
        if any(x.lower() in line.lower() for x in ignore_terms):
            i += 1
            continue

        keyword = None
        clean = re.sub(r'[\d\W_]+', ' ', line)

        match = process.extractOne(clean, ALL_KEYWORDS, score_cutoff=85)
        if match:
            keyword = match[0]

        if keyword:
            std_name = next(k for k, v in TEST_MAPPING.items() if keyword in v)

            next_line = lines[i+1] if i+1 < len(lines) else ""
            block = line + " " + next_line

            min_r, max_r, range_txt = extract_range(block)
            val = extract_value(block, range_txt)

            if val is not None and min_r is not None:
                results.append({
                    "test_name": std_name,
                    "value": val,
                    "min": min_r,
                    "max": max_r,
                    "range": range_txt
                })

        i += 1

    return results


# ---------------- 4. FILE PROCESSING ----------------
def analyze_file(uploaded):
    filename = uploaded.name.lower()

    try:
        if filename.endswith(".pdf"):
            raw = ""
            with pdfplumber.open(uploaded) as pdf:
                for p in pdf.pages:
                    txt = p.extract_text()
                    if txt:
                        raw += "\n" + txt
            return parse_text_block(raw)

        else:
            image = Image.open(uploaded)
            text = pytesseract.image_to_string(image)
            return parse_text_block(text)

    except Exception as e:
        logger.error(str(e))
        return []


# ---------------- 5. FIND ABNORMALS ----------------
def get_abnormals(all_tests):
    abnormal = []
    added = set()

    for item in all_tests:
        val = item["value"]
        if val < item["min"]:
            item["status"] = "Low"
        elif val > item["max"]:
            item["status"] = "High"
        else:
            continue

        if item["test_name"] not in added:
            abnormal.append(item)
            added.add(item["test_name"])

    return abnormal


# ---------------- 6. STREAMLIT UI ----------------
def main():
    st.markdown("<h2 style='text-align:center;'>🚨 OCR Report Scanner</h2>", unsafe_allow_html=True)
    st.write("Upload a **lab report PDF / Image** and see **abnormal tests only**.")

    file = st.file_uploader("Upload Report", type=["pdf", "png", "jpg", "jpeg"])

    if file:
        with st.spinner("Extracting data..."):
            all_data = analyze_file(file)
            abnormal = get_abnormals(all_data)

        if not abnormal:
            st.success("No abnormalities found!")
        else:
            st.markdown("### ⚠ Abnormal Findings")
            for item in abnormal:
                color = "#ef4444" if item["status"] == "High" else "#3b82f6"

                box = f"""
                <div style="
                    background:white;
                    padding:14px;
                    margin-top:12px;
                    border-left:5px solid {color};
                    border-radius:8px;
                    box-shadow:0px 1px 4px rgba(0,0,0,0.15);
                ">
                    <b>{item['test_name']}</b><br>
                    Value: <b>{item['value']}</b>  
                    <span style="background:{color}; color:white; padding:2px 6px; border-radius:6px;">{item['status']}</span><br>
                    <small>Ref: {item['range']}</small>
                </div>
                """
                st.markdown(box, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
