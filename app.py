"""Unihope Foundation - Trust Accounting Software (Streamlit UI). Run with run.bat"""
import datetime as dt
import re
import sqlite3

import pandas as pd
import streamlit as st

import core as c

st.set_page_config(page_title="Unihope Foundation - Accounts", page_icon="🏛️", layout="wide")


@st.cache_resource
def _boot():
    c.init_db()
    c.backup()
    return True


_boot()
nz = c.nz
MODES = ["Cash", "Cheque", "UPI", "NEFT/RTGS", "Online/Card", "DD", "Other"]
KINDS = ["Income", "Corpus", "Realisation", "Revenue", "Capital", "Investment"]
MENU = ["📊 Dashboard", "🎁 Donations Register", "💰 Other Receipts", "📤 Application Register (Payments)", "🔁 Transfers (Bank/Cash)",
        "🏦 Bank Ledger & BRS", "👥 Donors", "🏢 Fixed Assets", "📑 Year-end Reports", "⚙️ Settings"]
inr = lambda v: f"₹ {v:,.2f}"
idx = lambda lst, v: lst.index(v) if v in lst else 0
slug = lambda s: re.sub(r"\W+", "_", s)


def flash(msg, warn=False):
    st.session_state["flash"] = (msg, warn)
    st.rerun()


def show(df, **kw):
    cfg = {col: st.column_config.NumberColumn(format="%.2f") for col in df.columns if pd.api.types.is_float_dtype(df[col])}
    st.dataframe(df, hide_index=True, width="stretch", column_config=cfg, **kw)


# ------------------------------------------------------------------ login
if c.get_setting("pw_hash") and not st.session_state.get("ok"):
    st.title(f"🏛️ {c.get_setting('trust_name')}")
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if c.check_pw(pw):
            st.session_state["ok"] = True
            st.rerun()
        st.error("Wrong password")
    st.stop()

# ------------------------------------------------------------------ sidebar: menu + period
_logo = c.get_image("logo")
if _logo:
    st.sidebar.image(_logo, width=90)
st.sidebar.markdown(f"### {c.get_setting('trust_name')}")
page = st.sidebar.radio("Menu", MENU)
st.sidebar.divider()
cur, bs = c.fy_of(dt.date.today()), c.books_start()
pm = st.sidebar.radio("Period", ["Financial year", "Custom dates"], horizontal=True)
if pm == "Financial year":
    years = list(range(min(bs, cur), max(cur, bs) + 2))
    fy = st.sidebar.selectbox("Financial Year", years, index=years.index(cur) if cur in years else 0, format_func=c.fy_label)
    S_, E_ = c.fy_range(fy)
    custom, PERIOD = False, f"FY {c.fy_label(fy)}"
else:
    d1 = st.sidebar.date_input("From", pd.Timestamp(f"{cur}-04-01").date(), format="DD/MM/YYYY")
    d2 = st.sidebar.date_input("To", dt.date.today(), format="DD/MM/YYYY")
    if d1 > d2:
        st.sidebar.error("'From' date must be before 'To' date.")
        st.stop()
    S_, E_, fy, custom = str(d1), str(d2), c.fy_of(d2), True
    PERIOD = f"{d1:%d-%m-%Y} to {d2:%d-%m-%Y}"
if "flash" in st.session_state:
    _m, _w = st.session_state.pop("flash")
    (st.warning if _w else st.success)(_m)


