"""
Final Corrected and Robust OCR lab-report scanner for Streamlit.
- The extract_value function has been aggressively simplified to ensure it captures
  the result value even with complex/mangled OCR text and units.
- Should now correctly identify the Low RBC (3.3) and HCT (36) values.
"""
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
from io import BytesIO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Sample image path available in this environment (for testing / debug)
SAMPLE_IMAGE_PATH = "/mnt/data/6cd72834-b69c-4a07-a429-3ff5001aa3ea.png"

st.set_page_config(page_title="OCR Lab Scanner (Final Corrected)", page_icon="🚨", layout="centered")

# ----------------- helpers -----------------
def check_tesseract():
    tpath = shutil.which("tesseract")
    if not tpath:
        return False, None
    try:
        out = subprocess.check_output(["tesseract", "--version"], stderr=subprocess.STDOUT, text=True)
        return True, out.splitlines()[0]
    except Exception as e:
        return True, f"tesseract present at {tpath} but failed to get version: {e}"

# ------------ keywords & mapping ------------
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
    "RBC": ["RBC Count", "Red Blood Cell Count", "Total RBC", "RBC"],
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

# ----------------- extractors -----------------
def extract_range(text):
    """
    Return (min, max, range_text) or (None, None, None)
    """
    if not text:
        return None, None, None
    t = re.sub(r'\s+', ' ', text).strip()

    # DASH or 'to' ranges
    dash_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-–]|to)\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if dash_match:
        return float(dash_match.group(1)), float(dash_match.group(2)), dash_match.group(0)

    # <5 or less than 5 / Up to 15
    less = re.search(r"(?:<|less than|up to)\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if less:
        return 0.0, float(less.group(1)), less.group(0)

    # >10 or more than 10
    more = re.search(r"(?:>|more than)\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)

    # (Low) / (High) - Special ranges that don't need min/max comparison later
    if re.search(r"[\(\[]\s*(Low|L)\s*[\)\]]", t, re.IGNORECASE):
        return 999999.0, 999999.0, "(Low)"
    if re.search(r"[\(\[]\s*(High|H)\s*[\)\]]", t, re.IGNORECASE):
        return -999999.0, -999999.0, "(High)"

    return None, None, None

def extract_value(line, range_min, range_max, range_txt):
    """
    Extract numeric value from a single line and optionally auto-correct
    dropped leading '1' (Option 1 logic).
    (Revised to be more aggressive in number extraction)
    """
    if not line:
        return None

    txt = line
    if range_txt:
        # Remove the range text to isolate the result value
        txt = txt.replace(range_txt, "")

    # **CRITICAL CORRECTION:** Aggressively clean the line to isolate numbers.
    # Replace any character that is NOT a digit, a decimal point, or a space with a space.
    txt = re.sub(r'[^\d\.\s]', ' ', txt) 
    txt = txt.replace(",", "") # Ensure commas are gone

    # Now find any number (1 to 3 digits, optional decimal)
    nums = re.findall(r"(\d{1,3}(?:\.\d{1,3})?)", txt) 
    
    if not nums:
        return None

    # choose left-most plausible numeric
    try:
        val = float(nums[0])
    except:
        return None

    # ignore years/IDs
    if val > 2100:
        return None

    # Safe auto-correct: only if range available and adding 10 fits
    try:
        if range_min is not None and range_max is not None:
            is_valid_range = range_min != 999999.0 and range_max != -999999.0
            
            if is_valid_range and val < range_min and (val + 10) >= range_min and (val + 10) <= range_max:
                logger.info(f"Auto-correcting {val} -> {val+10} based on range {range_min}-{range_max}")
                val = val + 10
    except Exception:
        pass

    return val

# ----------------- parsing logic -----------------
def parse_text_block(full_text):
    """
    Parse text line-by-line, perform fuzzy matching and extract
    only when both range and value are present on the same line.
    """
    results = []
    if not full_text:
        return results

    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    for line in lines:
        low = line.lower()
        # Skip header/footer lines more aggressively
        skip_terms = ["test name", "result", "unit", "reference", "page", "date", "time", "remark", "method", "patient", "name", "laboratory", "report", "id", "doctor", "age", "sex"]
        if any(t in low for t in skip_terms):
            continue

        # letters-only for matching
        letters_only = re.sub(r'[^A-Za-z]+', ' ', line).strip()
        if len(letters_only) < 3:
            continue

        # Use relaxed cutoff=85
        match = process.extractOne(letters_only, ALL_KEYWORDS, score_cutoff=85) 
        if not match:
            continue

        keyword = match[0]
        std_name = next((k for k, v in TEST_MAPPING.items() if keyword in v), None)
        if not std_name:
            continue

        # Extract range and value from same line
        min_r, max_r, range_txt = extract_range(line)
        val = extract_value(line, min_r, max_r, range_txt)

        # Require both range & value (strict mode)
        if val is None or min_r is None:
            continue

        results.append({
            "test_name": std_name,
            "value": val,
            "min": min_r,
            "max": max_r,
            "range": range_txt
        })
        
    # Deduplicate: only take the first instance found for a test name
    unique_results = []
    seen_names = set()
    for item in results:
        if item["test_name"] not in seen_names:
            unique_results.append(item)
            seen_names.add(item["test_name"])
            
    return unique_results


# ----------------- file analyzer -----------------
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
            uploaded_file.seek(0)
            image = Image.open(uploaded_file).convert("RGB")
            raw_text = pytesseract.image_to_string(image)
    except Exception:
        logger.exception("Error reading file")
        raise

    return parse_text_block(raw_text)

# ----------------- abnormal checker -----------------
def get_abnormals(all_data):
    abn = {}
    for item in all_data:
        name = item.get("test_name")
        val = item.get("value")
        min_r = item.get("min")
        max_r = item.get("max")
        if name is None or val is None or min_r is None:
            continue
            
        # Skip High/Low flags that don't have comparison boundaries
        is_valid_range = min_r != 999999.0 and max_r != -999999.0

        if is_valid_range:
            if val < min_r:
                item["status"] = "Low"
                if name not in abn:
                    abn[name] = item
            elif val > max_r:
                item["status"] = "High"
                if name not in abn:
                    abn[name] = item
    return list(abn.values())

# ----------------- Streamlit UI -----------------
def main():
    st.markdown("<h2 style='text-align:center;'>🚨 OCR Lab Report Scanner (Final Corrected)</h2>", unsafe_allow_html=True)

    ok, tver = check_tesseract()
    if ok:
        st.success(f"Tesseract OK: {tver}")
    else:
        st.error("Tesseract not found. Add packages.txt with 'tesseract-ocr' and 'tesseract-ocr-eng' and redeploy.")
        st.stop()

    st.write("Upload a lab report (PDF / PNG / JPG). The app will list abnormal test results.")

    uploaded_file = st.file_uploader("Upload file", type=["pdf", "png", "jpg", "jpeg"])

    # Debug: quick-load sample image (only works in this environment)
    if st.button("Load sample debug image"):
        try:
            with open(SAMPLE_IMAGE_PATH, "rb") as fh:
                file_bytes = fh.read()
            uploaded_file = BytesIO(file_bytes)
            uploaded_file.name = SAMPLE_IMAGE_PATH.split("/")[-1]
            st.session_state["uploaded_file"] = uploaded_file
            st.rerun() 
        except FileNotFoundError:
            st.error(f"Sample image not found at: {SAMPLE_IMAGE_PATH}. This button only works in specific environments.")
        except Exception as e:
            st.error(f"Failed to load sample image: {e}")

    if uploaded_file is None:
        uploaded_file = st.session_state.get("uploaded_file")

    if uploaded_file is None:
        st.info("Tip: You can use the 'Load sample debug image' button for quick testing (only inside this environment).")
        return

    try:
        with st.spinner("Scanning document..."):
            all_data = analyze_file(uploaded_file)
            abnormals = get_abnormals(all_data)

        if not all_data:
             st.warning("⚠️ Could not extract any test results. Try a clearer image.")
        
        st.subheader("Results with Abnormal Values")
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

        st.subheader("All Extracted Results (Debug)")
        if all_data:
             st.json(all_data)
        else:
             st.info("No data extracted.")

    except Exception:
        tb = traceback.format_exc()
        logger.error(tb)
        st.error("App crashed while processing the file. Full traceback below:")
        st.code(tb, language="text")

if __name__ == "__main__":
    main()
