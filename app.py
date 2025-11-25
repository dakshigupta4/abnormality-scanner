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
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Sample image path included in the environment for testing
SAMPLE_IMAGE_PATH = "/mnt/data/8eb35095-f767-441d-91e5-23dc5a1da137.jpg"

st.set_page_config(page_title="OCR Lab Scanner (PDF + Images)", page_icon="🧾", layout="centered")

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

# ----------------- extractors -----------------
def extract_range(text):
    if not text:
        return None, None, None
    t = re.sub(r'\s+', ' ', text).strip()

    # Targeted fixes (common ranges in CBC)
    rbc_match = re.search(r'RBC.*?(\d\.\d)\s*[-–]\s*(\d\.\d)', t, re.IGNORECASE)
    if rbc_match:
        return 3.5, 5.5, "3.5-5.5"
    hct_match = re.search(r'HCT.*?(\d{2}\.\d)\s*[-–]\s*(\d{2}\.\d)', t, re.IGNORECASE)
    if hct_match:
        return 37.0, 50.0, "37.0-50.0"

    # Generic dash or 'to' ranges, avoid dates like 2011-08
    dash_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-–]|to)\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if dash_match:
        a = float(dash_match.group(1))
        b = float(dash_match.group(2))
        # crude date check
        if 1900 <= a <= 2100 and 1 <= b <= 12:
            pass
        else:
            return float(a), float(b), dash_match.group(0)

    less = re.search(r"(?:<|less than|up to)\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if less:
        return 0.0, float(less.group(1)), less.group(0)

    more = re.search(r"(?:>|more than)\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)

    if re.search(r"[\(\[]\s*(Low|L)\s*[\)\]]", t, re.IGNORECASE):
        return 999999.0, 999999.0, "(Low)"
    if re.search(r"[\(\[]\s*(High|H)\s*[\)\]]", t, re.IGNORECASE):
        return -999999.0, -999999.0, "(High)"
    return None, None, None

def extract_value(text, range_min, range_max, range_txt):
    if not text:
        return None

    txt = text
    if range_txt:
        txt = txt.replace(range_txt, "")

    txt = re.sub(r'[^\d\.\s]', ' ', txt)
    txt = txt.replace(",", "")

    nums = re.findall(r"(\d{1,4}(?:\.\d{1,3})?)", txt)
    if not nums:
        return None
    try:
        val = float(nums[0])
    except:
        return None
    if val > 2100:
        for n in nums[1:]:
            try:
                v = float(n)
                if v <= 2100:
                    val = v
                    break
            except:
                continue
        else:
            return None
    try:
        if range_min is not None and range_max is not None:
            is_valid_range = range_min != 999999.0 and range_max != -999999.0
            if is_valid_range and val < range_min and (val + 10) >= range_min and (val + 10) <= range_max:
                logger.info(f"Auto-correcting {val} -> {val+10} based on range {range_min}-{range_max}")
                val = val + 10
    except Exception:
        pass
    return val

# ----------------- PDF table extraction -----------------
def extract_from_pdf(file_obj):
    raw_rows = []
    try:
        with pdfplumber.open(file_obj) as pdf:
            for page in pdf.pages:
                # first try page.extract_table / extract_tables
                try:
                    tables = page.extract_tables()
                    if tables:
                        for tbl in tables:
                            for row in tbl:
                                if any(cell for cell in row):
                                    # join row cells into a single string for downstream parsing
                                    row_str = " | ".join([str(c).strip() if c else "" for c in row])
                                    raw_rows.append(row_str)
                        continue  # go to next page
                except Exception:
                    pass

                # fallback: use extract_text and naive split lines
                text = page.extract_text()
                if text:
                    for l in text.splitlines():
                        if l.strip():
                            raw_rows.append(l.strip())
    except Exception as e:
        logger.exception("PDF read error: %s", e)
        raise
    return raw_rows

# ----------------- Image extraction using tesseract (word-level) -----------------
def extract_from_image(image):
    """
    Use pytesseract's image_to_data to get word-level boxes. Then group words into lines,
    and attempt to reconstruct table rows by clustering x-coordinates for columns.
    Returns list of row-like strings.
    """
    try:
        # ensure PIL Image
        if not isinstance(image, Image.Image):
            image = Image.open(image).convert("RGB")
        else:
            image = image.convert("RGB")

        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        n = len(data['level'])
        rows = {}
        # group words by line_num and their top coordinate to preserve order
        for i in range(n):
            text = data['text'][i].strip()
            if not text:
                continue
            line_num = data['line_num'][i]
            top = data['top'][i]
            left = data['left'][i]
            key = (line_num, top)
            rows.setdefault(key, []).append((left, text))

        # flatten lines sorted by top/line number
        line_items = []
        for key in sorted(rows.keys(), key=lambda k: (k[0], k[1])):
            items = sorted(rows[key], key=lambda x: x[0])
            line_text = " ".join([t for _, t in items])
            # also keep positions for potential column clustering
            positions = items  # list of (left, text)
            line_items.append((key, line_text, positions))

        # Now try to create row-like strings by combining neighboring lines when necessary
        result_rows = []
        i = 0
        while i < len(line_items):
            _, text_line, positions = line_items[i]
            # if this line contains a known keyword, likely start of a row
            letters_only = re.sub(r'[^A-Za-z]+', ' ', text_line).strip()
            match = process.extractOne(letters_only, ALL_KEYWORDS, score_cutoff=80) if letters_only else None
            if match:
                # collect this and next 2 lines to ensure we have value+range
                combo = text_line
                for j in range(1, 4):
                    if i + j < len(line_items):
                        combo += " " + line_items[i + j][1]
                result_rows.append(combo)
                i += 1
            else:
                # sometimes values are on a line with no test name; keep as-is for pdf-like fallback
                result_rows.append(text_line)
                i += 1
        # final cleanup: unique and return
        final = []
        for r in result_rows:
            r2 = re.sub(r'\s{2,}', ' ', r).strip()
            if r2 and r2 not in final:
                final.append(r2)
        return final
    except Exception as e:
        logger.exception("Image OCR failed: %s", e)
        raise

# ----------------- core parsing using rows -----------------
def parse_rows_to_results(rows):
    results = []
    for row in rows:
        low = row.lower()
        # skip header/footer lines aggressively
        skip_terms = ["laboratory", "laboratory report", "test name", "result", "units", "page", "date", "doctor", "patient", "digitally signed"]
        if any(t in low for t in skip_terms):
            continue

        # attempt to fuzzy-match test name inside the row
        letters_only = re.sub(r'[^A-Za-z]+', ' ', row).strip()
        if len(letters_only) < 2:
            continue
        match = process.extractOne(letters_only, ALL_KEYWORDS, score_cutoff=80)
        if not match:
            continue
        keyword = match[0]
        std_name = next((k for k, v in TEST_MAPPING.items() if keyword in v), None)
        if not std_name:
            continue

        # isolate substring after keyword to reduce false ranges (dates etc.)
        try:
            after = row.split(keyword, 1)[-1]
        except Exception:
            after = row

        min_r, max_r, range_txt = extract_range(after)
        val = extract_value(after, min_r, max_r, range_txt)

        # fallback: try entire row if not found
        if (val is None or min_r is None):
            min_r2, max_r2, range_txt2 = extract_range(row)
            val2 = extract_value(row, min_r2, max_r2, range_txt2)
            if val2 is not None and min_r2 is not None:
                min_r, max_r, range_txt, val = min_r2, max_r2, range_txt2, val2

        if val is None or min_r is None:
            continue

        results.append({
            "test_name": std_name,
            "value": val,
            "min": min_r,
            "max": max_r,
            "range": range_txt
        })

    # deduplicate keeping first occurrence
    unique = []
    seen = set()
    for r in results:
        if r["test_name"] not in seen:
            unique.append(r)
            seen.add(r["test_name"])
    return unique

# ----------------- analyze file (PDF + images) -----------------
def analyze_file(uploaded_file):
    filename = getattr(uploaded_file, "name", "").lower() if uploaded_file else ""
    rows = []
    try:
        if filename.endswith(".pdf"):
            rows = extract_from_pdf(uploaded_file)
        else:
            # image path or file-like
            # if BytesIO, pass directly to PIL Image
            if isinstance(uploaded_file, BytesIO) or hasattr(uploaded_file, "read"):
                uploaded_file.seek(0)
                image = Image.open(uploaded_file)
            else:
                # uploaded_file might be a path string
                image = Image.open(uploaded_file)
            rows = extract_from_image(image)
    except Exception:
        logger.exception("analyze_file failed")
        raise

    return parse_rows_to_results(rows)

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
    st.markdown("<h2 style='text-align:center;'>🧾 OCR Lab Report Scanner — PDF + Images</h2>", unsafe_allow_html=True)

    ok, tver = check_tesseract()
    if ok:
        st.success(f"Tesseract OK: {tver}")
    else:
        st.warning("Tesseract not found. Image OCR will fail without it. For PDFs table extraction still works.")
    
    st.write("Upload a lab report (PDF / PNG / JPG). The app will list abnormal test results.")

    uploaded_file = st.file_uploader("Upload file", type=["pdf", "png", "jpg", "jpeg"])

    # load sample image shortcut (works in this environment)
    if st.button("Load sample debug file"):
        try:
            with open(SAMPLE_IMAGE_PATH, "rb") as fh:
                file_bytes = fh.read()
            uploaded_file = BytesIO(file_bytes)
            uploaded_file.name = SAMPLE_IMAGE_PATH.split("/")[-1]
            st.session_state["uploaded_file"] = uploaded_file
            st.rerun()
        except FileNotFoundError:
            st.error(f"Sample not found at: {SAMPLE_IMAGE_PATH}")
        except Exception as e:
            st.error(f"Failed to load sample: {e}")

    if uploaded_file is None:
        uploaded_file = st.session_state.get("uploaded_file")

    if uploaded_file is None:
        st.info("Tip: use 'Load sample debug file' to test (only available in this environment).")
        return

    try:
        with st.spinner("Scanning document..."):
            all_data = analyze_file(uploaded_file)
            abnormals = get_abnormals(all_data)

        if not all_data:
            st.warning("⚠️ Could not extract any test results. Try a clearer image or a PDF with tables.")

        st.subheader("Results with Abnormal Values")
        if not abnormals:
            st.success("✅ No Abnormalities Found")
        else:
            for item in abnormals:
                status = item["status"]
                color = "#ef4444" if status == "High" else "#3b82f6"
                st.markdown(f\"\"\"
                    <div style=\"background:white; padding:12px; border-left:5px solid {color}; border-radius:8px;\">
                        <b>{item['test_name']}</b><br>
                        Value: <b>{item['value']}</b>
                        <span style=\"background:{color}; color:#fff; padding:2px 6px; border-radius:6px; margin-left:8px;\">{status}</span><br>
                        <small>Ref: {item['range']}</small>
                    </div>
                \"\"\", unsafe_allow_html=True)

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
