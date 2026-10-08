-- Kubera Finance: Supabase (Postgres) schema and dashboard views.
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- The dashboard reads ONLY the views below, so every number it shows is
-- computed here, in one place: expenses are positive, transfers between your
-- own accounts are excluded, and uncategorised bookings are counted under
-- 'Uncategorised' so the chart always adds up to the card totals.

create table if not exists accounts (
    iban          text primary key,
    name          text,
    bank          text,
    balance       numeric(12, 2),
    balance_date  date,
    updated_at    timestamptz not null default now()
);

create table if not exists transactions (
    tx_id                              text primary key,
    "Booking Date"                     date,
    "Reference Account"                text,
    "Reference Account Name"           text,
    "Amount (€)"                       numeric(12, 2) not null,
    "Balance (€)"                      numeric(12, 2),
    "Currency"                         text default 'EUR',
    "Payee"                            text,
    "IBAN"                             text,
    "Purpose"                          text,
    "Transaction Type"                 text,
    "Source File"                      text,
    "Main Category"                    text,
    "Subcategory"                      text,
    "Contract"                         boolean default false,
    "Contract Frequency"               text,
    "Contract ID"                      text,
    "Internal Transfer"                boolean default false,
    "Excluded from Disposable Income"  boolean default false,
    "Analyzed Amount"                  text,
    "Week"                             text,
    "Month"                            text,
    "Quarter"                          text,
    "Year"                             integer,
    "text"                             text,
    "payer"                            text,
    needs_manual_input                 boolean default false
);

create index if not exists transactions_month_idx on transactions ("Month");

-- Months that have data, newest first (dropdown options).
create or replace view v_months as
select distinct "Month" as month
from transactions
where "Month" is not null and "Month" <> ''
order by 1 desc;

-- One row per month: what the summary cards show.
create or replace view v_monthly_summary as
select
    "Month"                                                             as month,
    coalesce(sum("Amount (€)") filter (where "Amount (€)" > 0), 0)      as income,
    coalesce(-sum("Amount (€)") filter (where "Amount (€)" < 0), 0)     as expenses,
    coalesce(sum("Amount (€)"), 0)                                      as net,
    count(*) filter (where needs_manual_input)                          as uncategorised_count
from transactions
where not coalesce("Excluded from Disposable Income", false)
group by "Month";

-- Spending per main category per month (positive numbers).
create or replace view v_category_expenses as
select
    "Month"                                                             as month,
    coalesce(nullif("Main Category", ''), 'Uncategorised')              as main_category,
    -sum("Amount (€)")                                                  as total_spent,
    count(distinct nullif("Subcategory", ''))                           as subcategory_count,
    count(*)                                                            as tx_count
from transactions
where "Amount (€)" < 0
  and not coalesce("Excluded from Disposable Income", false)
group by 1, 2;

-- Spending per subcategory, for the drill-down page.
create or replace view v_subcategory_expenses as
select
    "Month"                                                             as month,
    coalesce(nullif("Main Category", ''), 'Uncategorised')              as main_category,
    coalesce(nullif("Subcategory", ''), 'Uncategorised')                as subcategory,
    -sum("Amount (€)")                                                  as total_spent,
    count(*)                                                            as tx_count
from transactions
where "Amount (€)" < 0
  and not coalesce("Excluded from Disposable Income", false)
group by 1, 2, 3;

-- Latest balance per account, and the total across accounts.
create or replace view v_account_balances as
select iban, name, bank, balance, balance_date
from accounts
order by name;

create or replace view v_total_balance as
select coalesce(sum(balance), 0) as total_balance, max(balance_date) as as_of
from accounts;

-- Recurring payments, with when the next one is due.
create or replace view v_contracts as
select
    "Contract ID"                                                       as contract_id,
    max("Payee")                                                        as payee,
    max("Main Category")                                                as main_category,
    max("Subcategory")                                                  as subcategory,
    max("Contract Frequency")                                           as frequency,
    round(avg(abs("Amount (€)")), 2)                                    as typical_amount,
    max("Booking Date")                                                 as last_paid,
    (max("Booking Date") + case max("Contract Frequency")
        when 'Weekly'    then interval '7 days'
        when 'Monthly'   then interval '1 month'
        when 'Quarterly' then interval '3 months'
        when 'Yearly'    then interval '1 year'
    end)::date                                                          as next_due
from transactions
where "Contract"
group by "Contract ID";

-- Bookings the model could not place: the review queue.
create or replace view v_needs_review as
select tx_id, "Booking Date", "Reference Account Name", "Payee", "Purpose", "Amount (€)"
from transactions
where needs_manual_input
order by "Booking Date" desc;


-- ---------------------------------------------------------------------------
-- Migrating a table created by the old scripts (no tx_id, text columns):
-- run these once, then `kubera run --upload` to re-load all rows with ids.
--
--   alter table transactions rename to transactions_v1;
--   -- now re-run this file to create the new table, then:
--   kubera run --upload
--   -- check the dashboard, then:  drop table transactions_v1;
-- ---------------------------------------------------------------------------
