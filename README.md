# Kubera Finance App

**End-to-end Bank Statement ETL Pipeline — Data Engineering Project**

![Kubera Logo](https://i.postimg.cc/rwFKCB0K/kubera-round-icon-transparent.png)

## 🚀 Overview

Kubera Finance App is a modular data engineering pipeline that automates the extraction, cleaning, AI-based categorization, contract detection, and cloud database upload of bank statement data (PDF/Excel) from multiple sources.

---

## 🏗️ Key Features

- **Multi-bank Extraction**: Parse statements from DKB, N26, DB, Barclays (PDF/Excel)
- **Data Cleaning**: Standardize, harmonize, and prepare data for analytics
- **AI-powered Categorization**: Uses Google Gemini to classify spending by category/subcategory
- **Contract Frequency Detection**: Flags recurring/subscription transactions
- **Supabase Integration**: Automated upload to cloud database
- **Privacy-First**: No sensitive or real data shared in this repo (dummy/synthetic samples provided)

---

## 📦 Project Structure

.
├── bank_extractors.py # PDF/Excel extraction logic
├── process_all_transactions.py # Runs extraction for all files
├── combine_extracted_transactions.py # Combines outputs to single CSV
├── clean_transactions.py # Data cleaning and harmonization
├── categorize_and_upload.py # Categorizes and uploads to Supabase
├── extracted_transactions/ # (Git-ignored) Individual bank outputs
├── transactions/ # (Git-ignored) Input statement files
├── .env.example # Sample environment config
└── README.md

yaml

---

## 🔒 Security

- **No sensitive data**: All credentials are excluded via `.gitignore`
- **Use `.env` file**: See `.env.example` for configuration template

---

## 🏃‍♀️ How To Run

1. **Clone this repo**
2. **Install requirements** (requirements.txt)
3. **Set up your `.env` file**
4. **Place your bank statements (PDF/XLSX) in `/transactions` folder**
5. **Run pipeline scripts in order:**
   - `process_all_transactions.py`
   - `combine_extracted_transactions.py`
   - `clean_transactions.py`
   - `categorize_and_upload.py`

---

## 📄 Sample Data

- Dummy bank statements and sample data provided for reproducibility (see `/transactions` folder).

---

## 👩‍💻 Author

**Pavatharini Muthukkumar**  
[LinkedIn](https://www.linkedin.com/in/pavatharini-muthukkumar) | [Portfolio](link)

---

## ⭐️ Contributions

PRs and suggestions are welcome! For questions, raise an issue.
