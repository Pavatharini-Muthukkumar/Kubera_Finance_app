import pandas as pd
import numpy as np
from datetime import datetime

def clean_and_harmonize_transactions(input_csv, output_csv):
    df = pd.read_csv(input_csv)

    # Remove index columns accidentally saved in CSV
    for col in df.columns:
        if col.lower() in ['unnamed: 0', 'index']:
            df = df.drop(columns=[col])

    # Drop/ignore any Balance column (not needed at this step)
    if 'Balance (€)' in df.columns:
        df = df.drop(columns=['Balance (€)'])

    # Harmonize Booking Date to yyyy-mm-dd 00:00:00
    if 'Booking Date' in df.columns:
        def parse_date(x):
            if pd.isna(x) or not str(x).strip():
                return ""
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    return datetime.strptime(str(x).split()[0], fmt).strftime("%Y-%m-%d 00:00:00")
                except Exception:
                    continue
            try:
                return pd.to_datetime(x, errors='coerce').strftime("%Y-%m-%d 00:00:00")
            except Exception:
                return ""
        df['Booking Date'] = df['Booking Date'].apply(parse_date)

    # Fill missing columns to master schema
    master_cols = [
        'idx','Booking Date','Reference Account','Reference Account Name','Amount (€)','Balance (€)','Currency','Payee','IBAN',
        'Purpose','E-Reference','Mandate Reference','Creditor ID','Main Category','Subcategory','Contract','Contract Frequency',
        'Contract ID','Internal Transfer','Excluded from Disposable Income','Transaction Type','Analyzed Amount','Week','Month',
        'Quarter','Year','Tags','Note','text','payer','needs_manual_input','Source File'
    ]
    for col in master_cols:
        if col not in df.columns:
            df[col] = ""
    # Remove columns not in master_cols
    df = df[[c for c in master_cols if c in df.columns]]

    # Fill NaNs with correct types
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].fillna("")
        elif np.issubdtype(df[col].dtype, np.number):
            df[col] = df[col].fillna(0)
        elif df[col].dtype == bool:
            df[col] = df[col].fillna(False)
        else:
            df[col] = df[col].fillna("")

    # Set idx (row number, starting at 1)
    df['idx'] = range(1, len(df)+1)

    # Analyzed Amount: based on Amount (€)
    def get_analyzed_amount(x):
        try:
            return "Expenses" if float(x) < 0 else "Income"
        except:
            return ""
    df['Analyzed Amount'] = df['Amount (€)'].apply(get_analyzed_amount)

    # Week, Month, Quarter, Year columns (from Booking Date)
    def parse_date_for_fields(dtstr):
        try:
            dt = pd.to_datetime(dtstr[:10], errors='coerce')
            if pd.isna(dt):
                return ("", "", "", "")
            week = f"{dt.year}-" + f"{dt.isocalendar()[1]:02d}"
            month = f"{dt.year}-" + f"{dt.month:02d}"
            quarter = f"{dt.year}-Q{((dt.month-1)//3)+1}"
            year = str(dt.year)
            return (week, month, quarter, year)
        except:
            return ("", "", "", "")
    date_fields = df['Booking Date'].apply(parse_date_for_fields)
    df['Week'] = [w for w,m,q,y in date_fields]
    df['Month'] = [m for w,m,q,y in date_fields]
    df['Quarter'] = [q for w,m,q,y in date_fields]
    df['Year'] = [y for w,m,q,y in date_fields]

    # Clean bool columns to True/False, string to "", etc.
    bool_cols = ['Contract','Internal Transfer','Excluded from Disposable Income','needs_manual_input']
    for col in bool_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().map({'true':True, 'false':False, '1':True, '0':False, 'yes':True, 'no':False, '':False, None:False}).fillna(False)

    # Final default fill
    columns_to_clean = [
    'Main Category', 'Subcategory', 'Tags', 'Note', 'Creditor ID', 
    'Purpose', 'E-Reference', 'Mandate Reference', 'Contract ID', 
    'Contract Frequency', 'Reference Account Name', 'Payee', 'payer']
    for col in columns_to_clean:
      if col in df.columns:
        df[col] = df[col].replace(["0", "0.0", 0], "", regex=False)
        df[col] = df[col].replace(r"^\s*0(\.0)?\s*$", "", regex=True)

    # Fill NaN for object (string) columns
    string_cols = df.select_dtypes(include='object').columns
    df[string_cols] = df[string_cols].fillna("")

    # Fill NaN for numeric columns
    numeric_cols = df.select_dtypes(include=['number']).columns
    df[numeric_cols] = df[numeric_cols].fillna(0)

    # Fill NaN for boolean columns
    bool_cols = df.select_dtypes(include='bool').columns
    df[bool_cols] = df[bool_cols].fillna(False)

    # --- Always reconstruct 'text' as "Payee Purpose" (space separated, both cleaned of NaN/0/empty) ---
    def safe_str(x):
      if pd.isna(x) or str(x).strip() in ["0", "0.0"]:
        return ""
      return str(x).strip()

    df["text"] = df.apply(lambda row: f"{safe_str(row.get('Payee', ''))} {safe_str(row.get('Purpose', ''))}".strip(), axis=1)

    

    # Save cleaned output
    df.to_csv(output_csv, index=False)
    print(f"✅ Cleaned & harmonized file written to {output_csv}")
    print(df.head(10))
    return df

# --- Run as script ---
if __name__ == "__main__":
    # Change these to your actual file names as needed
    input_csv = "all_bank_transactions_combined.csv"
    output_csv = "all_bank_transactions_cleaned.csv"
    clean_and_harmonize_transactions(input_csv, output_csv)
