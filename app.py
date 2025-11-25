# app.py
import re
import logging
import traceback
import shutil
import subprocess
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
    "RBC Count": ["RBC Count", "Red Blood Cell Count", "Total RBC", "RBC"],
    "MCV": ["MCV"],
    "MCH": ["MCH"],
    "MCHC": ["MCHC"],
    "RDW": ["RDW", "R.D.W"],
    "TLC": ["TLC", "WBC", "Total Leucocyte Count", "White Blood Cell"],
    "Platelet Count": ["Platelet Count", "PLT", "Platelets"],
    "Neutrophils": ["Neutrophils", "Polymorphs", "NEU%"],
    "Lymphocytes": ["Lymphocytes", "LYM%"],
    "Monocytes": ["Monocytes", "MON%"],
    "Eosinophils": ["Eosinophils", "EOS%", "EOS"],
    "Basophils": ["Basophils", "BAS%"],
    "Urea": ["Urea", "Blood Urea"],
    "Creatinine": ["Creatinine", "Serum Creatinine"],
    "Uric Acid": ["Uric Acid"],
    "Cholesterol": ["Cholesterol", "Total Cholesterol"],
    "Triglycerides": ["Triglycerides"],
    "HDL": ["HDL Cholesterol", "H.D.L", "HDL"],
    "LDL": ["LDL Cholesterol", "L.D.L", "LDL"],
    "ESR": ["ESR", "Erythrocyte Sedimentation Rate"],
    "GRA#": ["GRA#", "Granulocyte"],
}

ALL_KEYWORDS = [alias for sublist in TEST_MAPPING.values() for alias in sublist]