# ------------------------------------------------------------------ entry form (receipt / payment / transfer)
def txn_form(kind, donation=False, tid=None):
    row = c.q("SELECT * FROM txns WHERE id=?", (tid,)).iloc[0].to_dict() if tid else {}
    g = lambda k, d="": nz(row.get(k), d)
    ac = c.q("SELECT id,name FROM accounts WHERE active=1 OR id IN (?,?)", (int(g("account_id", 0)), int(g("to_account_id", 0))))
    A = dict(zip(ac.id.tolist(), ac.name.tolist()))
    ids = list(A)
    if kind == "RECEIPT":
        hd = c.q("SELECT id,name FROM heads WHERE is_donation=? AND kind IN ('Income','Corpus','Realisation') ORDER BY id", (int(donation),))
    elif kind == "PAYMENT":
        hd = c.q("SELECT id,name FROM heads WHERE kind IN ('Revenue','Capital','Investment') ORDER BY id")
    ast = c.q("SELECT name,rate FROM assets WHERE txn_id=?", (tid,)) if tid else pd.DataFrame()
    dflt = min(max(dt.date.today(), pd.Timestamp(S_).date()), pd.Timestamp(E_).date())
    with st.form(f"f{kind}{int(donation)}{tid}", clear_on_submit=not tid):
        a, b, cc = st.columns(3)
        date = a.date_input("Date", pd.Timestamp(g("date", dflt)).date(), format="DD/MM/YYYY")
        amt = b.number_input("Amount (₹)", 0.0, value=float(g("amount", 0.0)), step=100.0, format="%.2f")
        acc = cc.selectbox("Deposited in account" if kind == "RECEIPT" else "From account", ids,
                           index=idx(ids, int(g("account_id", ids[0]))), format_func=A.get)
        a2, b2, c2 = st.columns(3)
        d = dict(kind=kind, date=str(date), amount=amt, account_id=acc)
        if kind == "TRANSFER":
            d["to_account_id"] = a2.selectbox("To account", ids, index=idx(ids, int(g("to_account_id", ids[-1]))), format_func=A.get)
            d["mode"] = "Transfer"
        else:
            H = dict(zip(hd.id.tolist(), hd.name.tolist()))
            d["head_id"] = a2.selectbox("Head / Category", list(H), index=idx(list(H), int(g("head_id", 0))), format_func=H.get)
            d["mode"] = b2.selectbox("Mode", MODES, index=idx(MODES, g("mode", "Cash")))
            if donation:
                dn = c.q("SELECT id,name,pan FROM donors ORDER BY name")
                D = {0: "- Not in donor list / anonymous -"} | {r.id: f"{r.name} ({nz(r.pan) or 'no PAN'})" for r in dn.itertuples()}
                d["donor_id"] = c2.selectbox("Donor", list(D), index=idx(list(D), int(g("donor_id", 0))), format_func=D.get) or None
            d["party"] = st.text_input({"RECEIPT": "Donor / received from (if not in donor list)", "PAYMENT": "Paid to (payee)"}[kind], g("party"))
        d["ref_no"] = st.text_input("Cheque no. / UTR / Bill no.", g("ref_no"))
        if kind == "PAYMENT":
            d["purpose"] = st.text_input("Purpose / Project / Programme", g("purpose"))
            x, y = st.columns(2)
            d["asset_name"] = x.text_input("Asset name (only for 'Purchase of Fixed Assets')", ast.name[0] if len(ast) else "")
            d["rate"] = y.number_input("Depreciation rate % (assets only)", 0.0, 100.0, float(ast.rate[0]) if len(ast) else 15.0,
                                       help="Buildings 10, Furniture 10, Plant & machinery 15, Vehicles 15, Computers 40")
        elif kind == "RECEIPT":
            d["purpose"] = st.text_input("Purpose / Remarks printed on receipt", g("purpose"))
        d["remarks"] = st.text_input("Internal remarks", g("remarks"))
        if st.form_submit_button("💾 Save changes" if tid else "💾 Save entry", type="primary"):
            try:
                c.save_txn(d, tid)
            except ValueError as err:
                st.error(str(err))
            else:
                warn = ""
                if kind == "RECEIPT" and donation and not c.is_eligible(d["mode"], amt):
                    warn = f" ⚠ Cash donation above ₹{c.fget('cash_limit', 2000):,.0f} is NOT eligible for deduction - the receipt will say so."
                if kind == "PAYMENT" and d["mode"] == "Cash" and amt > c.fget("cash_pay_limit", 10000):
                    warn = f" ⚠ Cash payment above ₹{c.fget('cash_pay_limit', 10000):,.0f} may not be allowed as expenditure - please check with your CA."
                flash("Saved successfully." + warn, bool(warn))


def reg_df(kind, donation=None):
    sql = """SELECT t.id,t.voucher_no Voucher,t.date Date,COALESCE(d.name,NULLIF(t.party,''),a2.name,'') Party,COALESCE(h.name,'Transfer') Head,
             a.name Account,t.mode Mode,t.ref_no Ref,t.purpose Purpose,t.amount Amount,t.remarks Remarks,
             CASE t.cancelled WHEN 1 THEN 'CANCELLED' ELSE '' END Status,t.cancelled,t.cancel_reason
             FROM txns t LEFT JOIN heads h ON h.id=t.head_id LEFT JOIN donors d ON d.id=t.donor_id
             LEFT JOIN accounts a ON a.id=t.account_id LEFT JOIN accounts a2 ON a2.id=t.to_account_id
             WHERE t.kind=? AND t.date BETWEEN ? AND ?"""
    p = [kind, S_, E_]
    if donation is not None:
        sql += " AND h.is_donation=?"
        p.append(int(donation))
    return c.q(sql + " ORDER BY t.date,t.id", p)


