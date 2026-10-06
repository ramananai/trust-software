"""Core engine for Unihope Foundation trust accounts: database, accounting rules, reports, exports."""
import base64
import datetime as dt
import hashlib
import io
import os
import sqlite3
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as esc

import pandas as pd

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
DB = Path(os.environ.get("UNIHOPE_DB", DATA / "unihope.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT, bank TEXT, acc_no TEXT, opening REAL DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS heads(id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT, is_donation INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS donors(id INTEGER PRIMARY KEY, name TEXT, pan TEXT, address TEXT, phone TEXT, email TEXT);
CREATE TABLE IF NOT EXISTS txns(id INTEGER PRIMARY KEY, kind TEXT, date TEXT, voucher_no TEXT UNIQUE, account_id INTEGER, to_account_id INTEGER,
  amount REAL, head_id INTEGER, donor_id INTEGER, party TEXT, mode TEXT, ref_no TEXT, purpose TEXT, remarks TEXT,
  cleared_date TEXT, cleared_date2 TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, cancelled INTEGER DEFAULT 0, cancel_reason TEXT);
CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY, name TEXT, purchase_date TEXT, cost REAL, rate REAL, is_opening INTEGER DEFAULT 0, txn_id INTEGER);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP, action TEXT, detail TEXT);
"""
VIEW = """
DROP VIEW IF EXISTS moves;
CREATE VIEW moves AS
 SELECT id,date,voucher_no,kind,account_id acc,amount signed,head_id,donor_id,party,ref_no,purpose,mode,cleared_date cleared,'A' side FROM txns WHERE cancelled=0 AND kind='RECEIPT'
 UNION ALL SELECT id,date,voucher_no,kind,account_id,-amount,head_id,donor_id,party,ref_no,purpose,mode,cleared_date,'A' FROM txns WHERE cancelled=0 AND kind='PAYMENT'
 UNION ALL SELECT id,date,voucher_no,kind,account_id,-amount,head_id,donor_id,party,ref_no,purpose,mode,cleared_date,'A' FROM txns WHERE cancelled=0 AND kind='TRANSFER'
 UNION ALL SELECT id,date,voucher_no,kind,to_account_id,amount,head_id,donor_id,party,ref_no,purpose,mode,cleared_date2,'B' FROM txns WHERE cancelled=0 AND kind='TRANSFER';