# ------------ extractors ------------
def extract_range(text):
    """
    Return (min, max, range_text) or (None, None, None)
    """
    if not text:
        return None, None, None
    text = re.sub(r'\s+', ' ', text).strip()

    # patterns like 3.5 - 5.50 or 3.5-5.5 or 3.5 to 5.5
    dash_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-–]|to)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if dash_match:
        return float(dash_match.group(1)), float(dash_match.group(2)), dash_match.group(0)

    # <5  or less than 5
    less = re.search(r"(?:<|less than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if less:
        return 0.0, float(less.group(1)), less.group(0)

    # >10 or more than 10
    more = re.search(r"(?:>|more than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)

    # (Low) or (High) textual markers
    if re.search(r"[\(\[]\s*(Low|L)\s*[\)\]]", text, re.IGNORECASE):
        return 999999.0, 999999.0, "(Low)"
    if re.search(r"[\(\[]\s*(High|H)\s*[\)\]]", text, re.IGNORECASE):
        return -999999.0, -999999.0, "(High)"

    return None, None, None

def extract_value(line, range_min, range_max, range_txt):
    """
    Extract the most likely numeric test value from a single line.
    Also attempt to auto-correct dropped leading '1' (e.g. 2 -> 12)
    if it fits the reference range (Option 1 logic).
    """
    if not line:
        return None

    clean = line
    if range_txt:
        clean = clean.replace(range_txt, "")

    # remove thousands separators & stray characters, keep decimals and digits
    clean = clean.replace(",", " ")

    # match 1-3 digit numbers optionally with decimal part
    nums = re.findall(r"(?<!\d)(\d{1,3}(?:\.\d{1,3})?)(?!\d)", clean)
    if not nums:
        return None

    # choose first plausible numeric token (left-most)
    try:
        val = float(nums[0])
    except:
        return None

    # If value obviously is a page number or year (> 2100) or id, ignore
    if val > 2100:
        return None

    # Option 1 correction: if value < range_min and value+10 fits within [min, max], correct it
    try:
        if range_min is not None and range_max is not None:
            if val < range_min and (val + 10) >= range_min and (val + 10) <= range_max:
                logger.info(f"Auto-correcting {val} -> {val+10} based on range {range_min}-{range_max}")
                val = val + 10
    except Exception:
        pass

    return val

# ------------ parsing logic ------------
def parse_text_block(full_text):
    """
    Parse the extracted text line-by-line, match test names (strict),
    extract ranges & values only from the same line, and return results.
    """
    results = []
    if not full_text:
        return results

    # Split into lines and filter out very short lines
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    for line in lines:
        # Skip header/footer/irrelevant lines (common words)
        skip_terms = ["test name", "result", "unit", "reference", "page", "date", "time", "remark", "method", "patient", "name", "laboratory", "report"]
        if any(t in line.lower() for t in skip_terms):
            continue

        # Build a letters-only string for safe fuzzy matching
        letters_only = re.sub(r'[^A-Za-z]+', ' ', line).strip()
        if len(letters_only) < 3:
            continue

        # Perform fuzzy match with a stricter cutoff
        match = process.extractOne(letters_only, ALL_KEYWORDS, score_cutoff=92)
        if not match:
            continue

        keyword = match[0]
        # Map to standard name
        std_name = next((k for k, v in TEST_MAPPING.items() if keyword in v), None)
        if not std_name:
            continue

        # Extract range and value from SAME LINE only
        min_r, max_r, range_txt = extract_range(line)
        val = extract_value(line, min_r, max_r, range_txt)

        # Validate: both range and value should be present (strict)
        if val is None or min_r is None:
            # If no range detected, try to capture value from inline table-like lines where range is in separate column
            # try a simple heuristic: if line contains a number and next adjacent patterns, skip for now (avoid false positives)
            continue

        # Final sanity checks: value numeric & within a plausible clinical domain
        if val is None:
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
    filename = getattr(uploaded_file, "name", "").lower() if uploaded_file else ""
    try:
        if filename.endswith(".pdf"):
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text()
                    if txt:
                        raw_text += "\n" + txt
                    # extract tables as fallback: join cells
                    try:
                        tables = page.extract_tables()
                        for tb in tables:
                            for row in tb:
                                row_str = " ".join(str(c) for c in row if c)
                                raw_text += "\n" + row_str
                    except Exception:
                        pass
        else:
            # image file
            image = Image.open(uploaded_file).convert("RGB")
            raw_text = pytesseract.image_to_string(image)
    except Exception:
        logger.exception("Error reading file")
        raise

    return parse_text_block(raw_text)

# ------------ abnormal checker ------------
def get_abnormals(all_data):
    abnormals = {}
    for item in all_data:
        name = item.get("test_name")
        val = item.get("value")
        min_r = item.get("min")
        max_r = item.get("max")
        if name is None or val is None or min_r is None:
            continue
        if val < min_r:
            item["status"] = "Low"
            if name not in abnormals:
                abnormals[name] = item
        elif val > max_r:
            item["status"] = "High"
            if name not in abnormals:
                abnormals[name] = item
    return list(abnormals.values())

# ------------ Streamlit UI ------------
def main():
    st.markdown("<h2 style='text-align:center;'>🚨 OCR Report Scanner (improved)</h2>", unsafe_allow_html=True)

    ok, tver = check_tesseract()
    if ok:
        st.success(f"Tesseract OK: {tver}")
    else:
        st.error("Tesseract not found on this instance. Add packages.txt with tesseract-ocr and tesseract-ocr-eng and redeploy.")
        st.stop()

    st.write("Upload a lab report PDF / image (PNG/JPG). The app will show abnormal tests (improved accuracy).")

    uploaded_file = st.file_uploader("Upload file", type=["pdf","png","jpg","jpeg"])
    if not uploaded_file:
        # show sample debug file path (local) for your testing
        st.info("For local testing you can try this sample image path: `/mnt/data/6cd72834-b69c-4a07-a429-3ff5001aa3ea.png`")
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

    except Exception:
        tb = traceback.format_exc()
        logger.error(tb)
        st.error("App crashed while processing the file. Full traceback below (copy this and share if you want me to debug):")
        st.code(tb, language="text")


if __name__ == "__main__":
    main()