def register(title, kind, donation=None):
    st.header(f"{title} - {PERIOD}")
    with st.expander("➕ Add new entry"):
        txn_form(kind, bool(donation))
    df = reg_df(kind, donation)
    if donation:
        df["Eligible"] = ["Yes" if c.is_eligible(m, a) else "No" for m, a in zip(df.Mode, df.Amount)]
    f = st.text_input("🔍 Search this register")
    if f:
        df = df[df.drop(columns="id").astype(str).apply(lambda r: r.str.contains(f, case=False, regex=False).any(), axis=1)]
    view = df.drop(columns=["id", "cancelled", "cancel_reason"])
    show(view)
    act = df[df.cancelled == 0]
    st.metric(f"Total ({len(act)} active entries; cancelled entries excluded)", inr(act.Amount.sum()))
    fname = slug(title) + "_" + slug(PERIOD)
    x1, x2 = st.columns(2)
    x1.download_button("⬇ Excel", c.to_excel({title: view}), fname + ".xlsx")
    x2.download_button("⬇ PDF", c.to_pdf(f"{title} - {PERIOD}", [("", view.drop(columns=["Remarks", "Purpose"]))], True), fname + ".pdf")
    if df.empty:
        return
    with st.expander("✏️ Edit / cancel" + (" / print receipt" if donation else "")):
        L = {r.id: f"{r.Voucher} | {r.Date} | {r.Party} | {inr(r.Amount)}" + (" | CANCELLED" if r.cancelled else "") for r in df.itertuples()}
        sel = st.selectbox("Select entry", list(L), format_func=L.get, key=f"sel{kind}{donation}")
        row = df[df.id == sel].iloc[0]
        if donation:
            st.download_button("🧾 Download receipt PDF", c.receipt_pdf(sel), L[sel].split(" |")[0].replace("/", "-") + ".pdf")
        if row.cancelled:
            st.info(f"This entry is cancelled (number retained). Reason: {nz(row.cancel_reason)}")
        else:
            txn_form(kind, bool(donation), sel)
            st.markdown("**Cancel this entry** - the number stays in the register for audit; it no longer counts in any account or report.")
            reason = st.text_input("Reason for cancellation", key=f"cr{sel}")
            if st.button("🚫 Cancel this entry", key=f"cb{sel}"):
                if not reason.strip():
                    st.error("Please enter a reason.")
                else:
                    c.cancel_txn(sel, reason.strip())
                    flash("Entry cancelled.")


# ------------------------------------------------------------------ pages
def dashboard():
    st.header(f"Dashboard - {PERIOD}")
    t = c.totals(S_, E_) if custom else c.fy_totals(fy)
    m = st.columns(5)
    m[0].metric("Income (non-corpus)", inr(t["income"]))
    m[1].metric("Corpus donations", inr(t["corpus"]))
    m[2].metric("Revenue expenditure", inr(t["revenue"]))
    m[3].metric("Capital expenditure", inr(t["capital"]))
    m[4].metric("Surplus / (Deficit)", inr(t["surplus"]))
    if custom:
        st.caption("Surplus for custom dates is before depreciation.")
    l, r = st.columns(2)
    with l:
        st.subheader("Cash & bank balances")
        b = c.balances(E_ if E_ <= str(dt.date.today()) else str(dt.date.today()))
        show(b[["name", "type", "bal"]].rename(columns={"name": "Account", "type": "Type", "bal": "Balance"}).astype({"Balance": float}))
        st.metric("Total balance", inr(b.bal.sum()))
    with r:
        st.subheader("Monthly receipts vs payments")
        mm = c.q("""SELECT substr(date,1,7) Month,SUM(CASE WHEN kind='RECEIPT' THEN amount ELSE 0 END) Receipts,
                    SUM(CASE WHEN kind='PAYMENT' THEN amount ELSE 0 END) Payments FROM txns WHERE cancelled=0 AND date BETWEEN ? AND ?
                    GROUP BY 1 ORDER BY 1""", (S_, E_))
        if mm.empty:
            st.info("No entries yet for this period. Start with Settings → add bank accounts and opening balances.")
        else:
            st.bar_chart(mm.set_index("Month"))
    st.subheader("Recent entries")
    show(c.q("SELECT voucher_no Voucher,date Date,kind Type,amount Amount FROM txns WHERE cancelled=0 ORDER BY id DESC LIMIT 8"))