"""

# (name, kind, is_donation)  kind: Income/Corpus/Realisation (receipts)  Revenue/Capital/Investment (payments)
HEADS = [
    ("Donation - General", "Income", 1), ("Donation - Specific Purpose / Project", "Income", 1), ("Donation - Corpus", "Corpus", 1),
    ("Grant Received", "Income", 0), ("Interest - Savings Bank", "Income", 0), ("Interest - Fixed Deposit", "Income", 0),
    ("Membership / Event Income", "Income", 0), ("Other Income", "Income", 0), ("FD Matured / Investment Redeemed", "Realisation", 0),
    ("Programme / Project Expenses", "Revenue", 0), ("Salaries & Honorarium", "Revenue", 0), ("Rent", "Revenue", 0),
    ("Electricity & Utilities", "Revenue", 0), ("Printing, Postage & Stationery", "Revenue", 0), ("Travel & Conveyance", "Revenue", 0),
    ("Professional / Audit Fees", "Revenue", 0), ("Repairs & Maintenance", "Revenue", 0), ("Bank Charges", "Revenue", 0),
    ("Donations / Grants to Other Institutions", "Revenue", 0), ("Miscellaneous Expenses", "Revenue", 0),
    ("Purchase of Fixed Assets", "Capital", 0), ("Investment in FD / Securities", "Investment", 0),
]


# ---------------------------------------------------------------- database helpers
def q(sql, p=()):
    cn = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, cn, params=list(p))
    finally:
        cn.close()


def ex(sql, p=()):
    cn = sqlite3.connect(DB)
    try:
        cur = cn.execute(sql, list(p))
        cn.commit()
        return cur.lastrowid
    finally:
        cn.close()


def nz(v, d=""):
    return d if v is None or (isinstance(v, float) and pd.isna(v)) else v


def init_db():
    cn = sqlite3.connect(DB)
    cn.executescript(SCHEMA)
    cols = [r[1] for r in cn.execute("PRAGMA table_info(txns)")]
    for col, ddl in (("cancelled", "INTEGER DEFAULT 0"), ("cancel_reason", "TEXT")):
        if col not in cols:
            cn.execute(f"ALTER TABLE txns ADD COLUMN {col} {ddl}")
    cn.executescript(VIEW)
    defaults = dict(trust_name="Unihope Foundation", address="", reg_no="", pan="", reg12a="", reg80g="", phone="", email="",
                    prefix="UF", signatory="Authorised Signatory", books_start=fy_of(dt.date.today()),
                    opening_corpus=0, opening_inv=0, apply_pct=85, cash_limit=2000, cash_pay_limit=10000, appr_valid="")
    for k, v in defaults.items():
        cn.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, str(v)))
    if not cn.execute("SELECT count(*) FROM heads").fetchone()[0]:
        cn.executemany("INSERT INTO heads(name,kind,is_donation) VALUES(?,?,?)", HEADS)
    if not cn.execute("SELECT count(*) FROM accounts").fetchone()[0]:
        cn.execute("INSERT INTO accounts(name,type) VALUES('Cash in Hand','Cash')")
    cn.commit()
    cn.close()


def backup():
    d = DATA / "backups"
    d.mkdir(exist_ok=True)
    f = d / f"unihope_{dt.date.today():%Y%m%d}.db"
    if DB.exists() and not f.exists():
        src, dst = sqlite3.connect(DB), sqlite3.connect(f)
        src.backup(dst)
        dst.close()
        src.close()
        for old in sorted(d.glob("unihope_*.db"))[:-30]:
            old.unlink()


def get_setting(k, d=""):
    r = q("SELECT v FROM settings WHERE k=?", (k,))
    return r.v[0] if len(r) else d


def set_setting(k, v):
    ex("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def fget(k, d=0.0):
    try:
        return float(get_setting(k, d))
    except ValueError:
        return d


def audit(action, detail):
    ex("INSERT INTO audit(action,detail) VALUES(?,?)", (action, detail))


def hash_pw(p, salt=None):
    salt = salt or os.urandom(8).hex()
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', p.encode(), salt.encode(), 100000).hex()}"


def check_pw(p):
    stored = get_setting("pw_hash")
    return (not stored) or hash_pw(p, stored.split("$")[0]) == stored


# ---------------------------------------------------------------- financial year helpers
def fy_of(d):
    d = pd.Timestamp(d)
    return d.year if d.month >= 4 else d.year - 1


def fy_label(y):
    return f"{y}-{str(y + 1)[2:]}"


def fy_range(y):
    return f"{y}-04-01", f"{y + 1}-03-31"


def books_start():
    return int(get_setting("books_start", fy_of(dt.date.today())))


# ---------------------------------------------------------------- transactions
def next_voucher(letter, date):
    y = fy_of(date)
    pre = f"{get_setting('prefix', 'UF')}/{fy_label(y)}/{letter}"
    nums = []
    for v in q("SELECT voucher_no FROM txns WHERE voucher_no LIKE ?", (pre + "%",)).voucher_no:
        try:
            nums.append(int(v[len(pre):]))
        except ValueError:
            pass
    return f"{pre}{max(nums + [0]) + 1:04d}"


def save_txn(d, tid=None):
    d = {k: (v.item() if hasattr(v, "item") else v) for k, v in d.items()}
    an, rate = d.pop("asset_name", ""), float(d.pop("rate", 15) or 0)
    d["date"] = str(d["date"])
    if tid and int(q("SELECT cancelled FROM txns WHERE id=?", (tid,)).cancelled[0]):
        raise ValueError("Cancelled entries cannot be edited.")
    if d["date"] < f"{books_start()}-04-01":
        raise ValueError(f"Date is before the books start (FY {fy_label(books_start())}). Change it in Settings if needed.")
    if not d.get("amount") or d["amount"] <= 0:
        raise ValueError("Amount must be greater than zero.")
    if d["kind"] == "TRANSFER" and d["account_id"] == d.get("to_account_id"):
        raise ValueError("From and To accounts must be different.")
    h = q("SELECT * FROM heads WHERE id=?", (d["head_id"],)) if d.get("head_id") else None
    if tid:
        ks = [k for k in d if k not in ("kind", "voucher_no")]
        ex(f"UPDATE txns SET {','.join(k + '=?' for k in ks)} WHERE id=?", [d[k] for k in ks] + [tid])
        audit("EDIT", f"txn {tid} {d['date']} {d['amount']}")
    else:
        letter = {"PAYMENT": "P", "TRANSFER": "T"}.get(d["kind"]) or ("D" if h is not None and h.is_donation[0] else "R")
        d["voucher_no"] = next_voucher(letter, d["date"])
        ks = list(d)
        tid = ex(f"INSERT INTO txns({','.join(ks)}) VALUES({','.join('?' * len(ks))})", [d[k] for k in ks])
        audit("ADD", f"{d['voucher_no']} {d['date']} {d['amount']}")
    if h is not None and h.kind[0] == "Capital":
        nm = an or d.get("party") or "Fixed asset"
        if len(q("SELECT id FROM assets WHERE txn_id=?", (tid,))):
            ex("UPDATE assets SET name=?,purchase_date=?,cost=?,rate=? WHERE txn_id=?", (nm, d["date"], d["amount"], rate, tid))
        else:
            ex("INSERT INTO assets(name,purchase_date,cost,rate,txn_id) VALUES(?,?,?,?,?)", (nm, d["date"], d["amount"], rate, tid))
    else:
        ex("DELETE FROM assets WHERE txn_id=?", (tid,))
    return tid


def cancel_txn(tid, reason):
    v = q("SELECT voucher_no,amount,cancelled FROM txns WHERE id=?", (tid,))
    if not len(v) or int(v.cancelled[0]):
        raise ValueError("Entry not found or already cancelled.")
    ex("UPDATE txns SET cancelled=1,cancel_reason=? WHERE id=?", (reason, tid))
    ex("DELETE FROM assets WHERE txn_id=?", (tid,))
    audit("CANCEL", f"{v.voucher_no[0]} {v.amount[0]} - {reason}")


# ---------------------------------------------------------------- accounting calculations
def balances(upto):
    return q("""SELECT a.id,a.name,a.type,a.opening+COALESCE((SELECT SUM(signed) FROM moves m WHERE m.acc=a.id AND m.date<=?),0) bal
                FROM accounts a ORDER BY a.id""", (upto,))


def head_totals(kinds, s, e):
    ph = ",".join("?" * len(kinds))
    return q(f"""SELECT h.name Particulars,h.kind Kind,SUM(t.amount) Amount FROM txns t JOIN heads h ON h.id=t.head_id
                 WHERE t.kind IN ('RECEIPT','PAYMENT') AND h.kind IN ({ph}) AND t.cancelled=0 AND t.date BETWEEN ? AND ?
                 GROUP BY h.id ORDER BY h.id""", (*kinds, s, e))


def asset_year(y):
    """Fixed-asset schedule for FY y (WDV method; half rate if put to use after 2 Oct)."""
    bs, rows = books_start(), []
    for a in q("SELECT * FROM assets ORDER BY id").itertuples():
        opn = bool(a.is_opening)
        py = bs if opn else fy_of(a.purchase_date)
        if y < py:
            continue
        wdv = float(a.cost) if opn else 0.0
        for yy in range(py, y + 1):
            op = wdv
            add = 0.0 if opn else (float(a.cost) if yy == py else 0.0)
            half = (not opn) and yy == py and str(a.purchase_date) > f"{yy}-10-02"
            dep = round((op + add) * a.rate / 100 * (0.5 if half else 1), 2)
            wdv = op + add - dep
        rows.append([a.name, a.rate, op, add, dep, wdv])
    return pd.DataFrame(rows, columns=["Asset", "Rate %", "Opening WDV", "Additions", "Depreciation", "Closing WDV"])


def prev_day(s):
    return (pd.Timestamp(s) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def totals(s, e, dep=0.0):
    def g(ks):
        return float(head_totals(ks, s, e)["Amount"].sum())

    t = dict(income=g(("Income",)), corpus=g(("Corpus",)), realis=g(("Realisation",)), revenue=g(("Revenue",)),
             capital=g(("Capital",)), invest=g(("Investment",)), dep=dep)
    t["surplus"] = t["income"] - t["revenue"] - t["dep"]
    t["closing"] = float(balances(e)["bal"].sum())
    return t


def fy_totals(y):
    s, e = fy_range(y)
    return totals(s, e, float(asset_year(y)["Depreciation"].sum()))


def account_summary(s, e):
    df = q("""SELECT a.id,a.name Account,
        a.opening+COALESCE((SELECT SUM(signed) FROM moves m WHERE m.acc=a.id AND m.date<?),0) Opening,
        COALESCE((SELECT SUM(signed) FROM moves m WHERE m.acc=a.id AND m.date BETWEEN ? AND ? AND signed>0),0) Receipts,
        -COALESCE((SELECT SUM(signed) FROM moves m WHERE m.acc=a.id AND m.date BETWEEN ? AND ? AND signed<0),0) Payments
        FROM accounts a ORDER BY a.id""", (s, s, e, s, e))
    df["Closing"] = df.Opening + df.Receipts - df.Payments
    return df


def tot(df, label="TOTAL"):
    df = df[["Particulars", "Amount"]]
    return pd.concat([df, pd.DataFrame([[label, float(df["Amount"].sum())]], columns=["Particulars", "Amount"])], ignore_index=True)


def balance_sheet(y):
    e, b = f"{y + 1}-03-31", books_start()
    s0 = f"{b}-04-01"
    cb = balances(e)

    def ht(ks):
        return float(head_totals(ks, s0, e)["Amount"].sum())

    oinv, ocorp = fget("opening_inv"), fget("opening_corpus")
    oassets = float(q("SELECT COALESCE(SUM(cost),0) v FROM assets WHERE is_opening=1").v[0])
    open_total = float(q("SELECT COALESCE(SUM(opening),0) v FROM accounts").v[0]) + oinv + oassets
    cumdep = sum(float(asset_year(yy)["Depreciation"].sum()) for yy in range(b, y + 1))
    corpus = ocorp + ht(("Corpus",))
    general = (open_total - ocorp) + ht(("Income",)) - ht(("Revenue",)) - cumdep
    L = pd.DataFrame([("Corpus Fund", corpus), ("General Fund (accumulated surplus)", general)], columns=["Particulars", "Amount"])
    A = [("Fixed assets (at WDV)", float(asset_year(y)["Closing WDV"].sum())),
         ("Investments / FDs (at cost)", oinv + ht(("Investment",)) - ht(("Realisation",)))]
    A += [(f"Cash / Bank - {n}", v) for n, v in zip(cb["name"], cb["bal"])]
    A = pd.DataFrame(A, columns=["Particulars", "Amount"])
    return tot(L), tot(A), float(L.Amount.sum() - A.Amount.sum())


def rp_ie(s, e, dep=0.0):
    """Receipts & Payments + Income & Expenditure for any date range."""
    ob, cb = balances(prev_day(s)), balances(e)
    rec = head_totals(("Income", "Corpus", "Realisation"), s, e)
    pay = head_totals(("Revenue", "Capital", "Investment"), s, e)
    t = totals(s, e, dep)
    P = lambda df: df[["Particulars", "Amount"]]
    rp_r = tot(pd.concat([pd.DataFrame({"Particulars": [f"Opening balance - {n}" for n in ob["name"]], "Amount": ob["bal"]}), P(rec)], ignore_index=True))
    rp_p = tot(pd.concat([P(pay), pd.DataFrame({"Particulars": [f"Closing balance - {n}" for n in cb["name"]], "Amount": cb["bal"]})], ignore_index=True))
    inc, exp = P(rec[rec.Kind == "Income"]), P(pay[pay.Kind == "Revenue"])
    if t["dep"]:
        exp = pd.concat([exp, pd.DataFrame([["Depreciation on fixed assets", t["dep"]]], columns=["Particulars", "Amount"])], ignore_index=True)
    bal_row = lambda lbl, v: pd.DataFrame([[lbl, abs(v)]], columns=["Particulars", "Amount"])
    if t["surplus"] >= 0:
        exp = pd.concat([exp, bal_row("Excess of income over expenditure (surplus)", t["surplus"])], ignore_index=True)
    else:
        inc = pd.concat([inc, bal_row("Excess of expenditure over income (deficit)", t["surplus"])], ignore_index=True)
    return dict(rp_r=rp_r, rp_p=rp_p, ie_e=tot(exp), ie_i=tot(inc), totals=t)


def donations_data(s, e):
    df = q("""SELECT COALESCE(d.name,t.party) "Donor name",d.pan PAN,d.address Address,
              CASE WHEN h.kind='Corpus' THEN 'Corpus' WHEN h.name LIKE '%Specific%' THEN 'Specific grant' ELSE 'Others' END "Donation type",
              t.mode "Mode",t.voucher_no "Receipt no",t.date Date,t.amount Amount
              FROM txns t JOIN heads h ON h.id=t.head_id LEFT JOIN donors d ON d.id=t.donor_id
              WHERE t.kind='RECEIPT' AND h.is_donation=1 AND t.cancelled=0 AND t.date BETWEEN ? AND ? ORDER BY t.date,t.id""", (s, e))
    df["Eligible"] = ["Yes" if is_eligible(m, a) else "No" for m, a in zip(df["Mode"], df["Amount"])]
    return df


def annual(y):
    s, e = fy_range(y)
    r = rp_ie(s, e, float(asset_year(y)["Depreciation"].sum()))
    t = r["totals"]
    bs_l, bs_a, diff = balance_sheet(y)
    applied, req = t["revenue"] + t["capital"], t["income"] * fget("apply_pct", 85.0) / 100
    app = pd.DataFrame([
        ("Gross income of the year (excluding corpus donations)", t["income"]),
        (f"Minimum application required @ {fget('apply_pct', 85.0):g}%", req),
        ("Revenue expenditure applied", t["revenue"]),
        ("Capital expenditure applied (assets / full cost)", t["capital"]),
        ("Total application of income", applied),
        ("Excess application / (shortfall) vs. requirement", applied - req),
        ("Income not applied - to be accumulated (Income - Application)", t["income"] - applied),
        ("Corpus donations received (not income)", t["corpus"]),
    ], columns=["Particulars", "Amount"])
    r.update(bs_l=bs_l, bs_a=bs_a, bs_diff=diff, app=app, app_pct=(applied / t["income"] * 100 if t["income"] else 0.0),
             fa=asset_year(y), d10=donations_data(s, e))
    return r


def compare():
    rows = []
    for y in range(books_start(), max(fy_of(dt.date.today()), books_start()) + 1):
        t = fy_totals(y)
        rows.append({"FY": fy_label(y), "Income": t["income"], "Corpus donations": t["corpus"], "Revenue exp.": t["revenue"],
                     "Capital exp.": t["capital"], "Depreciation": t["dep"], "Surplus / (Deficit)": t["surplus"], "Closing cash+bank": t["closing"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- exports
ONES = "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen".split()
TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _w(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " Hundred" + (" " + _w(n % 100) if n % 100 else "")
    for div, nm in ((10 ** 7, "Crore"), (10 ** 5, "Lakh"), (1000, "Thousand")):
        if n >= div:
            return _w(n // div) + " " + nm + (" " + _w(n % div) if n % div else "")


def words(a):
    r = int(a)
    p = int(round((a - r) * 100))
    return "Rupees " + _w(r) + (f" and {_w(p)} Paise" if p else "") + " Only"


def to_excel(sheets):
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as w:
        for n, df in sheets.items():
            df.to_excel(w, sheet_name=n[:31], index=False)
    return b.getvalue()


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return f"{v:,.2f}" if isinstance(v, float) else str(v)[:42]


# ---------------------------------------------------------------- logo / signature
def set_image(name, data):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.thumbnail((400, 400))
    b = io.BytesIO()
    im.convert("RGBA").save(b, "PNG")
    set_setting(f"img_{name}", base64.b64encode(b.getvalue()).decode())


def get_image(name):
    v = get_setting(f"img_{name}")
    return base64.b64decode(v) if v else None


# ---------------------------------------------------------------- tax-section wording & limits
def sec_name(date):
    """Donor-deduction section: Income-tax Act 2025 applies from 1 April 2026."""
    return ("Section 133 of the Income-tax Act, 2025 (corresponding to Section 80G of the Income-tax Act, 1961)"
            if str(date) >= "2026-04-01" else "Section 80G of the Income-tax Act, 1961")


def is_eligible(mode, amount):
    return not (mode == "Cash" and float(amount) > fget("cash_limit", 2000))


# ---------------------------------------------------------------- PDFs
def _head_elements(st):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, Table
    S = get_setting
    ctr = ParagraphStyle("ctr", parent=st["Normal"], alignment=1)
    ps = [Paragraph(f"<b>{esc(S('trust_name'))}</b>", st["Title"])]
    l1 = " | ".join(x for x in (f"Reg. No.: {S('reg_no')}" if S("reg_no") else "", f"PAN: {S('pan')}" if S("pan") else "") if x)
    l2 = " | ".join(x for x in (f"12A: {S('reg12a')}" if S("reg12a") else "", f"80G / Sec. 133 approval: {S('reg80g')}" if S("reg80g") else "") if x)
    ps += [Paragraph(esc(x), ctr) for x in (S("address"), l1, l2) if x]
    lg = get_image("logo")
    if not lg:
        return ps
    iw, ih = ImageReader(io.BytesIO(lg)).getSize()
    h = min(55, 75 * ih / iw)
    return [Table([[Image(io.BytesIO(lg), width=h * iw / ih, height=h), ps]], colWidths=[80, 440])]


def to_pdf(title, sections, landscape_=False):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    st, b = getSampleStyleSheet(), io.BytesIO()
    doc = SimpleDocTemplate(b, pagesize=landscape(A4) if landscape_ else A4, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    el = _head_elements(st) + [Paragraph(esc(title), st["Heading2"])]
    for head, df in sections:
        if head:
            el.append(Paragraph(esc(head), st["Heading4"]))
        num = [i for i, t in enumerate(df.dtypes) if pd.api.types.is_numeric_dtype(t)]
        data = [list(df.columns)] + [[_fmt(v) for v in r] for r in df.itertuples(index=False)]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")] +
                              [("ALIGN", (i, 0), (i, -1), "RIGHT") for i in num]))
        el += [t, Spacer(1, 10)]
    doc.build(el)
    return b.getvalue()


def receipt_pdf(tid):
    """Donation receipt. Layout is measured first, so the box always fits the content."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfgen import canvas
    t = q("""SELECT t.*,d.name dname,d.pan dpan,d.address daddr,h.name hname FROM txns t LEFT JOIN donors d ON d.id=t.donor_id
             LEFT JOIN heads h ON h.id=t.head_id WHERE t.id=?""", (tid,)).iloc[0]
    S = get_setting
    W, H = A4
    ops, off = [], [18]

    def line(font, size, x, s, align="l", lead=None):
        ops.append(("t", font, size, x, off[0] + size, s, align))
        off[0] += lead or size + 4

    def img(name, x, maxw, maxh, right=False):
        data = get_image(name)
        if not data:
            return 0
        ir = ImageReader(io.BytesIO(data))
        iw, ih = ir.getSize()
        sc = min(maxw / iw, maxh / ih)
        w, h = iw * sc, ih * sc
        ops.append(("i", ir, x - w if right else x, off[0], w, h))
        return h

    top0 = off[0]
    lh = img("logo", 45, 75, 65)
    line("Helvetica-Bold", 18, W / 2, S("trust_name"), "c", 24)
    for part in simpleSplit(S("address"), "Helvetica", 9, W - 230) if S("address") else []:
        line("Helvetica", 9, W / 2, part, "c", 12)
    l1 = "    ".join(x for x in (f"Reg. No.: {S('reg_no')}" if S("reg_no") else "", f"PAN: {S('pan')}" if S("pan") else "") if x)
    l2 = "    ".join(x for x in (f"12A: {S('reg12a')}" if S("reg12a") else "", f"80G / Sec. 133 approval: {S('reg80g')}" if S("reg80g") else "") if x)
    l3 = "    ".join(x for x in (f"Phone: {S('phone')}" if S("phone") else "", f"Email: {S('email')}" if S("email") else "") if x)
    for ln in (l1, l2, l3):
        for part in simpleSplit(ln, "Helvetica", 9, W - 230) if ln else []:
            line("Helvetica", 9, W / 2, part, "c", 12)
    off[0] = max(off[0], top0 + lh)
    off[0] += 6
    ops.append(("l", off[0]))
    off[0] += 10
    line("Helvetica-Bold", 13, W / 2, "DONATION RECEIPT", "c", 26)

    mode_, amt, cancelled = nz(t["mode"]), float(t["amount"]), bool(nz(t["cancelled"], 0))
    rows = [("Receipt No.", t["voucher_no"]), ("Date", pd.Timestamp(t["date"]).strftime("%d-%m-%Y")),
            ("Received with thanks from", nz(t["dname"]) or nz(t["party"]) or "Anonymous"), ("Address", t["daddr"]), ("PAN", t["dpan"]),
            ("Amount", f"Rs. {amt:,.2f}"), ("In words", words(amt)), ("Mode of payment", mode_),
            ("Cheque / UTR / Ref.", t["ref_no"]), ("Towards", t["hname"]), ("Purpose", t["purpose"]),
            ("Cancelled because", t["cancel_reason"] if cancelled else "")]
    for lbl, val in rows:
        val = str(nz(val))
        if not val:
            continue
        ops.append(("t", "Helvetica-Bold", 10, 50, off[0] + 10, lbl + ":", "l"))
        for part in simpleSplit(val, "Helvetica", 10, W - 250):
            line("Helvetica", 10, 200, part, "l", 14)
        off[0] += 4

    off[0] += 8
    if S("reg80g").strip():
        sec = sec_name(t["date"])
        if not is_eligible(mode_, amt):
            note = f"Note: Cash donation exceeding Rs. {fget('cash_limit', 2000):,.0f} is not eligible for deduction under {sec}."
        else:
            note = f"Donation is eligible for deduction under {sec}, subject to the provisions of the Act. Approval No.: {S('reg80g')}"
            if S("appr_valid").strip():
                note += f" (valid up to {S('appr_valid')})"
            note += "."
        for part in simpleSplit(note, "Helvetica-Oblique", 8, W - 100):
            line("Helvetica-Oblique", 8, 50, part, "l", 11)
    off[0] += 12
    line("Helvetica-Bold", 10, W - 50, f"For {S('trust_name')}", "r", 14)
    sh = img("signature", W - 50, 130, 45, right=True)
    off[0] += (sh + 4) if sh else 30
    line("Helvetica", 9, W - 50, S("signatory"), "r", 12)
    off[0] += 12

    total, top, b = off[0], H - 30, io.BytesIO()
    p = canvas.Canvas(b, pagesize=A4)
    p.rect(30, top - total, W - 60, total)
    for op in ops:
        if op[0] == "t":
            _, f, sz, x, o, s, al = op
            p.setFont(f, sz)
            {"l": p.drawString, "c": p.drawCentredString, "r": p.drawRightString}[al](x, top - o, s)
        elif op[0] == "i":
            _, ir, x, o, w, h = op
            p.drawImage(ir, x, top - o - h, w, h, mask="auto")
        else:
            p.line(30, top - op[1], W - 30, top - op[1])
    if cancelled:
        p.saveState()
        p.setFillColorRGB(.8, .1, .1)
        p.setFillAlpha(.25)
        p.setFont("Helvetica-Bold", 70)
        p.translate(W / 2, top - total / 2)
        p.rotate(30)
        p.drawCentredString(0, 0, "CANCELLED")
        p.restoreState()
    p.showPage()
    p.save()
    return b.getvalue()


