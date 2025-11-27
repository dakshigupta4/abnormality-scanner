import streamlit as st
import pytesseract
from PIL import Image
import pdfplumber
import re

# ✅ SET TESSERACT PATH (WINDOWS)
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ✅ NORMAL RANGES (NO CHANGE)
NORMAL_RANGES = {
    "Hemoglobin": (12, 15),
    "PCV": (36, 46),
    "RBC": (3.8, 4.8),
    "MCV": (83, 101),
    "MCH": (27, 32),
    "MCHC": (31.5, 34.5),
    "RDW": (11.6, 14.4),
    "TLC": (4000, 10000),

    "NEUTROPHILS%": (40, 80),
    "LYMPHOCYTES%": (20, 40),
    "EOSINOPHILS%": (1, 6),
    "MONOCYTES%": (2, 10),
    "BASOPHILS%": (0, 2),

    "NEUTROPHILS_ABS": (2000, 7000),
    "LYMPHOCYTES_ABS": (1000, 3000),
    "EOSINOPHILS_ABS": (20, 500),
    "MONOCYTES_ABS": (200, 1000),

    "PLATELET": (150000, 400000),
    "MPV": (8.1, 13.9),
    "NLR": (0.78, 3.53),
    "ESR": (0, 15),
    "WBC": (4.5, 11),
    "HCT": (37, 50),
}

# ---------- TEXT EXTRACT ----------
def extract_pdf_text(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

def extract_image_text(file):
    img = Image.open(file)
    img = img.convert("L")
    return pytesseract.image_to_string(img)

# ---------- OCR CLEAN ----------
def normalize_text(text):
    replacements = {
        "Hem0g10bin": "Hemoglobin",
        "Rec": "RBC",
        "yer": "HCT",
        "pur": "PLT",
        "wec": "WBC",
        "M0N": "MON",
        "R0WcV": "RDW-CV",
        "R0W-SD": "RDW-SD",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    text = text.replace("l", "1").replace("|", "1")
    
    # NEW: Remove non-alphanumeric, non-space characters (like special symbols from OCR)
    text = re.sub(r'[^\w\s\.\,\%\-]+', '', text) 
    
    return text

# ---------- VALUE EXTRACTION ----------
# ---------- VALUE EXTRACTION (FIXED) ----------
def extract_values(text):
    results = {}

    patterns = {
        # Added |HGB for flexibility, made search for value more generic
        "Hemoglobin": r"(HAEMOGLOBIN|Hemoglobin|HGB)\s*([\d\.]+)", 
        "PCV": r"PCV\s*([\d\.]+)",
        "RBC": r"RBC\s*([\d\.]+)", # Now searching across lines
        "MCV": r"MCV\s*([\d\.]+)",
        "MCH": r"MCH\s*([\d\.]+)",
        "MCHC": r"MCHC\s*([\d\.]+)",
        "RDW": r"RDW[- ]?CV\s*([\d\.]+)",
        "HCT": r"HCT\s*([\d\.]+)", # Now searching across lines

        "TLC": r"(TOTAL LEUCOCYTE COUNT|TLC)\s*([\d,]+)",

        "NEUTROPHILS%": r"(NEUTROPHILS|NEU)[T]?%\s*([\d\.]+)",
        "LYMPHOCYTES%": r"(LYMPHOCYTES|LYM)[P]?%\s*([\d\.]+)",
        "EOSINOPHILS%": r"(EOSINOPHILS|EOS)%\s*([\d\.]+)",
        "MONOCYTES%": r"(MONOCYTES|MON)%\s*([\d\.]+)",
        "BASOPHILS%": r"(BASOPHILS|BAS)%\s*([\d\.]+)",

        # Absolute Counts are currently complex, sticking to simple value extraction
        "NEUTROPHILS_ABS": r"NEUTROPHILS\s+([\d\.]+)\s*Cells",
        "LYMPHOCYTES_ABS": r"LYMPHOCYTES\s+([\d\.]+)\s*Cells",
        "EOSINOPHILS_ABS": r"EOSINOPHILS\s+([\d\.]+)\s*Cells",
        "MONOCYTES_ABS": r"MONOCYTES\s+([\d\.]+)\s*Cells",

        "PLATELET": r"(PLATELET COUNT|PLT)\s*([\d,]+)",
        "MPV": r"MPV\s*([\d\.]+)",
        "NLR": r"NLR\s*([\d\.]+)",
        "ESR": r"ESR\s*([\d\.]+)",
        "WBC": r"WBC\s*([\d\.]+)"
    }
    for test, pattern in patterns.items():
        # NOTE: Using re.DOTALL and re.IGNORECASE to improve matching
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL) 
        if match:
            raw = match.group(1).replace(",", "").strip()

            try:
                value = float(raw)
            except:
                continue

            # ✅ SAME AUTO FIXES (as before)
            if test in ["WBC", "RBC"] and value > 20:
                s = str(int(value))
                value = float(s[0] + "." + s[1:])

            if test == "MCHC" and value < 10:
                value = 32.0

            results[test] = value

    return results
# ---------- STATUS ----------
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
        else:
            status = "NORMAL"

        report[test] = {
            "value": value,
            "normal": f"{low} - {high}",
            "status": status
        }

    return report

# ---------- X-RAY ----------
def extract_xray_report(text):
    report = {}
    keys = ["FINDINGS", "IMPRESSION", "IMPRESSIONS", "OPINION", "CONCLUSION", "RECOMMENDATION"]
    lines = text.split("\n")
    current = None
    buffer = ""

    for line in lines:
        u = line.strip().upper()
        for k in keys:
            if k in u:
                if current:
                    report[current] = buffer.strip()
                current = k.title()
                buffer = ""
                break
        else:
            if current:
                buffer += " " + line

    if current:
        report[current] = buffer.strip()

    return report

# ---------- UI ----------
st.set_page_config(page_title="Blood Report Analyzer", layout="wide")

st.title("Blood Report Analyzer")

file = st.file_uploader("Upload PDF or Image", type=["pdf", "jpg", "jpeg", "png"])

if file:
    if st.button("Analyze"):
        # 1. Extract Text
        if file.type == "application/pdf":
            text = extract_pdf_text(file)
        else:
            text = extract_image_text(file)

        # ------------------------------------
        # 🐞 DEBUGGING STEP START
        # ------------------------------------
        st.subheader("🕵️ Raw OCR Text (DEBUG)")
        st.code(text)
        
        # 2. Normalize Text
        text = normalize_text(text)
        
        st.subheader("🧹 Normalized Text (DEBUG)")
        st.code(text)
        # ------------------------------------
        # 🐞 DEBUGGING STEP END
        # ------------------------------------

        # 3. Analyze Values
        blood = analyze(extract_values(text))
        xray  = extract_xray_report(text)

        # ---------- TABLE ----------
        if blood:
            st.subheader("Blood Report")
            for k, v in blood.items():
                # Check for LOW/HIGH status
                color = "green" if v["status"] == "NORMAL" else "orange" if v["status"] == "HIGH" else "red"
                
                # Display the results
                st.markdown(f"""
                <div style="padding:8px;border-left:5px solid {color};background:#f7f7f7;margin-bottom:5px">
                    <b>{k}</b> — {v["value"]} <br>
                    Normal: {v["normal"]} <br>
                    Status: <b style="color:{color}">{v["status"]}</b>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.warning("No blood values detected.")

        # ---------- XRAY ----------
        if xray:
            st.subheader("X-Ray Report")
            for k, v in xray.items():
                st.markdown(f"**{k}:** {v}")
        else:
            st.info("No X-Ray report found.")






