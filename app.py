import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import fitz  # PyMuPDF
import io
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import time
import matplotlib.pyplot as plt

# --- 1. KONFIGURÁCIA ---
DB_FILE = "sklad_v7_5.db" 

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    st.error(f"Chyba konfigurácie API kľúča: {e}")

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

CAT_ICONS = {
    "Mäso": "🥩", 
    "Mliečne": "🥛", 
    "Zelenina": "🥦", 
    "Ovocie": "🍎", 
    "Trvanlivé": "🥖", 
    "Iné": "🥫"
}

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
    start_idx = text.find('[')
    end_idx = text.rfind(']')
    if start_idx != -1 and end_idx != -1:
        return text[start_idx:end_idx+1]
    start_obj = text.find('{')
    end_obj = text.rfind('}')
    if start_obj != -1 and end_obj != -1:
        return text[start_obj:end_obj+1]
    return text

def process_file(uploaded_file):
    if uploaded_file.type == "application/pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        pix = doc.load_page(0).get_pixmap()
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else: img = Image.open(uploaded_file)
    return optimize_image(img)

# --- 3. DATABÁZA A LOGIKA ---
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
        bielkoviny_100g REAL DEFAULT 0,
        sacharidy_100g REAL DEFAULT 0,
        tuky_100g REAL DEFAULT 0,
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

def add_item_manual(owner, nazov, vaha, kategoria):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
              (owner, nazov, kategoria, vaha, 100, 10, 10, 5, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                  (owner, item.get('nazov'), item.get('kategoria'), item.get('vaha_g'), item.get('kcal_100g', 100), 
                   item.get('bielkoviny_100g',0), item.get('sacharidy_100g',0), item.get('tuky_100g',0), today))
    conn.commit()
    conn.close()

