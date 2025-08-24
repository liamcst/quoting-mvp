import io, os, re
import pandas as pd
import streamlit as st
from matching import match_line, build_search_strings, load_synonyms

try:
    import pdfplumber
except Exception:
    pdfplumber = None

DATA_DIR  = "data"
INV_CACHE = os.path.join(DATA_DIR, "inventory_cache.csv")
SYN_FILE  = os.path.join(DATA_DIR, "synonyms.csv")
LEARN_FILE= os.path.join(DATA_DIR, "learned_aliases.csv")

st.set_page_config(page_title="Quoting MVP — PDF Columns → Cin7", layout="wide")
st.title("Quoting MVP — PDF Columns → Cin7 (Persistent + Learning)")

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Inventory & Options")
    inv_file = st.file_uploader("InventoryList CSV (Cin7 export)", type=["csv"])
    if st.button("Save uploaded inventory as default"):
        if inv_file:
            with open(INV_CACHE,"wb") as f: f.write(inv_file.getvalue())
            st.success("Inventory saved to cache.")
    syn_upload = st.file_uploader("Synonyms CSV (optional)", type=["csv"])
    if st.button("Save uploaded synonyms as default"):
        if syn_upload:
            with open(SYN_FILE,"wb") as f: f.write(syn_upload.getvalue())
            st.success("Synonyms saved to cache.")
    confidence_cutoff = st.slider("Low-confidence threshold", 0, 100, 70, 1)
    use_misc_for_lowconf = st.checkbox("Use C01017 : MISC for low-confidence/no-match", value=True)
    include_price = st.checkbox("Include PriceTier1 in export", value=False)

# ---------------- Inventory Prep ----------------
def prep_inventory(inv_df: pd.DataFrame):
    out = pd.DataFrame()
    out['sku'] = inv_df['ProductCode'].astype(str)
    out['name'] = inv_df['Name'].astype(str)
    out['brand'] = inv_df.get('Brand', pd.Series([""]*len(inv_df))).astype(str)
    out['category'] = inv_df.get('Category', pd.Series([""]*len(inv_df))).astype(str)
    out['width_mm'] = pd.to_numeric(inv_df.get('Width', None), errors='coerce')
    out['length_m'] = pd.to_numeric(inv_df.get('Length', None), errors='coerce')
    uom_raw = inv_df.get('DefaultUnitOfMeasure', pd.Series(["item"]*len(inv_df))).astype(str).str.lower()
    out['sell_uom'] = uom_raw.replace({'each':'unit','item':'unit','roll':'roll','metre':'metre'})
    out['pack_qty'] = 1
    out['active'] = 'y'
    out['price'] = pd.to_numeric(inv_df.get('PriceTier1', pd.Series([None]*len(inv_df))), errors='coerce')
    out['_search'] = out.apply(build_search_strings, axis=1)
    return out

def load_inventory_df(uploaded):
    if uploaded is not None:
        return pd.read_csv(uploaded), True
    if os.path.exists(INV_CACHE):
        return pd.read_csv(INV_CACHE), False
    return None, False

# ---------------- Synonyms (base + learned) ----------------
def load_all_synonyms():
    base = load_synonyms(SYN_FILE) if os.path.exists(SYN_FILE) else {}
    learned = load_synonyms(LEARN_FILE) if os.path.exists(LEARN_FILE) else {}
    return {**base, **learned}

# ---------------- PDF parsing with columns ----------------
def parse_pdf_tables(pdf_bytes: bytes) -> pd.DataFrame:
    if pdfplumber is None:
        st.error("pdfplumber not installed.")
        return pd.DataFrame()
    items = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for t in tables:
                for row in t:
                    if not row: continue
                    if all((c is None) or (str(c).strip()=="") for c in row): continue
                    items.append(row)
    if not items:
        return pd.DataFrame()
    maxcols = max(len(r) for r in items)
    norm = [(r + [None]*(maxcols-len(r))) for r in items]
    df = pd.DataFrame(norm, columns=[f"col{i+1}" for i in range(maxcols)])
    # Drop obvious section headings: short ALLCAPS rows w/out digits
    def is_heading(row):
        joined = " ".join([str(x or "").strip() for x in row])
        if not joined: return False
        letters = re.sub(r"[^A-Za-z ]","", joined).strip()
        if not letters: return False
        if len(letters.split()) <= 4 and letters.upper() == letters and not re.search(r"\d", joined):
            return True
        return False
    return df[~df.apply(is_heading, axis=1)]

# ---------------- Column mapping UI ----------------
uploaded_pdf = st.file_uploader("Customer PDF", type=["pdf"])
input_df = None
if uploaded_pdf:
    raw_tbl = parse_pdf_tables(uploaded_pdf.getvalue())
    if not raw_tbl.empty:
        st.subheader("PDF Table Preview")
        st.dataframe(raw_tbl.head(30), use_container_width=True)
        cols = list(raw_tbl.columns)
        # naive guesses
        item_col = st.selectbox("Select ITEM column", cols, index=0)
        note_col = st.selectbox("Select NOTES column", cols, index=1 if len(cols)>1 else 0)
        qty_col  = st.selectbox("Select QUANTITY column", cols, index=2 if len(cols)>2 else max(0,len(cols)-1))
        mapped = pd.DataFrame({
            "item":   raw_tbl[item_col].astype(str).str.strip(),
            "notes":  raw_tbl[note_col].astype(str).str.strip(),
            "quantity": raw_tbl[qty_col]
        })
        mapped = mapped[(mapped["item"]!="") | (mapped["notes"]!="")]
        st.subheader("Mapped Data")
        st.dataframe(mapped.head(30), use_container_width=True)
        input_df = mapped.copy()

