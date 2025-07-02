import os
import re
import json
import time
import hashlib
import pandas as pd
from dotenv import load_dotenv

# ==== Load secrets from .env file ====
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

assert SUPABASE_URL, "Missing SUPABASE_URL in .env"
assert SUPABASE_KEY, "Missing SUPABASE_KEY in .env"
assert GEMINI_API_KEY, "Missing GEMINI_API_KEY in .env"

# ==== Set up Supabase ====
from supabase import create_client, Client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==== Set up Gemini ====
import google.generativeai as genai
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(model_name="models/gemini-1.5-flash")

# ==== Patterns for cleaning ====
own_name_patterns = [
    r'(?i)vignesh\s+natarajan',
    r'(?i)natarajan\s+vignesh',
    r'(?i)natarajan,\s*vignesh',
]
noise_patterns = [
    r'(?i)issuer',
    r'(?i)visa\s+debitkartenumsatz',
    r'(?i)tassilostrasse\s+\d+\s+\d{5}\s+\w+',
    r'(?i)im\s+heller\s+\d+',
    r'(?i)bayenwerft\s+\d+',
    r'(?i)flughafenstr(?:a|ä)?sse\s+\d+',
    r'(?i)siegburger\s+str(?:a|ä)?sse\s+\d+',
    r'(?i)scalable\s+capital.*?',
    r'(?i)karte[nn]?abrechnung.*?',
    r'(?i)kundennummer.*?',
    r'(?i)rechnung.*?',
    r'(?i)darlehensrate.*?',
    r'(?i)check24.*?',
    r'(?i)www\.\S+',
    r'(?i)\d{3,}[.,]\d{2}',  # monetary values
]
def clean_text(text, remove_names=True):
    if not isinstance(text, str):
        return ""
    #patterns = noise_patterns + (own_name_patterns if remove_names else [])
    patterns = noise_patterns 
    for pattern in patterns:
        text = re.sub(pattern, '', text)
    return text.strip()

# ==== Gemini cache system ====
MEMORY_FILE = 'gemini_memory.json'
REQUIRED_DELAY_SECONDS = 4.0

def normalize_text(text):
    """Normalize text for consistent cache key generation"""
    if not text:
        return ""
    return text.strip().lower().replace('\n', ' ').replace('\r', '').replace('\t', ' ')

def create_cache_key(text):
    normalized = normalize_text(text)
    if len(normalized) > 500:
        return hashlib.md5(normalized.encode('utf-8')).hexdigest()
    return normalized

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                memory = json.load(f)
                print(f"✅ Loaded {len(memory)} entries from cache file")
                return memory
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"⚠️ Error loading cache file: {e}. Starting with empty cache.")
            return {}
    else:
        print("📝 No existing cache file found. Starting with empty cache.")
        return {}

def save_memory(memory):
    try:
        if os.path.exists(MEMORY_FILE):
            backup_file = f"{MEMORY_FILE}.backup"
            os.rename(MEMORY_FILE, backup_file)
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
        backup_file = f"{MEMORY_FILE}.backup"
        if os.path.exists(backup_file):
            os.remove(backup_file)
        print(f"💾 Saved {len(memory)} entries to cache file")
    except Exception as e:
        print(f"❌ Error saving cache file: {e}")
        backup_file = f"{MEMORY_FILE}.backup"
        if os.path.exists(backup_file):
            os.rename(backup_file, MEMORY_FILE)
            print("🔄 Restored backup cache file")

# ==== Gemini API wrapper ====
def ask_gemini_for_category(text):
    prompt = f"""
You are a smart finance assistant. Based on the transaction text below, determine the most appropriate **Main Category and Subcategory combination**.

Transaction:
"{text}"

Instructions:

1. Match the transaction to **one and only one** of the following **Main Category → Subcategory** combinations. Evaluate them **jointly** and not independently. The match must be based on **overall meaning**, including merchant name, purpose, and any recognizable patterns.

   - Groceries → Supermarket, International Grocery, Drugstore
   - Dining Out → Restaurant, Fast Food, Cafe, Delivery
   - Car → Fuel, Parking, Car Wash, Maintenance, Car Insurance
   - Health → Pharmacy, Health Insurance, Private Insurance
   - Housing → Rent, Gas, Electricity, Internet & Phone, Broadcast Fee (GEZ), Furniture, Renovation
   - Savings → Investments, Savings Account
   - Shopping → Clothing, Electronics, Online Shopping, Household, Other Shopping
   - Leisure → Cinema, Subscription (e.g. Netflix), Travel, Games, Sports
   - Baby → Kita, Baby Supplies, Toys
   - Lifestyle → Mobile, Hairdresser, Gym Membership, Other Lifestyle, Education
   - Banking → Bank Fees, Credit Card Statement, Credit, Self Transfer
   - Income → Salary, Other Income, Child Benefit, Refunds, Social Benefits
   - Government → Taxes, Social Benefits, Pension
   - Mobility → Bicycle, Public Transport, Shared Mobility, Taxi 

2. When identifying categories:
   - For stores like "DM", "Rossmann", or mixed-type names, consider context: if it appears related to groceries, cosmetics, hygiene, or pharmacy items, prefer **Groceries → Drugstore** over Shopping.
   - Don’t assume based on merchant name alone — check if the transaction text implies a better match.

3. If the transaction text includes a **combination** of sender and receiver names such as:
   - "Vignesh Natarajan", "Natarajan Vignesh", "Pavatharini Muthukkumar", or any variation thereof,
   - and it looks like an internal or personal money movement (e.g. "Sent from N26", "Money2India", etc.),
   then classify it as:
   - Main Category = "Banking"
   - Subcategory = "Self Transfer"

4. If the transaction **does not clearly** fit into any allowed combination, return:
   - "Main Category": "",
   - "Subcategory": ""

Do not invent or guess new categories.

Respond strictly in this JSON format:
{{
  "Main Category": "...",
  "Subcategory": "...",
  "Contract": true or false,
  "Contract Frequency": "...",
  "Excluded from Disposable Income": true or false
}}
"""
    try:
        response = gemini_model.generate_content(prompt)
        raw = response.text.strip()
        json_text = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_text:
            result = json.loads(json_text.group())
            return result
    except Exception as e:
        print(f"⚠️ Gemini API Error: {e}")
    return {
        "Main Category": "",
        "Subcategory": "",
        "Contract": False,
        "Contract Frequency": "",
        "Excluded from Disposable Income": False
    }