def bank():
    st.header(f"Bank / Cash Ledger & Reconciliation - {PERIOD}")
    ac = c.q("SELECT id,name FROM accounts ORDER BY id")
    A = dict(zip(ac.id.tolist(), ac.name.tolist()))
    aid = st.selectbox("Account", list(A), format_func=A.get)
    t1, t2, t3 = st.tabs(["Ledger", "Bank Reconciliation (BRS)", "📥 Import bank statement (Excel/CSV)"])
    with t1:
        sm = c.account_summary(S_, E_)
        st.subheader("Closing balance of every account")
        show(sm.drop(columns="id"))
        st.metric("Total closing balance (all accounts)", inr(float(sm.Closing.sum())))
        opening = float(sm.set_index("id").loc[aid, "Opening"])
        st.subheader(f"Ledger - {A[aid]}")
        L = c.q("""SELECT m.date Date,m.voucher_no Voucher,m.kind Type,COALESCE(h.name,'Transfer') Head,COALESCE(d.name,NULLIF(m.party,''),'') Party,
                   m.ref_no Ref,m.signed FROM moves m LEFT JOIN heads h ON h.id=m.head_id LEFT JOIN donors d ON d.id=m.donor_id
                   WHERE m.acc=? AND m.date BETWEEN ? AND ? ORDER BY m.date,m.id""", (aid, S_, E_))
        L["Receipt"] = L.signed.clip(lower=0)
        L["Payment"] = (-L.signed).clip(lower=0)
        L["Closing Balance"] = opening + L.signed.cumsum()
        top = pd.DataFrame([{"Date": S_, "Head": "Opening balance", "Closing Balance": opening}])
        L = pd.concat([top, L.drop(columns="signed")], ignore_index=True)
        show(L)
        st.download_button("⬇ Excel", c.to_excel({"Ledger": L}), f"ledger_{slug(A[aid])}.xlsx")
    with t2:
        asof = st.date_input("Reconcile as on", pd.Timestamp(E_).date(), format="DD/MM/YYYY")
        U = c.q("""SELECT m.id,m.side,m.date Date,m.voucher_no Voucher,m.kind Type,m.ref_no Ref,m.signed Amount,m.cleared "Cleared on"
                   FROM moves m WHERE m.acc=? AND m.date<=? ORDER BY m.date,m.id""", (aid, str(asof)))
        U = U[U["Cleared on"].isna() | (U["Cleared on"] > str(asof))].reset_index(drop=True)
        U["Cleared on"] = pd.to_datetime(U["Cleared on"])
        st.caption("Enter the date shown in the bank statement against each entry that has cleared, then save. Entries imported from a bank statement are already marked cleared.")
        ed = st.data_editor(U, hide_index=True, width="stretch", key=f"brs{aid}{asof}",
                            disabled=[x for x in U.columns if x != "Cleared on"],
                            column_config={"id": None, "side": None, "Amount": st.column_config.NumberColumn(format="%.2f"),
                                           "Cleared on": st.column_config.DateColumn("Cleared on", format="DD/MM/YYYY")})
        if st.button("💾 Save clearing dates"):
            for i in U.index:
                n = ed.loc[i, "Cleared on"]
                if not pd.isna(n):
                    col = "cleared_date" if U.loc[i, "side"] == "A" else "cleared_date2"
                    c.ex(f"UPDATE txns SET {col}=? WHERE id=?", (str(pd.Timestamp(n).date()), int(U.loc[i, "id"])))
            flash("Clearing dates saved.")
        books = float(c.balances(str(asof)).set_index("id").loc[aid, "bal"])
        dep, chq = float(U[U.Amount > 0].Amount.sum()), float(-U[U.Amount < 0].Amount.sum())
        show(pd.DataFrame([("Balance as per books", books), ("Less: deposits / receipts not yet credited by bank", dep),
                           ("Add: cheques / payments issued but not yet debited", chq),
                           ("Balance as per bank (calculated)", books - dep + chq)], columns=["Particulars", "Amount"]))
        bb = st.number_input("Balance as per bank statement / passbook", value=0.0, format="%.2f")
        diff = bb - (books - dep + chq)
        (st.success if abs(diff) < 0.5 else st.warning)(f"Difference: {inr(diff)}" + ("  ✅ Reconciled" if abs(diff) < 0.5 else ""))
    with t3:
        bank_import(aid, A[aid])


