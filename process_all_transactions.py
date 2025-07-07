import os
import json
import pandas as pd

from bank_extractors import (
    extract_dkb_kontoauszug,
    extract_n26_statement,
    extract_db_statement,
    extract_barclays_excel,
)

# --- Helper: Robust bank detection (filename + content) ---
def detect_bank(filename, lines=None):
    """Detect bank type from filename and content."""
    name = filename.lower()

    # Excel files (Barclays)
    if name.endswith(".xlsx"):
        if "barclays" in name:
            return "BARCLAYS"
        try:
            df = pd.read_excel(filename, header=None, nrows=10)
            for val in df[0].astype(str):
                if "barclays" in val.lower():
                    return "BARCLAYS"
            if df.shape[1] > 1:
                for val in df[1].astype(str):
                    if "barclays" in val.lower():
                        return "BARCLAYS"
        except Exception:
            pass
        return "UNKNOWN"

    # PDF files
    if name.endswith(".pdf"):
        # Filename-based detection
        if "dkb" in name or "kontoauszug" in name:
            return "DKB"
        if "n26" in name:
            return "N26"
        if "deutsche" in name or "db" in name or "account_statement" in name:
            return "DB"
        # Content-based detection
        if lines:
            joined = " ".join(lines).lower()
            if "deutsche kreditbank" in joined or "dkb" in joined:
                return "DKB"
            if ("iban: de15" in joined and "ntsbdeb1xxx" in joined) or \
               ("pavatharini muthukkumar" in joined and "kontoauszug" in joined):
                return "N26"
            if "deutsche bank" in joined:
                return "DB"
    return "UNKNOWN"

TRANSACTIONS_FOLDER = 'transactions'
PROCESSED_FILE = 'processed_files.json'

def load_processed_files():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_processed_files(processed_files):
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(sorted(list(processed_files)), f, indent=2)

def process_new_transactions():
    processed_files = load_processed_files()
    files = os.listdir(TRANSACTIONS_FOLDER)
    for fname in files:
        full_path = os.path.join(TRANSACTIONS_FOLDER, fname)
        if fname in processed_files or not os.path.isfile(full_path):
            continue

        # --- Read file content for robust detection ---
        lines = None
        if fname.lower().endswith('.pdf'):
            try:
                import pdfplumber
                lines = []
                with pdfplumber.open(full_path) as pdf:
                    for page in pdf.pages[:2]:  # Just first 2 pages needed
                        text = page.extract_text()
                        if text:
                            lines.extend(text.split('\n'))
            except Exception as e:
                print(f"[WARN] Could not read PDF: {fname} ({e})")
        # For Excel, detect_bank already handles reading file

        # --- Detect bank ---
        bank = detect_bank(full_path, lines)
        print(f"\nProcessing: {fname} | Detected bank: {bank}")

        try:
            if bank == 'DKB':
                df = extract_dkb_kontoauszug(full_path)
            elif bank == 'N26':
                df = extract_n26_statement(full_path)
            elif bank == 'DB':
                df = extract_db_statement(full_path)
            elif bank == 'BARCLAYS':
                df = extract_barclays_excel(full_path)
            else:
                print(f"❓ Could not detect bank for file: {fname}. Skipping.")
                continue

            # Save as CSV and Excel for checking
            safe_bank = bank if bank != "UNKNOWN" else "UNDETECTED"
            base_name = fname.rsplit('.', 1)[0]
            out_csv = f"extracted_{safe_bank}_{base_name}.csv"
            out_excel = f"extracted_{safe_bank}_{base_name}.xlsx"
            df.to_csv(os.path.join('extracted_transactions', out_csv), index=False)
            df.to_excel(os.path.join('extracted_transactions', out_excel), index=False)

            print(f"✅ Processed {fname} and saved to extracted_transactions folder.")
            processed_files.add(fname)
            save_processed_files(processed_files)

        except Exception as e:
            print(f"❌ Error processing {fname}: {e}")

# --- Make sure output folder exists ---
os.makedirs('extracted_transactions', exist_ok=True)

# --- Run main dispatcher ---
process_new_transactions()
