"""The one transaction schema every stage reads and writes.

Column names match the Supabase ``transactions`` table and the Appsmith
dashboard, so they keep their original spelling (spaces and the euro sign).
"""

# What a bank extractor must produce.
EXTRACTED_COLUMNS = [
    "Booking Date",
    "Reference Account",
    "Reference Account Name",
    "Amount (€)",
    "Balance (€)",
    "Currency",
    "Payee",
    "IBAN",
    "Purpose",
    "Transaction Type",
    "Source File",
]

# The full row after cleaning, categorisation and contract detection.
COLUMNS = [
    "tx_id",
    *EXTRACTED_COLUMNS,
    "Main Category",
    "Subcategory",
    "Contract",
    "Contract Frequency",
    "Contract ID",
    "Internal Transfer",
    "Excluded from Disposable Income",
    "Analyzed Amount",
    "Week",
    "Month",
    "Quarter",
    "Year",
    "text",
    "payer",
    "needs_manual_input",
]

BOOL_COLUMNS = ["Contract", "Internal Transfer", "Excluded from Disposable Income", "needs_manual_input"]

# Allowed Main Category -> Subcategories. The model may only answer with a pair
# from this table; anything else is left for manual review.
CATEGORIES: dict[str, list[str]] = {
    "Groceries": ["Supermarket", "International Grocery", "Drugstore"],
    "Dining Out": ["Restaurant", "Fast Food", "Cafe", "Delivery"],
    "Car": ["Fuel", "Parking", "Car Wash", "Maintenance", "Car Insurance"],
    "Health": ["Pharmacy", "Health Insurance", "Private Insurance"],
    "Housing": ["Rent", "Gas", "Electricity", "Internet & Phone", "Broadcast Fee (GEZ)", "Furniture", "Renovation"],
    "Savings": ["Investments", "Savings Account"],
    "Shopping": ["Clothing", "Electronics", "Online Shopping", "Household", "Other Shopping"],
    "Leisure": ["Cinema", "Subscription", "Travel", "Games", "Sports"],
    "Baby": ["Kita", "Baby Supplies", "Toys"],
    "Lifestyle": ["Mobile", "Hairdresser", "Gym Membership", "Other Lifestyle", "Education"],
    "Banking": ["Bank Fees", "Credit Card Statement", "Credit", "Self Transfer"],
    "Income": ["Salary", "Other Income", "Child Benefit", "Refunds", "Social Benefits"],
    "Government": ["Taxes", "Social Benefits", "Pension"],
    "Mobility": ["Bicycle", "Public Transport", "Shared Mobility", "Taxi"],
}

# Money that only moves between your own pockets. Counting it as income or
# spending would double it, so it is excluded from disposable income.
EXCLUDED_PAIRS = {
    ("Banking", "Self Transfer"),
    ("Banking", "Credit Card Statement"),
}


def is_valid_category(main: str, sub: str) -> bool:
    return sub in CATEGORIES.get(main, [])