def update_item_details(item_id, nazov, kategoria, vaha, kcal, b, s, t, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''UPDATE inventory 
                 SET nazov=?, kategoria=?, vaha_g=?, kcal_100g=?, bielkoviny_100g=?, sacharidy_100g=?, tuky_100g=? 
                 WHERE id=? AND owner=?''', 
              (nazov, kategoria, vaha, kcal, b, s, t, item_id, owner))
    conn.commit()
    conn.close()

def update_inventory_weight(updates, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for u in updates:
        c.execute("UPDATE inventory SET vaha_g=? WHERE id=? AND owner=?", (u['vaha_g'], u['id'], owner))
    conn.commit()
    conn.close()

def quick_consume(item_id, amount_g, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT vaha_g FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    row = c.fetchone()
    if row:
        new_w = max(0, row[0] - amount_g)
        if new_w == 0:
            c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        else:
            c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_w, item_id))
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
    nakup = [
        {'nazov': 'Kuracie prsia', 'kategoria': 'Mäso', 'vaha_g': 1500, 'kcal_100g': 165},
        {'nazov': 'Hovädzie zadné', 'kategoria': 'Mäso', 'vaha_g': 1000, 'kcal_100g': 250},
        {'nazov': 'Vajcia L (30ks)', 'kategoria': 'Mliečne', 'vaha_g': 1800, 'kcal_100g': 155},
        {'nazov': 'Mlieko polotučné', 'kategoria': 'Mliečne', 'vaha_g': 6000, 'kcal_100g': 46},
        {'nazov': 'Maslo 82%', 'kategoria': 'Mliečne', 'vaha_g': 500, 'kcal_100g': 717},
        {'nazov': 'Zemiaky', 'kategoria': 'Zelenina', 'vaha_g': 5000, 'kcal_100g': 77},
        {'nazov': 'Ryža Basmati', 'kategoria': 'Trvanlivé', 'vaha_g': 2000, 'kcal_100g': 365},
        {'nazov': 'Olivový olej', 'kategoria': 'Trvanlivé', 'vaha_g': 1000, 'kcal_100g': 884}
    ]
    add_to_inventory(nakup, owner)

def cook_recipe_from_stock(ingredients_used, recipe_name, total_kcal, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now()
    c.execute('INSERT INTO daily_log (owner, nazov, prijate_kcal, datum, cas) VALUES (?, ?, ?, ?, ?)', 
              (owner, recipe_name, total_kcal, now.strftime("%Y-%m-%d"), now.strftime("%H:%M")))
    for ing in ingredients_used:
        if ing.get('id'):
            c.execute("UPDATE inventory SET vaha_g = vaha_g - ? WHERE id = ?", (ing['amount_g'], ing['id']))
            c.execute("DELETE FROM inventory WHERE vaha_g <= 0")
    conn.commit()
    conn.close()

# --- 4. UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food v7.5.2", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if 'active_plan' not in st.session_state: st.session_state.active_plan = [] 

if not st.session_state.username:
    col1, col2 = st.columns([1,2])
    with col1: st.image("https://cdn-icons-png.flaticon.com/512/2927/2927347.png", width=120)
    with col2: 
        st.title("Smart Food")
        st.caption("Verzia 7.5.2 - Stability Fix")
        name = st.text_input("Meno užívateľa:")
        if st.button("🚀 Vstúpiť") and name:
            st.session_state.username = name
            create_basic_user(name)
            st.rerun()
    st.stop()

current_user = st.session_state.username
tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "👤 Profil"])

# === TAB 1: SKLAD (STABLE VERSION) ===
with tabs[0]:
    df_inv = get_inventory(current_user)

    if df_inv.empty:
        st.info("Tvoj sklad je prázdny.")
        if st.button("➕ Pridať prvú položku"):
             with st.form("manual_add_empty"):
                n = st.text_input("Názov")
                v = st.number_input("Množstvo (g)", 100)
                if st.form_submit_button("Uložiť"):
                    add_item_manual(current_user, n, v, "Iné"); st.rerun()
    else:
        # Metriky
        total_items = len(df_inv)
        total_weight = df_inv['vaha_g'].sum() / 1000
        low_stock = len(df_inv[df_inv['vaha_g'] < 200])
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Počet položiek", total_items)
        m2.metric("Váha zásob", f"{total_weight:.1f} kg")
        m3.metric("Dochádza", f"{low_stock} ks", delta_color="inverse")

        st.divider()

        # Filtre
        c_search, c_filter = st.columns([2, 2])
        search_query = c_search.text_input("🔍 Rýchle hľadanie", placeholder="Vajcia, Mlieko...")
        cats = df_inv['kategoria'].unique().tolist()
        sel_cats = c_filter.multiselect("Filter", cats, default=cats)
        
        # Aplikácia filtrov
        df_view = df_inv[df_inv['kategoria'].isin(sel_cats)].copy() 
        if search_query:
            df_view = df_view[df_view['nazov'].str.contains(search_query, case=False)]

        # Príprava vizuálnych dát
        df_view['icon'] = df_view['kategoria'].map(lambda x: CAT_ICONS.get(x, "📦"))
        
        # Konverzia na čísla (Float), aby sme sa vyhli TypeError
        df_view['vaha_g'] = pd.to_numeric(df_view['vaha_g'], errors='coerce').fillna(0)

        st.caption("Klikni na riadok pre rýchle akcie.")
        
        # ZJEDNODUŠENÝ DATA EDITOR (Bez Progress Barov, ktoré padajú)
        edited_df = st.data_editor(
            df_view,
            column_order=["icon", "nazov", "vaha_g"], 
            column_config={
                "icon": st.column_config.TextColumn("Druh", disabled=True), 
                "nazov": st.column_config.TextColumn("Surovina", disabled=True),
                # Používame NumberColumn namiesto ProgressColumn pre maximálnu stabilitu
                "vaha_g": st.column_config.NumberColumn(
                    "Množstvo (g)", 
                    min_value=0, 
                    max_value=10000,
                    format="%d g"
                ),
            },
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            key="inv_select"
        )

        # Inšpektor a akcie
        selection = st.session_state.inv_select.get("selection", {"rows": []})
        
        if selection["rows"]:
            idx = selection["rows"][0]
            try:
                row = df_view.iloc[idx]
                
                with st.container(border=True):
                    st.subheader(f"{CAT_ICONS.get(row['kategoria'], '📦')} {row['nazov']}")
                    
                    c_eat1, c_eat2, c_trash = st.columns(3)
                    if c_eat1.button("🍽️ Zjesť 100g", use_container_width=True):
                        quick_consume(row['id'], 100, current_user)
                        st.toast(f"-100g {row['nazov']}")
                        time.sleep(0.5); st.rerun()
                    
                    if c_eat2.button("🥪 Zjesť 50g", use_container_width=True):
                        quick_consume(row['id'], 50, current_user)
                        st.toast(f"-50g {row['nazov']}")
                        time.sleep(0.5); st.rerun()

                    if c_trash.button("🗑️ Minúť všetko", use_container_width=True, type="primary"):
                        quick_consume(row['id'], row['vaha_g'], current_user)
                        st.toast(f"{row['nazov']} minuté!")
                        time.sleep(0.5); st.rerun()
                    
                    with st.expander("✏️ Detailná editácia"):
                        with st.form("edit_full"):
                            new_n = st.text_input("Názov", row['nazov'])
                            new_v = st.number_input("Presná váha", 0, 10000, int(row['vaha_g']))
                            if st.form_submit_button("Uložiť zmeny"):
                                update_item_details(row['id'], new_n, row['kategoria'], new_v, row['kcal_100g'], 0,0,0, current_user)
                                st.rerun()
            except IndexError:
                st.info("Vyber riadok.")
            except Exception as e:
                st.error(f"Chyba pri výbere: {e}")

    st.divider()
    with st.expander("➕ Manuálne pridanie"):
        with st.form("add_new"):
            c1, c2, c3 = st.columns([2,1,1])
            n = c1.text_input("Názov")
            v = c2.number_input("Váha (g)", 100)
            k = c3.selectbox("Kategória", list(CAT_ICONS.keys()))
            if st.form_submit_button("Pridať"):
                add_item_manual(current_user, n, v, k); st.rerun()

# === TAB 2: SKENOVANIE ===
with tabs[1]:
    st.header("📸 Skenovanie")
    up = st.file_uploader("Nahraj bločky", accept_multiple_files=True)
    if up and st.button("Analyzovať"):
        res = []
        bar = st.progress(0)
        for i, f in enumerate(up):
            try:
                img = process_file(f)
                p = "JSON zoznam potravín: [{'nazov':str, 'kategoria':str, 'vaha_g':int}]. Odhadni váhu."
                r = model.generate_content([p, img], safety_settings=SAFETY_SETTINGS)
                res.extend(json.loads(clean_json_response(r.text)))
                time.sleep(2)
            except: st.error("Chyba API")
            bar.progress((i+1)/len(up))
        st.session_state.scan_res = res

    if 'scan_res' in st.session_state:
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_res), num_rows="dynamic")
        if st.button("📥 Naskladniť"):
            add_to_inventory(ed.to_dict('records'), current_user)
            del st.session_state.scan_res; st.rerun()

# === TAB 3: KUCHYŇA ===
with tabs[2]:
    st.header("👨‍🍳 Kuchyňa")
    inv = get_inventory(current_user)
    if inv.empty: st.warning("Sklad je prázdny.")
    else:
        mode = st.radio("Režim", ["🔥 Hladný TERAZ", "📅 Plánovač"], horizontal=True)
        if st.button("✨ Generovať"):
            inv_j = inv[['nazov', 'vaha_g']].to_json()
            if mode == "🔥 Hladný TERAZ":
                p = f"Zo skladu {inv_j} navrhni 3 recepty. JSON: [{{'title':str, 'kcal':int, 'ingredients':[{{'name':str, 'amount_g':int, 'id':int}}], 'steps':[str]}}]"
            else:
                p = f"Plán na 3 dni zo skladu {inv_j}. JSON: [{{'day':'Deň 1', 'title':str, 'kcal':int, 'ingredients':[{{'name':str, 'amount_g':int, 'id':int}}], 'steps':[str]}}]"
            
            try:
                r = model.generate_content(p)
                st.session_state.recipes = json.loads(clean_json_response(r.text))
            except: st.error("Chyba AI")

        if 'recipes' in st.session_state:
            for i, r in enumerate(st.session_state.recipes):
                lbl = r.get('day', f"Recept {i+1}")
                with st.expander(f"{lbl}: {r['title']} ({r['kcal']} kcal)"):
                    st.write(r['steps'])
                    if st.button("Uvariť", key=f"c_{i}"):
                        cook_recipe_from_stock(r['ingredients'], r['title'], r['kcal'], current_user)
                        st.balloons(); st.rerun()

# === TAB 4: PREHĽAD ===
with tabs[3]:
    st.header("📊 Prehľad")
    log = get_full_log(current_user)
    if log.empty: st.info("Zatiaľ žiadne varenie.")
    else:
        st.metric("Počet varení", len(log))
        st.bar_chart(log['prijate_kcal'])
        if st.button("AI Analýza"):
            h = log[['nazov', 'datum']].tail().to_string()
            r = model.generate_content(f"Vtipný komentár k jedlám: {h}")
            st.info(r.text)

# === TAB 5: PROFIL ===
with tabs[4]:
    st.header("🛠 Nástroje")
    if st.button("🛒 Testovací nákup (150€)", type="primary"):
        seed_test_data(current_user); st.success("Hotovo!"); time.sleep(1); st.rerun()
    if st.button("🗑️ Vymazať sklad"):
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        c.execute("DELETE FROM inventory WHERE owner=?", (current_user,)); conn.commit(); conn.close()
        st.rerun()
    if st.button("Odhlásiť"): st.session_state.clear(); st.rerun()
