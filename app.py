import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import fitz  # PyMuPDF
import io
import pandas as pd
import sqlite3
from datetime import datetime
import time

# --- 1. KONFIGURÁCIA ---
DB_FILE = "sklad_v7_7.db" # Nová DB pre istotu

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    # Tichý režim chyby, aby nezhadzovala appku pri štarte
    pass

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# --- 2. POMOCNÉ FUNKCIE ---
def optimize_image(image, max_width=800):
    width, height = image.size
    if width > max_width:
        ratio = max_width / width
        new_height = int(height * ratio)
        return image.resize((max_width, new_height))
    return image

def clean_json_response(text):
    text = text.replace("```json", "").replace("```", "").strip()
    start = text.find('['); end = text.rfind(']')
    if start != -1 and end != -1: return text[start:end+1]
    return text

def process_file(uploaded_file):
    if uploaded_file.type == "application/pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        pix = doc.load_page(0).get_pixmap()
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else: img = Image.open(uploaded_file)
    return optimize_image(img)

# --- 3. DATABÁZA ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, is_premium INTEGER DEFAULT 0, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        owner TEXT, 
        nazov TEXT, 
        kategoria TEXT, 
        vaha_g REAL, 
        kcal_100g REAL, 
        datum_pridania TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_log (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, prijate_kcal REAL, datum TEXT, cas TEXT)''')
    conn.commit()
    conn.close()

def create_basic_user(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (username, is_premium, last_updated) VALUES (?, 0, ?)', (username, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        try:
            vaha = float(item.get('vaha_g', 100))
            kcal = float(item.get('kcal_100g', 100))
        except:
            vaha, kcal = 100.0, 100.0
        c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, datum_pridania) 
                     VALUES (?, ?, ?, ?, ?, ?)''', 
                  (owner, item.get('nazov', 'Neznáme'), item.get('kategoria', 'Iné'), vaha, kcal, today))
    conn.commit()
    conn.close()

def update_inventory_weight(updates, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for u in updates:
        try:
            w = float(u['vaha_g'])
            c.execute("UPDATE inventory SET vaha_g=? WHERE id=? AND owner=?", (w, u['id'], owner))
        except: pass
    conn.commit()
    conn.close()

def quick_consume(item_id, amount_g, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT vaha_g FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    row = c.fetchone()
    if row:
        new_w = max(0, row[0] - amount_g)
        if new_w <= 0: c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        else: c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_w, item_id))
    conn.commit()
    conn.close()

def delete_item(item_id, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    conn.commit()
    conn.close()

def get_inventory(owner):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM inventory WHERE owner=?", conn, params=(owner,))
    conn.close()
    return df

def get_full_log(owner):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM daily_log WHERE owner=?", conn, params=(owner,))
    conn.close()
    return df

def seed_test_data(owner):
    data = [
        ("Kuracie prsia", "Mäso", 1500, 165), ("Mlieko", "Mliečne", 1000, 42),
        ("Vajcia", "Mliečne", 500, 155), ("Ryža", "Trvanlivé", 2000, 360),
        ("Jablká", "Ovocie", 1000, 52), ("Maslo", "Mliečne", 250, 717)
    ]
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    d = datetime.now().strftime("%Y-%m-%d")
    for n, k, v, kc in data:
        c.execute("INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, datum_pridania) VALUES (?,?,?,?,?,?)", (owner, n, k, v, kc, d))
    conn.commit(); conn.close()

def cook_recipe(recipe_name, kcal, ingredients, owner):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    now = datetime.now()
    c.execute("INSERT INTO daily_log (owner, nazov, prijate_kcal, datum, cas) VALUES (?,?,?,?,?)", 
              (owner, recipe_name, kcal, now.strftime("%Y-%m-%d"), now.strftime("%H:%M")))
    for ing in ingredients:
        if ing.get('id'):
            c.execute("UPDATE inventory SET vaha_g = vaha_g - ? WHERE id = ?", (ing.get('amount_g', 0), ing['id']))
            c.execute("DELETE FROM inventory WHERE vaha_g <= 0")
    conn.commit(); conn.close()

# --- 4. UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food v7.7", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if 'recipes' not in st.session_state: st.session_state.recipes = []

if not st.session_state.username:
    st.title("🥗 Smart Food v7.7 (Stable)")
    name = st.text_input("Meno:")
    if st.button("Štart") and name:
        st.session_state.username = name
        create_basic_user(name)
        st.rerun()
    st.stop()

current_user = st.session_state.username
tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "⚙️ Nástroje"])

