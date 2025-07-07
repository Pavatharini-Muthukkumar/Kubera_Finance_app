# Start pasting your extractor functions here
import re
import pdfplumber
import pandas as pd
from datetime import datetime
import json
import os

# ===== Shared balance helpers (define ONCE at the top of bank_extractors.py) =====

BALANCE_FILE = "last_balance.json"
BALANCE_BACKUP_FILE = "last_balance_backup.json"

def load_balances():
    if os.path.exists(BALANCE_FILE):
        with open(BALANCE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_balances(balances):
    with open(BALANCE_FILE, "w") as f:
        json.dump(balances, f, indent=2)
    with open(BALANCE_BACKUP_FILE, "w") as f:
        json.dump(balances, f, indent=2)

def update_balance_for_account(account_id, latest_balance):
    balances = load_balances()
    balances[account_id] = latest_balance
    save_balances(balances)
    print(f"Updated balance for {account_id}: {latest_balance}")

# ===== DKB Extractor Function =====

def extract_dkb_kontoauszug(pdf_path):
    def detect_reference_account(lines):
        for line in lines[:50]:
            line_nospace = re.sub(r"\s+", "", line)
            m = re.search(r'(DE\d{20})', line_nospace)
            if m:
                print(f"[DEBUG] Detected Reference Account (no spaces): {m.group(1)}")
                return m.group(1)
        header = re.sub(r"\s+", "", "\n".join(lines[:50]))
        m = re.search(r'(DE\d{20})', header)
        if m:
            print(f"[DEBUG] Detected Reference Account in header: {m.group(1)}")
            return m.group(1)
        print("[DEBUG] No Reference Account IBAN found!")
        return "Unknown"

    def find_ibans(text, own_iban=None):
        text_nospace = re.sub(r"\s+", "", text)
        all_ibans = re.findall(r'DE\d{20}', text_nospace)
        ibans = [iban for iban in all_ibans if own_iban is None or iban != own_iban]
        return ibans

    def extract_payee_and_iban(block_text, amount, own_iban=None):
        amt_regex = r'[-+]?\d{1,3}(?:\.\d{3})*,\d{2}'
        amt_match = re.search(amt_regex, block_text)
        payee = ""
        if amt_match:
            end = amt_match.end()
            after_amt = block_text[end:].strip()
            stop_words = ['Kd.', 'Kunden', 'RG-N', 'Rechnung', 'Gläubiger-ID:', 'KM-', 'EUR', 'r.F', 'IBAN']
            after_amt_words = after_amt.split()
            payee_words = []
            for word in after_amt_words:
                if any(word.startswith(stop) for stop in stop_words):
                    break
                if re.match(r'\d{5,}', word):
                    break
                payee_words.append(word)
            payee = " ".join(payee_words).strip()
            if not payee:
                for word in after_amt_words:
                    if not any(word.startswith(stop) for stop in stop_words):
                        payee = word
                        break
        ibans = find_ibans(block_text, own_iban)
        counterparty_iban = ibans[-1] if ibans else ""
        print(f"[DEBUG] In block: {block_text[:70]}... Payee: '{payee}' | IBANs found: {ibans}")
        return payee, counterparty_iban

    def extract_amount_from_first_line(line):
        matches = list(re.finditer(r'-?\d{1,3}(?:\.\d{3})*,\d{2}', line))
        if matches:
            amt_str = matches[-1].group(0)
            amt_str = amt_str.replace('.', '').replace(',', '.')
            return float(amt_str)
        return 0.0

    with pdfplumber.open(pdf_path) as pdf:
        lines = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines.extend(text.split('\n'))

    reference_account = detect_reference_account(lines)
    reference_account_name = "MY DKB"

    balance_found = None
    for line in reversed(lines):
        if "Kontostand am" in line:
            m = re.search(r"([\d\.,]+)\s*(EUR)?\s*$", line)
            if m:
                balance_str = m.group(1)
                if ',' in balance_str and '.' in balance_str:
                    balance_str = balance_str.replace('.', '').replace(',', '.')
                elif ',' in balance_str:
                    balance_str = balance_str.replace('.', '').replace(',', '.')
                else:
                    balance_str = balance_str.replace(',', '')
                try:
                    balance_found = float(balance_str)
                except Exception:
                    balance_found = None
            break

    tx_date_pattern = re.compile(r"^(\d{2}\.\d{2}\.\d{4})")
    transactions = []
    blocks = []
    block = []
    for line in lines:
        if tx_date_pattern.match(line):
            if block:
                blocks.append(block)
                block = []
            block = [line]
        elif block:
            block.append(line)
    if block:
        blocks.append(block)

    idx = 1
    for i, block in enumerate(blocks):
        line0 = block[0]
        booking_date = datetime.strptime(tx_date_pattern.match(line0).group(1), "%d.%m.%Y").strftime("%Y-%m-%d")
        amount = extract_amount_from_first_line(line0)
        explanation_lines = [line0[11:].strip()] + [ln.strip() for ln in block[1:]]
        block_text = " ".join(explanation_lines)
        payee, counterparty_iban = extract_payee_and_iban(block_text, amount, own_iban=reference_account)
        tx_type = "Other"
        expl_lower = block_text.lower()
        if "geldautomat" in expl_lower:
            tx_type = "Cash Withdrawal"
        elif "dauerauftrag" in expl_lower:
            tx_type = "Standing Order"
        elif "überweisung" in expl_lower:
            tx_type = "Bank Transfer"
        elif "basislastschrift" in expl_lower or "lastschrift" in expl_lower:
            tx_type = "SEPA Direct Debit"
        elif "zins" in expl_lower or "gebühr" in expl_lower or "interest" in expl_lower:
            tx_type = "Interest/Fee"
        elif "karte" in expl_lower or "kartenzahlung" in expl_lower or "debitk" in expl_lower:
            tx_type = "Card Payment"
        elif "lohn" in expl_lower or "gehalt" in expl_lower or "rente" in expl_lower:
            tx_type = "Bank Transfer"
        analyzed_amount = "Income" if amount > 0 else "Expenses"
        transactions.append({
            "idx": idx,
            "Booking Date": booking_date + " 00:00:00",
            "Reference Account": reference_account,
            "Reference Account Name": reference_account_name,
            "Amount (€)": amount,
            "Balance (€)": None,
            "Currency": "EUR",
            "Payee": payee,
            "IBAN": counterparty_iban,
            "Purpose": block_text,
            "E-Reference": None,
            "Mandate Reference": "",
            "Creditor ID": "",
            "Main Category": "",
            "Subcategory": "",
            "Contract": False,
            "Contract Frequency": "Unknown",
            "Contract ID": "",
            "Internal Transfer": "No",
            "Excluded from Disposable Income": False,
            "Transaction Type": tx_type,
            "Analyzed Amount": analyzed_amount,
            "Week": str(datetime.strptime(booking_date, "%Y-%m-%d").isocalendar()[1]),
            "Month": booking_date[:7],
            "Quarter": str((int(booking_date[5:7])-1)//3 + 1),
            "Year": int(booking_date[:4]),
            "Tags": "",
            "Note": "",
            "text": block_text,
            "payer": payee if amount > 0 else "",
            "needs_manual_input": False
        })
        idx += 1

    df = pd.DataFrame(transactions)
    if balance_found is not None:
        df = df.sort_values("Booking Date")
        amounts = df["Amount (€)"].values[::-1]
        running_bal = [balance_found]
        for a in amounts[:-1]:
            running_bal.append(running_bal[-1] - a)
        df["Balance (€)"] = running_bal[::-1]
    df.to_csv("dkb_kontoauszug_transactions_final.csv", index=False)
    print(df.head(10))
    print(f"Extracted {len(df)} transactions and saved as dkb_kontoauszug_transactions_final.csv")

    if balance_found is not None:
        update_balance_for_account(reference_account, balance_found)

    return df



def extract_n26_statement(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        lines = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines.extend(text.split('\n'))

    # Your own IBAN for Reference Account (from bottom or header)
    ref_iban = None
    for line in lines:
        m = re.search(r'IBAN:\s*(DE\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{2})', line)
        if m and ("NTSBDEB1XXX" in line or "PAVATHARINI MUTHUKKUMAR" in lines):
            ref_iban = m.group(1).replace(" ", "")
            break
    if not ref_iban:
        # Fallback: grab last DE.... IBAN in file
        for line in reversed(lines):
            m = re.search(r'(DE\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{2})', line)
            if m:
                ref_iban = m.group(1).replace(" ", "")
                break
    ref_account_name = "Pavi"

    transactions = []
    for i, line in enumerate(lines):
        # Transaction lines look like: 'PAYEE 14.05.2025 +250,00€'
        m = re.match(r'(.+?)\s+(\d{2}\.\d{2}\.\d{4})\s+([+-]?\d{1,3}(?:\.\d{3})*,\d{2})€', line)
        if m:
            payee, date, amt_str = m.groups()
            amount = float(amt_str.replace('.', '').replace(',', '.'))
            # Look ahead for IBAN (usually next 3 lines)
            counterparty_iban = ''
            for j in range(i+1, min(i+5, len(lines))):
                m2 = re.search(r'IBAN:\s*(DE\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{2})', lines[j])
                if m2:
                    counterparty_iban = m2.group(1).replace(" ", "")
                    break
            transactions.append({
                "Booking Date": datetime.strptime(date, "%d.%m.%Y").strftime("%Y-%m-%d") + " 00:00:00",
                "Reference Account": ref_iban,
                "Reference Account Name": ref_account_name,
                "Amount (€)": amount,
                "Currency": "EUR",
                "Payee": payee.strip(),
                "IBAN": counterparty_iban,
            })

    df = pd.DataFrame(transactions)
    print(df)
    balance_found = None
    for line in lines:
        m = re.search(r'Dein neuer Kontostand\s*([+-]?\d{1,3}(?:\.\d{3})*,\d{2})€', line)
        if m:
            balance_found = float(m.group(1).replace('.', '').replace(',', '.'))
            break

    df.to_csv("n26_statement_extracted.csv", index=False)
    print(f"Extracted {len(df)} transactions and saved as n26_statement_extracted.csv")

    # --- Update balance using your function if found ---
    if balance_found is not None and ref_iban:
        update_balance_for_account(ref_iban, balance_found)

    return df


def extract_db_statement(pdf_path):
    from datetime import datetime

    with pdfplumber.open(pdf_path) as pdf:
        lines = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines.extend(text.split('\n'))

    # Reference Account IBAN (from header)
    ref_iban = None
    for line in lines:
        m = re.search(r'(DE\d{20})', line.replace(" ", ""))
        if m:
            ref_iban = m.group(1)
            break
    ref_account_name = "MY DB"

    # --- Extract balance (look for 'EUR +...' pattern at end of file) ---
    balance_found = None
    for line in reversed(lines):
        m = re.match(r'^EUR\s*([+-]?[\d\.,]+)$', line.strip())
        if m:
            balance_str = m.group(1).replace(',', '')  # Remove comma as thousands separator only

            try:
                balance_found = float(balance_str)
                print(f"[DEBUG] Found balance: {balance_found}")
                break
            except:
                continue

    # --- Transactions extraction ---
    transactions = []
    for i in range(len(lines)-1):
        clean_line = re.sub(r'\s+', '', lines[i])
        # Only match lines that look like transactions (end with .dd and have SEPA)
        if re.search(r'\.\d{2}$', clean_line) and "SEPA" in clean_line:
            amt_m = re.search(r'([+-]?\d+(?:\.\d{3})*\.\d{2})$', clean_line)
            if not amt_m:
                amt_m = re.search(r'(\d+(?:\.\d{3})*\.\d{2})$', clean_line)
            amt_str = amt_m.group(1) if amt_m else ""
            amount = float(amt_str) if amt_str else 0.0

            # Get value date (second date group) from original line
            date_match = re.match(r'.*?(\d{2}-\d{2}-)\s*(\d{2}-\d{2}-)', lines[i])
            dd_mm = date_match.group(2) if date_match else ""
            payee_line = lines[i+1]
            m_year_payee = re.match(r'(\d{4})\s+\d{4}\s+(.+)', payee_line)
            if dd_mm and m_year_payee:
                year = m_year_payee.group(1)
                booking_date = f"{year}-{dd_mm[:2]}-{dd_mm[3:5]}"
                payee = m_year_payee.group(2).strip()
            else:
                booking_date = ""
                payee = payee_line.strip()

            # Counterparty IBAN (not your own) in next 5 lines
            counterparty_iban = ""
            for j in range(i+2, min(i+7, len(lines))):
                m_iban = re.search(r'(DE\d{20})', lines[j].replace(" ", ""))
                if m_iban and m_iban.group(1) != ref_iban:
                    counterparty_iban = m_iban.group(1)
                    break

            desc = lines[i]
            if "Dauerauftrag" in desc:
                tx_type = "Standing Order"
            elif "Lastschrifteinzug" in desc:
                tx_type = "SEPA Direct Debit"
            elif "Überweisung" in desc:
                tx_type = "Bank Transfer"
            else:
                tx_type = "Other"

            transactions.append({
                "Booking Date": booking_date + " 00:00:00",
                "Reference Account": ref_iban,
                "Reference Account Name": ref_account_name,
                "Amount (€)": amount,
                "Currency": "EUR",
                "Payee": payee,
                "IBAN": counterparty_iban,
                "Transaction Type": tx_type,
            })

    df = pd.DataFrame(transactions)
    print(df)
    df.to_csv("db_statement_extracted.csv", index=False)
    print(f"Extracted {len(df)} transactions and saved as db_statement_extracted.csv")

    # --- Save/Update balance if found ---
    if ref_iban and balance_found is not None:
        update_balance_for_account(ref_iban, balance_found)

    return df
import pandas as pd

def extract_barclays_excel(excel_path):
    # Read the file as no-header
    all_data = pd.read_excel(excel_path, header=None)
    meta = {}
    header_row = None
    for idx, row in all_data.iterrows():
        key = str(row[0]).strip()
        value = str(row[1]).strip() if len(row) > 1 else ""
        if key in ["IBAN", "Kontoname", "Kontonummer", "Stand", "Verfügungsrahmen"]:
            meta[key] = value
        if key == "Referenznummer":
            header_row = idx
            break

    if header_row is None:
        raise ValueError("Could not find 'Referenznummer' row to detect header.")

    # Read transaction table with proper header
    df = pd.read_excel(excel_path, header=header_row)

    # Balance: "Verfügungsrahmen" or another key, parse to float
    balance_str = meta.get("Verfügungsrahmen", "").replace(".", "").replace(",", ".")
    try:
        latest_balance = float(balance_str)
    except:
        latest_balance = None

    # Update balance file if IBAN found
    iban = meta.get("IBAN", "")
    if iban and latest_balance is not None:
        update_balance_for_account(iban, latest_balance)

    # Map columns
        # Map columns
    df["Booking Date"] = pd.to_datetime(df["Buchungsdatum"], errors="coerce")
    df["Reference Account"] = iban
    df["Reference Account Name"] = meta.get("Kontoname", "Barclays Visa")

    def clean_euro_number(numstr):
        s = re.sub(r"[^\d,.\-]", "", str(numstr).replace(" ", "").strip())

        if not s or s.lower() in ['nan', 'none']:
            return 0.0
        # Remove all dots that are thousands separators (if any)
        if ',' in s and '.' in s:
            s = s.replace('.', '')
        # Then replace decimal comma with decimal dot
        s = s.replace(',', '.')
        try:
            return float(s)
        except Exception as e:
            print(f"Could not convert: '{numstr}' cleaned as '{s}' | Error: {e}")
            return 0.0

    df["Amount (€)"] = df["Betrag"].apply(clean_euro_number)
    print(df["Betrag"].head(10).to_list())

    df["Payee"] = df["Beschreibung"]
    df["Currency"] = "EUR"
    df["IBAN"] = iban
    df["Balance (€)"] = None  # Transaction-level balance typically not present

    # Fill the rest as needed...
    final_cols = [
        "Booking Date", "Reference Account", "Reference Account Name",
        "Amount (€)", "Balance (€)", "Currency", "Payee", "IBAN"
        # Add other master columns here if needed...
    ]
    df_final = df[final_cols]

    print(df_final.head())
    df_final.to_csv("barclays_transactions_final.csv", index=False)
    print(f"Extracted {len(df_final)} Barclays transactions and saved as barclays_transactions_final.csv")

    return df_final