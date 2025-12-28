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
# ZMENA: Úplne nová DB, aby sme vylúčili poškodené dáta
DB_FILE = "sklad_final_v78.db"

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
except: pass

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
    # Veľmi jednoduchá tabuľka inventory
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
        try: v = float(item.get('vaha_g', 100))
        except: v = 100.0
        try: k = float(item.get('kcal_100g', 100))
        except: k = 100.0
        
        c.execute("INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, datum_pridania) VALUES (?,?,?,?,?,?)", 
                  (owner, item.get('nazov','?'), item.get('kategoria','Iné'), v, k, today))
    conn.commit()
    conn.close()

def update_inventory_weight(item_id, new_weight, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE inventory SET vaha_g=? WHERE id=? AND owner=?", (float(new_weight), item_id, owner))
    conn.commit()
    conn.close()

def quick_consume(item_id, amount, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT vaha_g FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    row = c.fetchone()
    if row:
        new_w = max(0, row[0] - amount)
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
    data = [("Mlieko", "Mliečne", 1000), ("Vajcia", "Mliečne", 500), ("Chlieb", "Trvanlivé", 800)]
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    d = datetime.now().strftime("%Y-%m-%d")
    for n, k, v in data:
        c.execute("INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, datum_pridania) VALUES (?,?,?,?,100,?)", (owner, n, k, v, d))
    conn.commit(); conn.close()

def cook_recipe(name, kcal, ingredients, owner):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    now = datetime.now()
    c.execute("INSERT INTO daily_log (owner, nazov, prijate_kcal, datum, cas) VALUES (?,?,?,?,?)", (owner, name, kcal, now.strftime("%Y-%m-%d"), now.strftime("%H:%M")))
    for ing in ingredients:
        if ing.get('id'):
            c.execute("UPDATE inventory SET vaha_g = vaha_g - ? WHERE id = ?", (ing.get('amount_g', 0), ing['id']))
            c.execute("DELETE FROM inventory WHERE vaha_g <= 0")
    conn.commit(); conn.close()

# --- 4. UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food v7.8", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if 'recipes' not in st.session_state: st.session_state.recipes = []

if not st.session_state.username:
    st.title("🥗 Smart Food v7.8 (Bare Metal)")
    name = st.text_input("Meno:")
    if st.button("Štart") and name:
        st.session_state.username = name
        create_basic_user(name)
        st.rerun()
    st.stop()

current_user = st.session_state.username
tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "⚙️ Nástroje"])

# === TAB 1: SKLAD (ABSOLUTE SAFE MODE) ===
with tabs[0]:
    df = get_inventory(current_user)
    
    if df.empty:
        st.info("Sklad je prázdny.")
    else:
        # Pripravíme dáta - ŽIADNE ŠPECIÁLNE TYPY, LEN ČISTÉ DÁTA
        # Streamlit editor potrebuje čisté typy
        df['vaha_g'] = df['vaha_g'].astype(float)
        df['nazov'] = df['nazov'].astype(str)
        df['kategoria'] = df['kategoria'].astype(str)
        
        # Zobrazíme len to čo treba
        display_df = df[['id', 'nazov', 'kategoria', 'vaha_g']].copy()
        
        st.write("📝 **Skladové zásoby** (Prepíš váhu a stlač Enter)")
        
        # !!! TOTO JE KĽÚČOVÁ ZMENA: Žiadne column_config, nový key !!!
        edited_df = st.data_editor(
            display_df,
            key="final_editor_v78",  # Nový kľúč vymaže staré chyby z cache
            num_rows="dynamic",
            use_container_width=True
        )
        
        # Detekcia zmien
        # Porovnáme pôvodné dáta s upravenými
        # Iterate over rows in edited_df
        for index, row in edited_df.iterrows():
            original_row = df[df['id'] == row['id']]
            if not original_row.empty:
                old_weight = float(original_row.iloc[0]['vaha_g'])
                new_weight = float(row['vaha_g'])
                
                if old_weight != new_weight:
                    update_inventory_weight(row['id'], new_weight, current_user)
                    st.toast(f"Zmenené: {row['nazov']}")
                    time.sleep(0.5)
                    st.rerun()
                    
        st.divider()
        st.write("🛠 **Rýchle akcie**")
        # Výber pre akcie cez selectbox namiesto klikania do tabuľky (stabilnejšie)
        selected_item_name = st.selectbox("Vyber surovinu na akciu:", display_df['nazov'].tolist())
        
        if selected_item_name:
            # Nájdi ID
            item_row = df[df['nazov'] == selected_item_name].iloc[0]
            c1, c2, c3 = st.columns(3)
            
            if c1.button(f"Zjesť 100g"):
                quick_consume(item_row['id'], 100, current_user)
                st.rerun()
            
            if c2.button(f"Minúť všetko"):
                quick_consume(item_row['id'], item_row['vaha_g'], current_user)
                st.rerun()
                
            if c3.button("Vyhodiť"):
                delete_item(item_row['id'], current_user)
                st.rerun()

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
                p = "JSON: [{'nazov':str, 'kategoria':str, 'vaha_g':int}]."
                r = model.generate_content([p, img], safety_settings=SAFETY_SETTINGS)
                res.extend(json.loads(clean_json_response(r.text)))
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
    elif st.button("✨ Recepty"):
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
