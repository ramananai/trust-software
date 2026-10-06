# Trust Accounting Software

Local, offline, multi-year accounts for the trust. Python + Streamlit + SQLite. All data stays in `data/unihope.db`.

## Install (Windows, one time)
1. Install Python 3.10+ (tick "Add Python to PATH").
2. Double-click `setup.bat` (creates the `venv` and installs everything).
3. Double-click `run.bat` any time to start. The browser opens automatically.

## First-time setup (in this order)
1. Settings -> Trust details (name, address, PAN, 12A, 80G, receipt prefix).
2. Settings -> Books & opening balances: first financial year you keep books here, opening Corpus Fund, opening FDs.
3. Settings -> Bank / Cash accounts: add each bank account with its opening balance (as on 1 April of the start year).
4. Fixed Assets -> add opening assets (WDV as on that date), if any.
5. Donors -> add donors with PAN. Then start entering Donations, Other Receipts, Application Register (payments), Transfers.

## Year-end
Reports page: Receipts & Payments, Income & Expenditure, Balance Sheet, Application of Income (85% check),
Fixed Asset schedule, Donation data for Form 10BD, multi-year comparison. Excel + PDF downloads.
Pick the next financial year in the sidebar; closing balances carry forward automatically.

## New in this version
- Logo and signature upload (Settings -> Trust details); printed on receipts, reports and the sidebar.
- Period selector in the sidebar: Financial year or Custom dates (applies to registers, ledger, dashboard, reports, audit log).
- Bank ledger shows closing balance after every entry plus a closing-balance table for every account.
- Bank statement import (Excel/CSV) in Bank Ledger & BRS: map columns, choose heads, duplicates skipped, imported rows marked cleared.
- Receipt wording: Section 133 of the Income-tax Act, 2025 (corresponding to Section 80G) for receipts dated on/after 1 April 2026; Section 80G for earlier dates.
- Cash donations above the limit (default Rs. 2,000, changeable in Settings) are flagged as not eligible; large cash payments show a warning.
- Entries are cancelled, not deleted: the number stays, the reason is recorded, and the receipt prints a CANCELLED mark.
- Donor donation certificates: single donor (Donors page) or all donors as a ZIP (Reports -> Donations).
- Upgrading: just replace app.py, core.py, requirements.txt and run setup.bat again. Your data folder is migrated automatically.

## Backup
Automatic daily copy in `data/backups` (last 30 kept). Also Settings -> Security & backup. Copy `data` to a pen drive regularly.

## Notes
- Capital expenditure is treated as full application of income; depreciation is shown only in the accounts (WDV method).
- Not covered: sale of assets, loans/advances/outstanding liabilities (books are receipts-and-payments based), multi-user access.
- Tax computations are helpers; have your CA verify before filing.
