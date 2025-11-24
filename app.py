import re
import logging
import numpy as np
import pdfplumber
import easyocr
from PIL import Image
from thefuzz import process
import streamlit as st

# ---------------- CONFIGURATION ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="OCR", page_icon="🚨", layout="centered")

@st.cache_resource
def load_ocr_reader():
    return easyocr.Reader(['en'], gpu=False)

reader = load_ocr_reader()

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

# ---------------- 2. INTELLIGENT EXTRACTORS ----------------
def extract_range(text):
    """ Finds range like '10-20', '<50', or explicit markers like '(Low)' """
    if not text:
        return None, None, None

    text = re.sub(r'\s+', ' ', text).strip()

    # Guard for address patterns like "Delhi-110002"
    if re.search(r'\b1100\d{2}\b', text):
        return None, None, None

    # Pattern A: "10.5 - 20.5"
    dash_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:[%/a-zA-Z]{0,5}\s*)?[-–to]\s*(\d+(?:\.\d+)?)",
        text
    )

    if dash_match:
        min_v = float(dash_match.group(1))
        max_v = float(dash_match.group(2))
        if max_v > 50000:
            return None, None, None
        return min_v, max_v, dash_match.group(0)

    # Pattern B: "< 5.0"
    less = re.search(r"(?:<|less than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if less:
        return 0.0, float(less.group(1)), less.group(0)

    # Pattern C: "> 5.0"
    more = re.search(r"(?:>|more than)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if more:
        return float(more.group(1)), 999999.0, more.group(0)

    # Pattern D: "(Low)" / "(L)"
    if re.search(r"[\(\[]\s*(?:Low|L)\s*[\)\]]", text, re.IGNORECASE):
        return 999999.0, 999999.0, "(Low)"

    # Pattern E: "(High)" / "(H)"
    if re.search(r"[\(\[]\s*(?:High|H)\s*[\)\]]", text, re.IGNORECASE):
        return -999999.0, -999999.0, "(High)"

    return None, None, None


def extract_value(text_source, range_str):
    """ Finds the test result value, ignoring dates/IDs """
    if not text_source:
        return None

    clean = text_source
    if range_str:
        clean = text_source.replace(range_str, "")

    clean = clean.replace(",", "")
    nums = re.findall(r"(\d+(?:\.\d+)?)", clean)

    valid_nums = []
    for n in nums:
        try:
            f = float(n)
            # Ignore years and zip-codes
            if f < 2000 or (f > 2100 and f < 10000):
                valid_nums.append(f)
        except Exception:
            continue

    if not valid_nums:
        return None
    return valid_nums[0]

# ---------------- 3. MULTI-LINE PARSER ----------------
def parse_text_block(full_text):
    results = []
    lines = full_text.split("\n")

    lines = [l.strip() for l in lines if len(l.strip()) > 3]

    i = 0
    while i < len(lines):
        line = lines[i]

        ignore_terms = [
            "Test Name", "Result", "Unit", "Reference",
            "Page", "Date", "Time", "Remark", "Method"
        ]
        if any(x.lower() in line.lower() for x in ignore_terms):
            i += 1
            continue

        text_for_matching = re.sub(r'[\d\W_]+', ' ', line)
        keyword = None

        # Exact short-code matches
        for safe in ["Hb", "PCV", "TLC", "RBC", "MCV", "MCH", "MCHC", "RDW", "TSH"]:
            if re.search(r'\b' + re.escape(safe) + r'\b', line, re.IGNORECASE):
                keyword = safe
                break

        # Fuzzy match if still not found
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

# ---------------- 4. FILE PROCESSING ----------------
def analyze_file(uploaded_file):
    raw_text = ""
    filename = uploaded_file.name.lower()

    try:
        if filename.endswith(".pdf"):
            # pdfplumber can open file-like object
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text()
                    if not txt:
                        continue
                    if "no test results" in txt.lower():
                        continue
                    if not re.search(r'\d', txt):
                        continue

                    raw_text += "\n" + txt
                    tables = page.extract_tables()
                    for tb in tables:
                        for row in tb:
                            raw_str = " ".join([str(c) for c in row if c])
                            raw_text += "\n" + raw_str
        else:
            # Image workflow
            image = Image.open(uploaded_file).convert("RGB")
            ocr_list = reader.readtext(np.array(image), detail=0, paragraph=False)
            raw_text = "\n".join(ocr_list)
    except Exception as e:
        logger.error(f"Error reading file: {e}")
        return []

    # Biopsy / narrative filter
    lower_raw = raw_text.lower()
    biopsy_markers = [
        "biopsy", "histopathology", "specimen examined",
        "microscopic examination", "impression:",
        "clinical history", "department of pathology", "cytology"
    ]

    match_count = sum(1 for m in biopsy_markers if m in lower_raw)
    if match_count >= 1:
        digit_count = sum(c.isdigit() for c in raw_text)
        if len(raw_text) > 0 and (digit_count / len(raw_text)) < 0.05:
            return []

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

# ---------------- 5. STREAMLIT UI ----------------
def main():
    st.markdown(
        "<h2 style='text-align:center;'>🚨 Abnormality Scanner</h2>",
        unsafe_allow_html=True
    )
    st.write("")
    st.write("Upload a **lab report (PDF / image)** and I’ll highlight only the **abnormal tests**.")

    uploaded_file = st.file_uploader(
        "Upload Report",
        type=["pdf", "png", "jpg", "jpeg"],
        label_visibility="collapsed"
    )

    if uploaded_file is not None:
        with st.spinner("Scanning document... this may take a moment ⏳"):
            all_data = analyze_file(uploaded_file)
            abnormals = get_abnormals(all_data)

        if not abnormals:
            st.markdown(
                "<div style='text-align:center; color:green; margin-top:20px; font-weight:bold;'>"
                "✅ No Abnormalities Found (All Normal)"
                "</div>",
                unsafe_allow_html=True
            )
        else:
            for item in abnormals:
                status = item["status"]
                border_color = "#3b82f6" if status == "Low" else "#ef4444"
                badge_bg = border_color

                card_html = f"""
                <div style="
                    background:white;
                    padding:16px;
                    margin-top:12px;
                    border-radius:8px;
                    display:flex;
                    justify-content:space-between;
                    align-items:center;
                    box-shadow:0 1px 3px rgba(0,0,0,0.1);
                    border-left:5px solid {border_color};
                ">
                    <div>
                        <div style="font-weight:bold">{item['test_name']}</div>
                        <div style="font-size:12px; color:#71717a">Ref: {item['range']}</div>
                    </div>
                    <div style="font-size:20px; font-weight:bold;">
                        {item['value']}
                        <span style="
                            font-size:12px;
                            padding:3px 8px;
                            border-radius:12px;
                            color:white;
                            margin-left:8px;
                            vertical-align:middle;
                            background:{badge_bg};
                        ">{status}</span>
                    </div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)


if __name__ == "__main__":
    main()