# === TAB 1: SKLAD (MAXIMAL STABILITY) ===
with tabs[0]:
    df = get_inventory(current_user)
    
    if df.empty:
        st.info("Sklad je prázdny. Choď do záložky 'Nástroje' a klikni na Testovací nákup.")
    else:
        # Metriky
        c1, c2 = st.columns(2)
        c1.metric("Položky", len(df))
        c2.metric("Váha", f"{df['vaha_g'].sum()/1000:.1f} kg")
        
        st.divider()
        
        # Filtrovanie
        col_s, col_f = st.columns([2,1])
        search = col_s.text_input("Hľadať...")
        cats = list(df['kategoria'].unique())
        if cats:
            sel_cat = col_f.multiselect("Kategória", cats, default=cats)
        else:
            sel_cat = []
        
        # Aplikácia filtra
        df_view = df.copy()
        if sel_cat:
            df_view = df_view[df_view['kategoria'].isin(sel_cat)]
        if search:
            df_view = df_view[df_view['nazov'].str.contains(search, case=False)]
        
        # !!! CRITICAL FIX: CLEANING DATA TYPES !!!
        # Toto zabráni TypeError. Vynútime typy.
        df_view = df_view.reset_index(drop=True)
        df_view['vaha_g'] = pd.to_numeric(df_view['vaha_g'], errors='coerce').fillna(0)
        df_view['nazov'] = df_view['nazov'].astype(str)
        df_view['kategoria'] = df_view['kategoria'].astype(str)

        # PRÍPRAVA PRE EDITOR
        # Zobrazíme len potrebné stĺpce, žiadne fancy configy
        editor_data = df_view[['nazov', 'kategoria', 'vaha_g', 'id']].copy()

        st.caption("Môžeš prepísať váhu priamo v tabuľke.")
        
        edited = st.data_editor(
            editor_data[['nazov', 'kategoria', 'vaha_g']], # ID nezobrazíme
            column_config={
                "vaha_g": st.column_config.NumberColumn("Množstvo (g)", min_value=0, max_value=10000)
            },
            hide_index=True,
            use_container_width=True,
            selection_mode="single-row",
            key="stable_editor_v77"
        )

        # DETEKCIA ZMIEN
        # Porovnáme edited (frontend) s editor_data (backend) podľa indexu
        changes = []
        for i, row in edited.iterrows():
            # Nájdeme pôvodné ID z editor_data podľa indexu
            original_id = editor_data.iloc[i]['id']
            # Nájdeme pôvodnú váhu z DB (df)
            db_row = df[df['id'] == original_id]
            
            if not db_row.empty:
                old_w = float(db_row.iloc[0]['vaha_g'])
                new_w = float(row['vaha_g'])
                if old_w != new_w:
                    changes.append({'id': original_id, 'vaha_g': new_w})
        
        if changes:
            update_inventory_weight(changes, current_user)
            st.toast("Uložené!")
            time.sleep(0.5)
            st.rerun()

        # AKCIE
        sel = st.session_state.stable_editor_v77.get("selection", {"rows": []})
        if sel["rows"]:
            idx = sel["rows"][0]
            # Mapovanie indexu na ID
            real_id = editor_data.iloc[idx]['id']
            row_data = df[df['id'] == real_id].iloc[0]
            
            st.info(f"Vybrané: **{row_data['nazov']}**")
            c1, c2 = st.columns(2)
            if c1.button("🍽️ Zjesť 100g"):
                quick_consume(real_id, 100, current_user); st.rerun()
            if c2.button("🗑️ Vyhodiť"):
                delete_item(real_id, current_user); st.rerun()


# === TAB 2: SKENOVANIE ===
with tabs[1]:
    st.header("📸 Skenovanie")
    up = st.file_uploader("Nahraj súbor", accept_multiple_files=True)
    if up and st.button("Analyzovať"):
        res = []
        bar = st.progress(0)
        for i, f in enumerate(up):
            try:
                img = process_file(f)
                p = "JSON zoznam: [{'nazov':str, 'kategoria':str, 'vaha_g':int}]."
                r = model.generate_content([p, img], safety_settings=SAFETY_SETTINGS)
                res.extend(json.loads(clean_json_response(r.text)))
                time.sleep(2)
            except: pass
            bar.progress((i+1)/len(up))
        st.session_state.scan_res = res

    if 'scan_res' in st.session_state:
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_res))
        if st.button("Uložiť"):
            add_to_inventory(ed.to_dict('records'), current_user)
            del st.session_state.scan_res; st.rerun()

# === TAB 3: KUCHYŇA ===
with tabs[2]:
    st.header("👨‍🍳 Kuchyňa")
    inv = get_inventory(current_user)
    if inv.empty: st.warning("Prázdny sklad.")
    elif st.button("✨ Vygenerovať recepty"):
        inv_j = inv[['nazov', 'vaha_g']].to_json()
        p = f"Sklad: {inv_j}. 3 recepty JSON: [{{'title':str, 'kcal':int, 'ingredients':[{{'name':str, 'amount_g':int, 'id':int}}], 'steps':[str]}}]"
        try:
            r = model.generate_content(p)
            st.session_state.recipes = json.loads(clean_json_response(r.text))
        except: st.error("Chyba AI")
    
    if st.session_state.recipes:
        for i, r in enumerate(st.session_state.recipes):
            with st.expander(f"{r['title']} ({r['kcal']} kcal)"):
                st.write(r['steps'])
                if st.button("Uvariť", key=f"r_{i}"):
                    cook_recipe(r['title'], r['kcal'], r['ingredients'], current_user)
                    st.balloons(); st.rerun()

# === TAB 4: PREHĽAD ===
with tabs[3]:
    st.header("📊 Štatistiky")
    log = get_full_log(current_user)
    if not log.empty:
        st.bar_chart(log['prijate_kcal'])
        st.dataframe(log)

# === TAB 5: NÁSTROJE ===
with tabs[4]:
    st.header("⚙️ Nástroje")
    if st.button("🛒 Testovací nákup", type="primary"):
        seed_test_data(current_user); st.success("Hotovo!"); time.sleep(1); st.rerun()
    if st.button("🗑️ Vymazať všetko"):
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        c.execute("DELETE FROM inventory WHERE owner=?", (current_user,)); conn.commit(); conn.close()
        st.rerun()
    if st.button("Odhlásiť"): st.session_state.clear(); st.rerun()