def donor_certificate_pdf(did, s, e):
    """Donation certificate for one donor for a period. Returns None if no donations."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    d = q("SELECT * FROM donors WHERE id=?", (did,)).iloc[0]
    df = q("""SELECT t.date Date,t.voucher_no "Receipt No.",h.name Head,t.mode Mode,t.amount Amount FROM txns t JOIN heads h ON h.id=t.head_id
              WHERE t.donor_id=? AND t.kind='RECEIPT' AND h.is_donation=1 AND t.cancelled=0 AND t.date BETWEEN ? AND ? ORDER BY t.date,t.id""", (did, s, e))
    if df.empty:
        return None
    df["Eligible"] = ["Yes" if is_eligible(m, a) else "No" for m, a in zip(df.Mode, df.Amount)]
    total, elig = float(df.Amount.sum()), float(df[df.Eligible == "Yes"].Amount.sum())
    st, b = getSampleStyleSheet(), io.BytesIO()
    doc = SimpleDocTemplate(b, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=36)
    fd = lambda x: pd.Timestamp(x).strftime("%d-%m-%Y")
    S = get_setting
    who = f"<b>{esc(d['name'])}</b>" + (f" (PAN: {esc(d['pan'])})" if nz(d["pan"]) else "") + (f", {esc(d['address'])}," if nz(d["address"]) else "")
    el = _head_elements(st) + [Spacer(1, 10), Paragraph("<b>DONATION CERTIFICATE</b>", ParagraphStyle("h", parent=st["Heading2"], alignment=1)), Spacer(1, 6),
          Paragraph(f"This is to certify that {who} has donated a total sum of <b>Rs. {total:,.2f}</b> ({esc(words(total))}) to "
                    f"{esc(S('trust_name'))} during the period {fd(s)} to {fd(e)}, as per the details below.", st["BodyText"]), Spacer(1, 8)]
    data = [["Date", "Receipt No.", "Head", "Mode", "Amount (Rs.)", "Eligible"]]
    data += [[fd(r[0]), r[1], r[2], r[3], f"{r[4]:,.2f}", r[5]] for r in df.values.tolist()]
    data.append(["", "", "", "TOTAL", f"{total:,.2f}", ""])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                           ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (4, 0), (4, -1), "RIGHT")]))
    note = (f"Donations eligible for deduction under {sec_name(e)}: Rs. {elig:,.2f}. Cash donations above Rs. {fget('cash_limit', 2000):,.0f} are not eligible. "
            f"The deduction is subject to the donor's tax regime and the conditions of the Act." + (f" Approval No.: {S('reg80g')}." if S("reg80g") else ""))
    right = ParagraphStyle("r", parent=st["Normal"], alignment=2)
    el += [t, Spacer(1, 10), Paragraph(esc(note), st["Italic"]), Spacer(1, 18), Paragraph(f"<b>For {esc(S('trust_name'))}</b>", right)]
    sg = get_image("signature")
    if sg:
        iw, ih = ImageReader(io.BytesIO(sg)).getSize()
        h = min(45, 130 * ih / iw)
        im = Image(io.BytesIO(sg), width=h * iw / ih, height=h)
        im.hAlign = "RIGHT"
        el.append(im)
    else:
        el.append(Spacer(1, 30))
    el += [Paragraph(esc(S("signatory")), right), Paragraph(f"Date: {dt.date.today():%d-%m-%Y}", st["Normal"])]
    doc.build(el)
    return b.getvalue()


def certificates_zip(s, e):
    ids = q("""SELECT DISTINCT t.donor_id i FROM txns t JOIN heads h ON h.id=t.head_id WHERE t.donor_id IS NOT NULL AND h.is_donation=1
               AND t.cancelled=0 AND t.date BETWEEN ? AND ?""", (s, e)).i.tolist()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for i in ids:
            nm = q("SELECT name FROM donors WHERE id=?", (int(i),)).name[0]
            z.writestr(f"{re.sub(r'[^A-Za-z0-9]+', '_', nm)}_{int(i)}.pdf", donor_certificate_pdf(int(i), s, e))
    return b.getvalue(), len(ids)


# ---------------------------------------------------------------- bank statement import helpers
def statement_rows(df, dc, nc, rc, dbc, crc):
    num = lambda s: pd.to_numeric(s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce").fillna(0.0).abs()
    out = pd.DataFrame({
        "Date": pd.to_datetime(df[dc], dayfirst=True, errors="coerce", format="mixed"),
        "Narration": df[nc].fillna("").astype(str) if nc != "(none)" else "",
        "Ref": df[rc].fillna("").astype(str) if rc != "(none)" else "",
        "Debit": num(df[dbc]) if dbc != "(none)" else 0.0,
        "Credit": num(df[crc]) if crc != "(none)" else 0.0})
    return out[out.Date.notna() & ((out.Debit > 0) | (out.Credit > 0))].reset_index(drop=True)


def guess_mode(n):
    u = n.upper()
    for key, m in (("UPI", "UPI"), ("NEFT", "NEFT/RTGS"), ("RTGS", "NEFT/RTGS"), ("IMPS", "NEFT/RTGS"),
                   ("CHQ", "Cheque"), ("CHEQUE", "Cheque"), ("CLG", "Cheque"), ("CASH", "Cash")):
        if key in u:
            return m
    return "Other"


def guess_head(n, credit, dflt):
    u = n.upper()
    if credit and any(k in u for k in ("INTEREST", "INT.PD", "INT PD", "INT.CR")):
        return "Interest - Savings Bank"
    if not credit and any(k in u for k in ("CHARGES", "CHRG", "SMS CHG")):
        return "Bank Charges"
    return dflt


def existing_keys(aid):
    r = q("SELECT date,amount,kind,COALESCE(ref_no,'') ref FROM txns WHERE account_id=? AND cancelled=0 AND kind IN ('RECEIPT','PAYMENT')", (aid,))
    return {(a, round(b, 2), k, x.strip()) for a, b, k, x in zip(r.date, r.amount, r.kind, r.ref)}