# ---------------- Build suggestions ----------------
if st.button("Match (build suggestions)"):
    inv_df, from_upload = load_inventory_df(inv_file)
    if inv_df is None:
        st.error("No inventory loaded or cached. Upload your InventoryList CSV or save one to cache.")
    elif input_df is None or input_df.empty:
        st.error("No customer items parsed from the PDF.")
    else:
        catalog_df = prep_inventory(inv_df)
        syn = load_all_synonyms()

        recs = []
        options = []
        for _, r in input_df.iterrows():
            item = str(r['item'])
            note = str(r.get('notes',''))
            q_raw = r.get('quantity')
            try:
                qty = int(str(q_raw).strip())
            except:
                qty = 1

            result = match_line((item + " " + note).strip(), catalog_df, syn)
            best = result['best']
            cands = result['candidates'] or []
            row_options = [f"{c['sku']} | {c['name']}" for c in cands]
            row_options.append("C01017 | MISC")
            options.extend(row_options)

            if (best and best['confidence'] >= confidence_cutoff):
                selected = f"{best['sku']} | {best['name']}"
                sel_qty = qty
                reason_note = best['reason']
            else:
                if use_misc_for_lowconf:
                    selected = "C01017 | MISC"
                    sel_qty = qty
                    reason_note = "low-confidence → MISC"
                else:
                    selected = (f"{best['sku']} | {best['name']}" if best else "")
                    sel_qty = qty
                    reason_note = (best['reason'] if best else "no match")

            recs.append({
                "raw_item": item,
                "notes": note,
                "selected": selected,
                "quantity": sel_qty,
                "confidence": (best['confidence'] if best else 0.0),
                "reason": reason_note
            })

        options = sorted(set(options))
        st.subheader("Review & Learn")
        st.caption("Pick by Name (dropdown shows “SKU | Name”). Choose C01017 | MISC for non-stock lines.")
        edited = st.data_editor(
            pd.DataFrame(recs),
            use_container_width=True,
            column_config={
                "selected": st.column_config.SelectboxColumn("selected (SKU | Name)", options=options),
                "raw_item": st.column_config.TextColumn(disabled=True),
                "notes": st.column_config.TextColumn(),
                "quantity": st.column_config.NumberColumn(min_value=0, step=1),
                "confidence": st.column_config.NumberColumn(disabled=True),
                "reason": st.column_config.TextColumn(disabled=True),
            },
            num_rows="fixed",
            key="review_editor_cols"
        )

        if st.button("✔ Save learning & prepare export"):
            all_syn = load_all_synonyms()

            learn_rows = []
            for _, row in edited.iterrows():
                sel = str(row["selected"])
                if not sel or sel.startswith("C01017"):  # skip MISC
                    continue
                sku = sel.split(" | ", 1)[0]
                line_key = (str(row["raw_item"]) + " " + str(row["notes"])).strip().lower()
                sku_row = catalog_df[catalog_df['sku'] == sku]
                canonical = (sku_row['name'].iloc[0] if len(sku_row) else sku).lower()
                learn_rows.append({"alias": line_key, "canonical": canonical, "source": "user_choice"})

            if learn_rows:
                new_df = pd.DataFrame(learn_rows)
                if os.path.exists(LEARN_FILE):
                    exist = pd.read_csv(LEARN_FILE)
                else:
                    exist = pd.DataFrame(columns=["alias","canonical","source"])
                merged = pd.concat([exist, new_df], ignore_index=True).drop_duplicates(subset=["alias"], keep="last")
                merged.to_csv(LEARN_FILE, index=False)
                st.success(f"Saved {len(learn_rows)} learned aliases.")

            out_rows = []
            for _, row in edited.iterrows():
                sel = str(row["selected"])
                qty = int(row["quantity"]) if pd.notna(row["quantity"]) else 1
                comment = str(row["notes"]).strip()
                if sel.startswith("C01017"):
                    out_rows.append({
                        "SKU": "C01017", "Name": "MISC",
                        "Comment": f"{row['raw_item']} {comment}".strip(),
                        "Quantity": qty, "Price": "" if not include_price else "", "Discount": ""
                    })
                else:
                    sku = sel.split(" | ", 1)[0]
                    sku_row = catalog_df[catalog_df['sku'] == sku]
                    name = sku_row['name'].iloc[0] if len(sku_row) else ""
                    price = sku_row['price'].iloc[0] if (include_price and len(sku_row)) else ""
                    out_rows.append({
                        "SKU": sku, "Name": name,
                        "Comment": comment,
                        "Quantity": qty, "Price": price, "Discount": ""
                    })

            out_df = pd.DataFrame(out_rows, columns=["SKU","Name","Comment","Quantity","Price","Discount"])
            st.subheader("Export preview")
            st.dataframe(out_df, use_container_width=True)
            csv = out_df.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download BulkUpdateSaleQuote.csv", csv, "BulkUpdateSaleQuote.csv", "text/csv")