def bank_import(aid, aname):
    st.caption(f"Upload the bank statement as Excel or CSV. Rows are added to **{aname}** as receipts (credits) and payments (debits), "
               "marked as cleared, and rows already in the books are skipped.")
    up = st.file_uploader("Bank statement (.xlsx, .xls, .csv)", type=["xlsx", "xls", "csv"], key=f"imp{aid}")
    if not up:
        return
    try:
        raw = pd.read_csv(up, header=None, dtype=str) if up.name.lower().endswith(".csv") else pd.read_excel(up, header=None, dtype=str)
    except Exception as err:
        st.error(f"Could not read the file: {err}")
        return
    guess_row = next((i for i, r in raw.iterrows() if any("date" in str(x).lower() for x in r.values)), 0)
    hrow = int(st.number_input("Header row (row number in the file that holds the column names)", 1, max(len(raw), 1), guess_row + 1)) - 1
    df = raw.iloc[hrow + 1:].copy()
    df.columns = [str(x).strip() if str(x) != "nan" else f"col{i}" for i, x in enumerate(raw.iloc[hrow])]
    cols = ["(none)"] + list(df.columns)
    pick = lambda *keys: cols.index(next((x for x in cols if any(k in x.lower() for k in keys)), "(none)"))
    k = st.columns(5)
    dc = k[0].selectbox("Date column", cols, pick("date"))
    nc = k[1].selectbox("Narration column", cols, pick("narration", "description", "particular", "details"))
    rc = k[2].selectbox("Reference / cheque no.", cols, pick("ref", "chq", "cheque", "utr"))
    dbc = k[3].selectbox("Debit (withdrawal)", cols, pick("debit", "withdraw", "paid out"))
    crc = k[4].selectbox("Credit (deposit)", cols, pick("credit", "deposit", "paid in"))
    if dc == "(none)" or (dbc == "(none)" and crc == "(none)"):
        st.info("Choose the date column and at least one of debit / credit.")
        return
    out = c.statement_rows(df, dc, nc, rc, dbc, crc)
    if out.empty:
        st.warning("No transactions found with the chosen columns.")
        return
    heads = c.q("SELECT id,name,kind FROM heads WHERE kind<>'Capital' ORDER BY id")
    H = {r.name: (r.id, r.kind) for r in heads.itertuples()}
    cred_names = [n for n, v in H.items() if v[1] in ("Income", "Corpus", "Realisation")]
    deb_names = [n for n, v in H.items() if v[1] in ("Revenue", "Investment")]
    h1, h2 = st.columns(2)
    dcred = h1.selectbox("Default head for deposits", cred_names, idx(cred_names, "Donation - General"))
    ddeb = h2.selectbox("Default head for withdrawals", deb_names, idx(deb_names, "Miscellaneous Expenses"))
    have = c.existing_keys(aid)
    out["Head"] = [c.guess_head(n, cr > 0, dcred if cr > 0 else ddeb) for n, cr in zip(out.Narration, out.Credit)]
    out["Duplicate"] = [(str(d.date()), round(float(cr if cr > 0 else db), 2), "RECEIPT" if cr > 0 else "PAYMENT", rf.strip()) in have
                        for d, cr, db, rf in zip(out.Date, out.Credit, out.Debit, out.Ref)]
    out["Import"] = ~out["Duplicate"]
    ed = st.data_editor(out, hide_index=True, width="stretch", key=f"imped{aid}{up.name}", disabled=["Date", "Narration", "Ref", "Debit", "Credit", "Duplicate"],
                        column_config={"Head": st.column_config.SelectboxColumn("Head", options=cred_names + deb_names, required=True),
                                       "Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                       "Debit": st.column_config.NumberColumn(format="%.2f"), "Credit": st.column_config.NumberColumn(format="%.2f")})
    st.caption(f"{len(out)} rows read, {int(out.Duplicate.sum())} already in the books. Check the Head for each row, untick any row you do not want.")
    if st.button("📥 Import ticked rows", type="primary"):
        ok, errs = 0, []
        for i, r in ed[ed.Import].iterrows():
            cr = r.Credit > 0
            hid, hk = H.get(r.Head, (None, None))
            if hk is None or (cr and hk not in ("Income", "Corpus", "Realisation")) or (not cr and hk not in ("Revenue", "Investment")):
                errs.append(f"row {i + 1}: head '{r.Head}' does not suit a {'deposit' if cr else 'withdrawal'}")
                continue
            day = str(pd.Timestamp(r.Date).date())
            try:
                c.save_txn(dict(kind="RECEIPT" if cr else "PAYMENT", date=day, amount=float(r.Credit if cr else r.Debit), account_id=aid, head_id=hid,
                                party=str(r.Narration)[:80], mode=c.guess_mode(str(r.Narration)), ref_no=str(r.Ref), remarks="Imported from bank statement",
                                cleared_date=day))
                ok += 1
            except ValueError as err:
                errs.append(f"row {i + 1}: {err}")
        flash(f"Imported {ok} entries." + (f" {len(errs)} skipped - " + "; ".join(errs[:3]) if errs else ""), bool(errs))


def donors():
    st.header("Donors")
    D = c.q("SELECT * FROM donors ORDER BY name")
    L = {0: "➕ New donor"} | {r.id: f"{r.name} ({nz(r.pan) or 'no PAN'})" for r in D.itertuples()}
    sel = st.selectbox("Select donor to edit, or add new", list(L), format_func=L.get)
    row = D[D.id == sel].iloc[0].to_dict() if sel else {}
    with st.form(f"donor{sel}", clear_on_submit=not sel):
        name = st.text_input("Name *", nz(row.get("name")))
        pan = st.text_input("PAN (needed for 80G / Section 133 / Form 10BD)", nz(row.get("pan"))).upper().strip()
        addr = st.text_area("Address", nz(row.get("address")))
        p1, p2 = st.columns(2)
        ph, em = p1.text_input("Phone", nz(row.get("phone"))), p2.text_input("Email", nz(row.get("email")))
        if st.form_submit_button("💾 Save donor", type="primary"):
            if not name.strip():
                st.error("Name is required.")
            elif pan and not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan):
                st.error("PAN format looks wrong (expected like ABCDE1234F).")
            elif sel:
                c.ex("UPDATE donors SET name=?,pan=?,address=?,phone=?,email=? WHERE id=?", (name.strip(), pan, addr, ph, em, sel))
                flash("Donor updated.")
            else:
                c.ex("INSERT INTO donors(name,pan,address,phone,email) VALUES(?,?,?,?,?)", (name.strip(), pan, addr, ph, em))
                flash("Donor added.")
    if sel:
        st.subheader("Donation history (all years)")
        h = c.q("""SELECT t.date Date,t.voucher_no Receipt,h.name Head,t.mode Mode,t.amount Amount FROM txns t JOIN heads h ON h.id=t.head_id
                   WHERE t.donor_id=? AND t.cancelled=0 ORDER BY t.date""", (sel,))
        show(h)
        st.metric("Total donated (all years)", inr(h.Amount.sum()))
        cert = c.donor_certificate_pdf(sel, S_, E_)
        if cert:
            st.download_button(f"📜 Donation certificate - {PERIOD}", cert, f"Certificate_{slug(row['name'])}_{slug(PERIOD)}.pdf")
        else:
            st.caption(f"No donations from this donor in {PERIOD}, so no certificate for this period.")
    else:
        show(D.drop(columns="id").rename(columns=str.title))


def assets():
    st.header(f"Fixed Assets - FY {c.fy_label(fy)}")
    sch = c.asset_year(fy)
    show(sch)
    st.metric("Closing WDV of fixed assets", inr(float(sch["Closing WDV"].sum())))
    st.caption("Assets bought through a 'Purchase of Fixed Assets' payment appear here automatically. Depreciation uses the WDV method "
               "(half rate if put to use after 2 October). Under the Income-tax Act the full cost of capital expenditure counts as application of income.")
    reg = c.q("SELECT id,name Asset,purchase_date 'Purchase date',cost Cost,rate 'Rate %',CASE is_opening WHEN 1 THEN 'Opening' ELSE 'Purchased' END Source FROM assets")
    with st.expander("📋 Asset register / edit"):
        show(reg.drop(columns="id"))
        if not reg.empty:
            L = {r.id: f"{r.Asset} ({r.Source})" for r in reg.itertuples()}
            sel = st.selectbox("Select asset", list(L), format_func=L.get)
            r = reg[reg.id == sel].iloc[0]
            with st.form(f"as{sel}"):
                nm, rt = st.text_input("Name", r.Asset), st.number_input("Rate %", 0.0, 100.0, float(r["Rate %"]))
                if st.form_submit_button("Save"):
                    c.ex("UPDATE assets SET name=?,rate=? WHERE id=?", (nm, rt, sel))
                    flash("Asset updated.")
            if r.Source == "Opening" and st.button("Delete opening asset"):
                c.ex("DELETE FROM assets WHERE id=?", (sel,))
                flash("Deleted.")
    with st.expander("➕ Add opening asset (owned before your books start)"):
        with st.form("openasset", clear_on_submit=True):
            n, w, rt = st.text_input("Asset name"), st.number_input(f"WDV on 1 April {bs}", 0.0, step=1000.0), st.number_input("Rate %", 0.0, 100.0, 15.0)
            if st.form_submit_button("Add") and n and w > 0:
                c.ex("INSERT INTO assets(name,cost,rate,is_opening) VALUES(?,?,?,1)", (n, w, rt))
                flash("Opening asset added.")


def reports():
    st.header(f"Reports - {PERIOD}")
    if custom:
        st.info("Custom dates show Receipts & Payments, Income & Expenditure (before depreciation) and Donations. "
                "The Balance Sheet, Application of Income and Fixed Asset schedule need a full financial year - switch Period to 'Financial year'.")
        r = c.rp_ie(S_, E_)
        r["d10"] = c.donations_data(S_, E_)
    else:
        r = c.annual(fy)
    sheets = {"Receipts": r["rp_r"], "Payments": r["rp_p"], "Expenditure": r["ie_e"], "Income": r["ie_i"], "Donations": r["d10"]}
    secs = [("Receipts and Payments Account - Receipts", r["rp_r"]), ("Receipts and Payments Account - Payments", r["rp_p"]),
            ("Income and Expenditure Account - Expenditure", r["ie_e"]), ("Income and Expenditure Account - Income", r["ie_i"])]
    if not custom:
        sheets.update({"Liabilities": r["bs_l"], "Assets": r["bs_a"], "Application of Income": r["app"], "Fixed Assets": r["fa"]})
        secs += [("Balance Sheet - Funds & Liabilities", r["bs_l"]), ("Balance Sheet - Assets", r["bs_a"]),
                 ("Application of Income", r["app"]), ("Fixed Asset Schedule", r["fa"])]
    tag = slug(PERIOD)
    ptitle = f"Accounts for the year ended 31 March {fy + 1}" if not custom else f"Accounts for the period {PERIOD}"
    x1, x2 = st.columns(2)
    x1.download_button("⬇ Complete report - Excel", c.to_excel(sheets), f"Unihope_Reports_{tag}.xlsx")
    x2.download_button("⬇ Complete report - PDF", c.to_pdf(ptitle, secs), f"Unihope_Reports_{tag}.pdf")
    names = ["Receipts & Payments", "Income & Expenditure", "Donations (10BD data)"] + ([] if custom else ["Balance Sheet", "Application of Income", "Fixed Assets", "Year comparison"])
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Receipts & Payments"]:
        l, rr = st.columns(2)
        with l:
            st.subheader("Receipts")
            show(r["rp_r"])
        with rr:
            st.subheader("Payments")
            show(r["rp_p"])
    with tabs["Income & Expenditure"]:
        l, rr = st.columns(2)
        with l:
            st.subheader("Expenditure")
            show(r["ie_e"])
        with rr:
            st.subheader("Income")
            show(r["ie_i"])
    with tabs["Donations (10BD data)"]:
        d10 = r["d10"]
        show(d10)
        m = st.columns(2)
        m[0].metric("Total donations", inr(float(d10.Amount.sum())))
        m[1].metric("Eligible for deduction", inr(float(d10[d10.Eligible == "Yes"].Amount.sum())))
        missing = d10[d10.PAN.isna() | (d10.PAN == "")]
        if len(missing):
            st.warning(f"{len(missing)} donation(s) have no donor PAN - add PAN in the Donors page where available.")
        key = f"certzip{S_}{E_}"
        if st.button("📜 Prepare donor certificates for all donors (ZIP)"):
            st.session_state[key] = c.certificates_zip(S_, E_)
        if key in st.session_state:
            z, n = st.session_state[key]
            st.download_button(f"⬇ Download ZIP ({n} certificates)", z, f"Donor_Certificates_{tag}.zip")
    if custom:
        return
    with tabs["Balance Sheet"]:
        l, rr = st.columns(2)
        with l:
            st.subheader("Funds & Liabilities")
            show(r["bs_l"])
        with rr:
            st.subheader("Assets")
            show(r["bs_a"])
        if abs(r["bs_diff"]) > 0.5:
            st.error(f"Balance sheet does not tally (difference {inr(r['bs_diff'])}). Check opening balances in Settings / Fixed Assets.")
        else:
            st.success("Balance sheet tallies ✅")
    with tabs["Application of Income"]:
        show(r["app"])
        pct = c.fget("apply_pct", 85.0)
        st.metric("Application as % of income", f"{r['app_pct']:.2f}%")
        (st.success if r["app_pct"] >= pct else st.warning)(
            "Minimum application requirement is met." if r["app_pct"] >= pct else
            "Application is below the requirement - the shortfall must be accumulated/handled as per the Income-tax rules (consult your CA).")
        st.caption("Helper computation of the 85% application rule. Always confirm final figures, accumulation and audit report forms with your CA.")
    with tabs["Fixed Assets"]:
        show(r["fa"])
    with tabs["Year comparison"]:
        show(c.compare())


def settings():
    st.header("Settings")
    t = st.tabs(["Trust details", "Books & opening balances", "Bank / Cash accounts", "Heads", "Security & backup", "Audit log"])
    with t[0]:
        keys = [("trust_name", "Trust name"), ("address", "Address"), ("reg_no", "Registration no. / deed details"), ("pan", "Trust PAN"),
                ("reg12a", "12A / 12AB registration no."), ("reg80g", "80G / Section 133 approval no."),
                ("appr_valid", "Approval valid up to (e.g. 31-03-2029)"), ("phone", "Phone"), ("email", "Email"),
                ("prefix", "Receipt / voucher prefix"), ("signatory", "Signatory title (printed on receipts)"),
                ("cash_limit", "Cash donation limit for deduction (₹)"), ("cash_pay_limit", "Cash payment warning limit (₹)")]
        with st.form("trust"):
            vals = {k: st.text_input(lbl, c.get_setting(k)) for k, lbl in keys}
            if st.form_submit_button("💾 Save", type="primary"):
                for k, v in vals.items():
                    c.set_setting(k, v)
                flash("Trust details saved.")
        st.subheader("Logo & signature")
        for key, lbl in (("logo", "Trust logo (shown on receipts, reports and sidebar)"), ("signature", "Authorised signature (optional, printed on receipts)")):
            img = c.get_image(key)
            l, r_ = st.columns([1, 3])
            if img:
                l.image(img, width=110)
            up = r_.file_uploader(lbl, type=["png", "jpg", "jpeg"], key="up" + key)
            if up and r_.button(f"💾 Save {key}", key="sv" + key):
                c.set_image(key, up.getvalue())
                flash(f"{key.title()} saved.")
            if img and r_.button(f"Remove {key}", key="rm" + key):
                c.set_setting(f"img_{key}", "")
                flash(f"{key.title()} removed.")
    with t[1]:
        st.info("Do this FIRST. Pick the first financial year you will keep accounts in this software, then enter balances as on 1 April of that year. "
                "Opening bank/cash balances go in the accounts tab, opening fixed assets in Fixed Assets. The opening General Fund is calculated automatically.")
        first = c.q("SELECT MIN(date) m FROM txns").m[0]
        yrs = list(range(2010, cur + 2))
        with st.form("books"):
            start = st.selectbox("Books start from financial year", yrs, index=idx(yrs, bs), format_func=c.fy_label)
            oc = st.number_input("Opening Corpus Fund", value=c.fget("opening_corpus"), format="%.2f")
            oi = st.number_input("Opening investments / FDs (at cost)", value=c.fget("opening_inv"), format="%.2f")
            pc = st.number_input("Minimum application % of income", value=c.fget("apply_pct", 85.0))
            if st.form_submit_button("💾 Save", type="primary"):
                if first and str(first) < f"{start}-04-01":
                    st.error(f"You already have entries dated {first}; choose an earlier start year.")
                else:
                    for k, v in dict(books_start=start, opening_corpus=oc, opening_inv=oi, apply_pct=pc).items():
                        c.set_setting(k, v)
                    flash("Saved.")
    with t[2]:
        ac = c.q("SELECT * FROM accounts ORDER BY id")
        show(ac.drop(columns="id"))
        L = {0: "➕ New account"} | {r.id: r.name for r in ac.itertuples()}
        sel = st.selectbox("Select account", list(L), format_func=L.get)
        row = ac[ac.id == sel].iloc[0].to_dict() if sel else {}
        with st.form(f"acc{sel}", clear_on_submit=not sel):
            nm = st.text_input("Account name *", nz(row.get("name")), help="e.g. SBI Current A/c 1234")
            ty = st.selectbox("Type", ["Bank", "Cash"], index=idx(["Bank", "Cash"], row.get("type", "Bank")))
            bk, no = st.text_input("Bank & branch", nz(row.get("bank"))), st.text_input("Account number", nz(row.get("acc_no")))
            op = st.number_input(f"Opening balance on 1 April {bs}", value=float(nz(row.get("opening"), 0.0)), format="%.2f")
            act = st.checkbox("Active", bool(nz(row.get("active"), 1)))
            if st.form_submit_button("💾 Save account", type="primary") and nm.strip():
                try:
                    if sel:
                        c.ex("UPDATE accounts SET name=?,type=?,bank=?,acc_no=?,opening=?,active=? WHERE id=?", (nm.strip(), ty, bk, no, op, int(act), sel))
                    else:
                        c.ex("INSERT INTO accounts(name,type,bank,acc_no,opening,active) VALUES(?,?,?,?,?,?)", (nm.strip(), ty, bk, no, op, int(act)))
                    flash("Account saved.")
                except sqlite3.IntegrityError:
                    st.error("An account with this name already exists.")
    with t[3]:
        hd = c.q("SELECT id,name Head,kind Kind,is_donation 'Donation head' FROM heads ORDER BY id")
        show(hd.drop(columns="id"))
        with st.form("head", clear_on_submit=True):
            nm, kd = st.text_input("New head name"), st.selectbox("Kind", KINDS, help="Income/Corpus/Realisation = receipts; Revenue/Capital/Investment = payments")
            isd = st.checkbox("Treat as donation (goes to Donations Register, receipts, certificates, 10BD data)")
            if st.form_submit_button("➕ Add head") and nm.strip():
                try:
                    c.ex("INSERT INTO heads(name,kind,is_donation) VALUES(?,?,?)", (nm.strip(), kd, int(isd and kd in KINDS[:3])))
                    flash("Head added.")
                except sqlite3.IntegrityError:
                    st.error("Head already exists.")
        dl = st.selectbox("Delete an unused head", hd.id.tolist(), format_func=dict(zip(hd.id, hd.Head)).get)
        if st.button("🗑 Delete head"):
            if c.q("SELECT COUNT(*) n FROM txns WHERE head_id=?", (dl,)).n[0]:
                st.error("This head is used in entries and cannot be deleted.")
            else:
                c.ex("DELETE FROM heads WHERE id=?", (dl,))
                flash("Head deleted.")
    with t[4]:
        has = bool(c.get_setting("pw_hash"))
        with st.form("pw", clear_on_submit=True):
            old = st.text_input("Current password", type="password") if has else ""
            n1, n2 = st.text_input("New password (leave blank to remove)", type="password"), st.text_input("Repeat new password", type="password")
            if st.form_submit_button("🔐 Update password"):
                if not c.check_pw(old):
                    st.error("Current password is wrong.")
                elif n1 != n2:
                    st.error("Passwords do not match.")
                else:
                    c.set_setting("pw_hash", c.hash_pw(n1) if n1 else "")
                    flash("Password updated.")
        st.subheader("Backup & restore")
        st.caption(f"Automatic daily backups are kept in: {c.DATA / 'backups'}")
        st.download_button("⬇ Download full backup now", c.DB.read_bytes(), f"unihope_backup_{dt.date.today():%Y%m%d}.db")
        up = st.file_uploader("Restore from a backup (.db) - replaces ALL current data", type="db")
        if up and st.checkbox("I understand current data will be replaced") and st.button("♻ Restore now"):
            c.DB.write_bytes(up.getvalue())
            c.init_db()
            flash("Backup restored.")
    with t[5]:
        show(c.q("SELECT ts Time,action Action,detail Detail FROM audit WHERE substr(ts,1,10) BETWEEN ? AND ? ORDER BY id DESC LIMIT 500", (S_, E_)))
        st.caption(f"Showing activity in {PERIOD}.")


PAGES = dict(zip(MENU, [
    dashboard,
    lambda: register("Donations Register", "RECEIPT", True),
    lambda: register("Other Receipts", "RECEIPT", False),
    lambda: register("Application Register", "PAYMENT"),
    lambda: register("Transfers", "TRANSFER"),
    bank, donors, assets, reports, settings]))
PAGES[page]()
