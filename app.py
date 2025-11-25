# app.py
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

# ------------ Keywords (same as before) ------------
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
    dash_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–to]\s*(\d+(?:\.\d+)?)", text)
    if dash_match:
        return float(dash_match.group(1)), float(dash_match.group(2)), dash_match.group(0)
    less = re.search(r"(?:<|less than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if less:
        return 0.0, float(less.group(1)), less.group(0)
    more = re.search(r"(?:>|more than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)
    if re.search(r"[\(\[]\s*(?:Low|L)\s*[\)\]]", text, re.IGNORECASE):
        return 999999.0, 999999.0, "(Low)"
    if re.search(r"[\(\[]\s*(?:High|H)\s*[\)\]]", text, re.IGNORECASE):
        return -999999.0, -999999.0, "(High)"
    return None, None, None

def extract_value(text_source, range_str):
    if not text_source:
        return None
    clean = text_source
    if range_str:
        clean = clean.replace(range_str, "")
    clean = clean.replace(",", "")
    nums = re.findall(r"(\d+(?:\.\d+)?)", clean)
    valid_nums = []
    for n in nums:
        try:
            f = float(n)
            if f < 2000 or (f > 2100 and f < 10000):
                valid_nums.append(f)
        except:
            continue
    if not valid_nums:
        return None
    return valid_nums[0]

def parse_text_block(full_text):
    results = []
    lines = [l.strip() for l in full_text.split("\n") if len(l.strip()) > 3]
    i = 0
    while i < len(lines):
        line = lines[i]
        ignore_terms = ["Test Name", "Result", "Unit", "Reference", "Page", "Date", "Time", "Remark", "Method"]
        if any(x.lower() in line.lower() for x in ignore_terms):
            i += 1
            continue
        text_for_matching = re.sub(r'[\d\W_]+', ' ', line)
        keyword = None
        for safe in ["Hb", "PCV", "TLC", "RBC", "MCV", "MCH", "MCHC", "RDW", "TSH"]:
            if re.search(r'\b' + re.escape(safe) + r'\b', line, re.IGNORECASE):
                keyword = safe
                break
        if not keyword:
            match = process.extractOne(text_for_matching, ALL_KEYWORDS, score_cutoff=85)
            if match:
                keyword = match[0]
        if keyword:
            try:
                std_name = next(k for k, v in TEST_MAPPING.items() if keyword in v)
            except StopIteration:
                i += 1
                continue
            next_line = lines[i + 1] if (i + 1) < len(lines) else ""
            context_block = line + " " + next_line
            min_r, max_r, range_txt = extract_range(context_block)
            val = extract_value(context_block, range_txt)
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

def analyze_file(uploaded_file):
    raw_text = ""
    filename = uploaded_file.name.lower()
    try:
        if filename.endswith(".pdf"):
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text()
                    if txt:
                        raw_text += "\n" + txt
                        tables = page.extract_tables()
                        for tb in tables:
                            for row in tb:
                                raw_str = " ".join([str(c) for c in row if c])
                                raw_text += "\n" + raw_str
        else:
            image = Image.open(uploaded_file).convert("RGB")
            text = pytesseract.image_to_string(image)
            raw_text = text
    except Exception as e:
        logger.exception("Error reading file")
        raise
    return parse_text_block(raw_text)

def get_abnormals(all_data):
    abnormals = []
    seen_tests = set()
    for item in all_data:
        name = item["test_name"]
        val = item["value"]
        is_low = val < item["min"]
        is_high = val > item["max"]
        if is_low or is_high:
            if name not in seen_tests:
                item["status"] = "Low" if is_low else "High"
                abnormals.append(item)
                seen_tests.add(name)
    return abnormals

# ------------- Streamlit UI with robust error display -------------
def main():
    st.markdown("<h2 style='text-align:center;'>🚨 OCR Report Scanner (Tesseract)</h2>", unsafe_allow_html=True)

    ok, tver = check_tesseract()
    if ok:
        st.success(f"Tesseract OK: {tver}")
    else:
        st.error("Tesseract not found on this instance. Add `packages.txt` with `tesseract-ocr` and `tesseract-ocr-eng` to your repo and redeploy.")
        st.stop()

    st.write("Upload a lab report PDF / image (PNG/JPG). The app will show abnormal tests.")

    uploaded_file = st.file_uploader("Upload file", type=["pdf", "png", "jpg", "jpeg"])
    if not uploaded_file:
        return

    try:
        with st.spinner("Scanning document..."):
            all_data = analyze_file(uploaded_file)
            abnormals = get_abnormals(all_data)

        if not abnormals:
            st.success("✅ No Abnormalities Found")
        else:
            for item in abnormals:
                status = item["status"]
                color = "#ef4444" if status == "High" else "#3b82f6"
                st.markdown(f"""
                    <div style="background:white; padding:12px; border-left:5px solid {color}; border-radius:8px;">
                        <b>{item['test_name']}</b><br>
                        Value: <b>{item['value']}</b>
                        <span style="background:{color}; color:#fff; padding:2px 6px; border-radius:6px; margin-left:8px;">{status}</span><br>
                        <small>Ref: {item['range']}</small>
                    </div>
                """, unsafe_allow_html=True)

    except Exception as e:
        # Show full traceback in UI for debugging
        tb = traceback.format_exc()
        logger.error(tb)
        st.error("App crashed while processing the file. Full traceback below (copy this and share if you want me to debug):")
        st.code(tb, language="text")

if __name__ == "__main__":
    main()
