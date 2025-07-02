import os
import pandas as pd

EXTRACTED_FOLDER = 'extracted_transactions'
OUTPUT_FILE = 'all_bank_transactions_combined.csv'

csv_files = [
    os.path.join(EXTRACTED_FOLDER, f)
    for f in os.listdir(EXTRACTED_FOLDER)
    if f.endswith('.csv')
]

dataframes = []
for csv in csv_files:
    try:
        df = pd.read_csv(csv)
        df['Source File'] = os.path.basename(csv)
        dataframes.append(df)
    except Exception as e:
        print(f"❌ Error reading {csv}: {e}")

if not dataframes:
    print("No transactions to combine.")
else:
    df_combined = pd.concat(dataframes, ignore_index=True, sort=False)
    df_combined = df_combined.drop_duplicates()
    master_columns = [
        'idx', 'Booking Date', 'Reference Account', 'Reference Account Name',
        'Amount (€)', 'Balance (€)', 'Currency', 'Payee', 'IBAN', 'Purpose',
        'E-Reference', 'Mandate Reference', 'Creditor ID', 'Main Category',
        'Subcategory', 'Contract', 'Contract Frequency', 'Contract ID',
        'Internal Transfer', 'Excluded from Disposable Income', 'Transaction Type',
        'Analyzed Amount', 'Week', 'Month', 'Quarter', 'Year', 'Tags', 'Note',
        'text', 'payer', 'needs_manual_input', 'Source File'
    ]
    final_cols = [c for c in master_columns if c in df_combined.columns]
    df_combined = df_combined[final_cols + [c for c in df_combined.columns if c not in final_cols]]
    df_combined = df_combined.fillna('')
    df_combined.to_csv(OUTPUT_FILE, index=False)
    print(f"✅ Combined {len(csv_files)} files into {OUTPUT_FILE}")
    print(df_combined.head())