# ==== Contract frequency detection ====
def detect_contract_frequency(df: pd.DataFrame) -> pd.DataFrame:
    df['Booking Date'] = pd.to_datetime(df['Booking Date'], errors='coerce')
    df.sort_values(by='Booking Date', inplace=True)
    df['Contract Frequency'] = ""  # Default

    grouped = df.groupby(['Payee', 'Subcategory'])
    for (payee, subcat), group in grouped:
        if len(group) < 3:
            continue
        intervals = group['Booking Date'].diff().dt.days.dropna()
        avg_interval = intervals.mean()
        freq = ""
        if 25 <= avg_interval <= 35:
            freq = "Monthly"
        elif 80 <= avg_interval <= 100:
            freq = "Quarterly"
        elif 360 <= avg_interval <= 380:
            freq = "Yearly"
        df.loc[group.index, 'Contract Frequency'] = freq
    return df

# ==== MAIN FUNCTION ====
def main():
    # === Load cleaned transactions ===
    input_csv = "all_bank_transactions_cleaned.csv"
    output_csv = "categorized_transactions.csv"
    df = pd.read_csv(input_csv)

    # ==== Clean and recompute 'text' column ====
    df['Payee'] = df['Payee'].apply(lambda x: clean_text(x, remove_names=False))
    df['Purpose'] = df['Purpose'].apply(lambda x: clean_text(x, remove_names=True))
    df['text'] = (df['Payee'].fillna('') + ' ' + df['Purpose'].fillna('')).str.strip()
    df['text'] = df['text'].apply(lambda x: clean_text(x, remove_names=False))

    # ==== Load memory cache ====
    gemini_memory = load_memory()

    # ==== Enrichment ====
    print(f"\n🚀 Starting enrichment for {len(df)} transactions")
    print(f"📊 Memory cache contains {len(gemini_memory)} entries")

    df['Contract'] = False
    df['Contract Frequency'] = ""
    df['Excluded from Disposable Income'] = False
    df['needs_manual_input'] = False

    cache_hits = 0
    api_calls = 0
    last_gemini_call_time = time.monotonic()

    for idx, row in df.iterrows():
      text = row['text'] if 'text' in row else str(row.get('description', ''))
      if not text or text.strip() == '':
        continue
      cache_key = create_cache_key(text)
      if cache_key in gemini_memory:
        result = gemini_memory[cache_key]
        cache_hits += 1
      else:
        current_time = time.monotonic()
        time_since_last_call = current_time - last_gemini_call_time
        if time_since_last_call < REQUIRED_DELAY_SECONDS:
            time.sleep(REQUIRED_DELAY_SECONDS - time_since_last_call)
        last_gemini_call_time = time.monotonic()
        result = ask_gemini_for_category(text)
        gemini_memory[cache_key] = result
        api_calls += 1
      main_cat = result.get("Main Category", "")
      sub_cat = result.get("Subcategory", "")
      df.at[idx, 'Main Category'] = main_cat
      df.at[idx, 'Subcategory'] = sub_cat
      df.at[idx, 'Contract'] = result.get("Contract", False)
      df.at[idx, 'Contract Frequency'] = ""  # Will fill after
      df.at[idx, 'Excluded from Disposable Income'] = result.get("Excluded from Disposable Income", False)
      # Updated logic for needs_manual_input:
      df.at[idx, 'needs_manual_input'] = (not main_cat or not sub_cat)

    # Save updated cache
    if api_calls > 0:
        save_memory(gemini_memory)

    print(f"\n📈 Summary: {len(df)} processed, {cache_hits} cache hits, {api_calls} API calls.")

    # ==== Detect contract frequency ====
    df = detect_contract_frequency(df)

    # remove index column 
    if 'idx' in df.columns:
      df = df.drop(columns=['idx'])


    # ==== Save as categorized_transactions.csv ====
    df.to_csv(output_csv, index=False)
    print(f"✅ Categorized transactions saved as {output_csv}")

    # ==== OPTIONAL: Upload to Supabase ====
    # Uncomment to upload all records to Supabase 'transactions' table
    # records = df.to_dict(orient="records")
    # for record in records:
    #     response = supabase.table("transactions").insert(record).execute()

if __name__ == "__main__":
    main()
