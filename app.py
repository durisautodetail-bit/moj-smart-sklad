import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from PIL import Image
import json
import fitz  # PyMuPDF
import io
import pandas as pd
import sqlite3
from datetime import datetime
import time

# --- KONFIGURÁCIA ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-flash-latest")
    coach_model = genai.GenerativeModel("gemini-flash-latest")
except Exception as e:
    st.error(f"Chyba konfigurácie: {e}")

# --- OPTIMALIZÁCIA ---
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
        text = text[start_idx:end_idx+1]
    return text

# --- DATABÁZA (S PODPOROU POUŽÍVATEĽOV) ---
def init_db():
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    # Pridali sme stĺpec 'owner' (vlastník)
    c.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT,
            nazov TEXT,
            kategoria TEXT,
            vaha_g REAL,
            kcal_100g REAL,
            bielkoviny_100g REAL,
            sacharidy_100g REAL,
            tuky_100g REAL,
            datum_pridania TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS daily_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT,
            nazov TEXT,
            zjedene_g REAL,
            prijate_kcal REAL,
            prijate_b REAL,
            prijate_s REAL,
            prijate_t REAL,
            datum TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_to_inventory(items, owner):
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('''
            INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            owner,
            item.get('nazov', 'Neznáme'), item.get('kategoria', 'Iné'), item.get('vaha_g', 0), 
            item.get('kcal_100g', 0), item.get('bielkoviny_100g', 0), 
            item.get('sacharidy_100g', 0), item.get('tuky_100g', 0), today
        ))
    conn.commit()
    conn.close()

def eat_item(item_id, grams_eaten, owner):
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Kontrola, či item patrí userovi
    c.execute("SELECT * FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    item = c.fetchone()
    
    if item:
        # Indexy sa posunuli o 1 kvôli stĺpcu owner
        # id=0, owner=1, nazov=2, kat=3, vaha=4, kcal=5, b=6, s=7, t=8
        current_weight = item[4]
        ratio = grams_eaten / 100
        
        c.execute('''
            INSERT INTO daily_log (owner, nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (owner, item[2], grams_eaten, item[5]*ratio, item[6]*ratio, item[7]*ratio, item[8]*ratio, today))
        
        new_weight = current_weight - grams_eaten
        if new_weight <= 0:
            c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        else:
            c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_weight, item_id))
            
    conn.commit()
    conn.close()

def get_inventory(owner):
    conn = sqlite3.connect('sklad.db')
    # Filtrujeme podľa vlastníka
    df = pd.read_sql_query("SELECT * FROM inventory WHERE owner=?", conn, params=(owner,))
    conn.close()
    return df

def get_today_log(owner):
    conn = sqlite3.connect('sklad.db')
    today = datetime.now().strftime("%Y-%m-%d")
    df = pd.read_sql_query("SELECT * FROM daily_log WHERE datum=? AND owner=?", conn, params=(today, owner))
    conn.close()
    return df

def process_file(uploaded_file):
    if uploaded_file.type == "application/pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        img = Image.open(uploaded_file)
    return optimize_image(img)

# --- UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food", layout="wide", page_icon="🥗")
init_db()

# === LOGIN OBRAZOVKA ===
if 'username' not in st.session_state:
    st.session_state.username = None

if not st.session_state.username:
    st.title("🔐 Prihlásenie do Skladu")
    st.write("Ahoj! Zadaj svoje meno alebo prezývku, aby si videl len svoje potraviny.")
    
    name_input = st.text_input("Tvoje meno (napr. Jakub, Jožo, Test):")
    
    if st.button("Vstúpiť 🚀", type="primary"):
        if name_input:
            st.session_state.username = name_input
            st.rerun()
        else:
            st.warning("Musíš zadať meno.")
    st.stop() # Zastaví zvyšok aplikácie, kým sa neprihlási

# Ak sme tu, používateľ je prihlásený
current_user = st.session_state.username

# --- HEADER (Log-out tlačidlo) ---
c_head1, c_head2 = st.columns([3, 1])
c_head1.caption(f"Prihlásený ako: **{current_user}**")
if c_head2.button("Odhlásiť sa"):
    st.session_state.username = None
    st.rerun()

# --- PROFIL ---
with st.expander("👤 Nastavenia Profilu & Cieľov"):
    c1, c2, c3 = st.columns(3)
    gender = c1.selectbox("Pohlavie", ["Muž", "Žena"])
    age = c1.number_input("Vek", 18, 99, 30)
    weight = c2.number_input("Váha (kg)", 40, 150, 80)
    height = c2.number_input("Výška (cm)", 140, 220, 180)
    activity = c3.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"])
    goal = c3.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"])

factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
bmr = (10 * weight) + (6.25 * height) - (5 * age) + (5 if gender == "Muž" else -161)
tdee = bmr * factor[activity]
target_kcal = tdee - 500 if goal == "Chudnúť" else (tdee + 300 if goal == "Pribrať" else tdee)
target_b = (target_kcal * 0.30) / 4
target_s = (target_kcal * 0.40) / 4
target_t = (target_kcal * 0.30) / 9

# --- TABS ---
tab_home, tab_scan, tab_storage, tab_coach = st.tabs(["🏠 Prehľad", "➕ Skenovať", "📦 Sklad", "🤖 Tréner"])

# === TAB 1: PREHĽAD ===
with tab_home:
    st.markdown(f"### 👋 Ahoj {current_user}, dnešný stav:")
    df_log = get_today_log(current_user) # Filtrujeme pre usera
    
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    curr_b = df_log['prijate_b'].sum() if not df_log.empty else 0
    curr_s = df_log['prijate_s'].sum() if not df_log.empty else 0
    curr_t = df_log['prijate_t'].sum() if not df_log.empty else 0
    
    left_kcal = int(target_kcal - curr_kcal)
    color = "green" if left_kcal > 0 else "red"
    st.markdown(f"""
    <div style="background-color: #f0f2f6; padding: 20px; border-radius: 10px; text-align: center;">
        <h2 style="margin:0; color: #31333F;">Zostáva: <span style="color:{color}">{left_kcal} kcal</span></h2>
        <p style="margin:0;">Cieľ: {int(target_kcal)} kcal</p>
    </div>
    """, unsafe_allow_html=True)
    st.progress(min(curr_kcal / target_kcal, 1.0))
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Bielkoviny", f"{int(curr_b)}/{int(target_b)}g", delta=int(target_b - curr_b))
    c2.metric("Sacharidy", f"{int(curr_s)}/{int(target_s)}g", delta=int(target_s - curr_s))
    c3.metric("Tuky", f"{int(curr_t)}/{int(target_t)}g", delta=int(target_t - curr_t))
    
    st.divider()
    
    st.subheader("🍽️ Rýchle jedenie")
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        col_food, col_gram, col_btn = st.columns([3, 2, 2])
        selected_food_name = col_food.selectbox("Jedlo", df_inv['nazov'].unique(), label_visibility="collapsed")
        item_data = df_inv[df_inv['nazov'] == selected_food_name].iloc[0]
        grams = col_gram.number_input("Gramy", 1, int(item_data['vaha_g']), 100, label_visibility="collapsed")
        
        if col_btn.button("Zjesť", type="primary", use_container_width=True):
            eat_item(int(item_data['id']), grams, current_user)
            st.toast("Zapísané!", icon="🥗")
            time.sleep(0.5)
            st.rerun()
    else:
        st.info("Tvoj sklad je prázdny.")

# === TAB 2: SKENOVANIE ===
with tab_scan:
    st.subheader("📸 Nahraj svoj nákup")
    uploaded_files = st.file_uploader("Bločky", type=["jpg", "png", "pdf"], accept_multiple_files=True)
    
    if uploaded_files:
        if st.button("Analyzovať", type="primary", use_container_width=True):
            all_items = []
            bar = st.progress(0)
            
            for i, f in enumerate(uploaded_files):
                try:
                    img = process_file(f)
                    for attempt in range(3):
                        try:
                            res = model.generate_content([
                                "Spracuj bloček do JSON. Polia: nazov, kategoria, vaha_g (odhad), kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g.", img
                            ])
                            txt = clean_json_response(res.text)
                            if txt:
                                all_items.extend(json.loads(txt))
                            break
                        except: time.sleep(2)
                except Exception as e: st.error(e)
                bar.progress((i+1)/len(uploaded_files))
            
            if all_items: st.session_state.scan_result = all_items

    if 'scan_result' in st.session_state:
        edited = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic", use_container_width=True)
        if st.button("📥 Pridať do môjho skladu", type="primary", use_container_width=True):
            add_to_inventory(edited.to_dict('records'), current_user) # Ukladáme s menom
            del st.session_state.scan_result
            st.toast("Naskladnené!", icon="✅")
            st.rerun()

# === TAB 3: SKLAD ===
with tab_storage:
    st.subheader(f"📦 Sklad používateľa {current_user}")
    df_inv = get_inventory(current_user)
    
    if not df_inv.empty:
        st.dataframe(
            df_inv[['nazov', 'vaha_g', 'kcal_100g']],
            column_config={
                "nazov": "Produkt",
                "vaha_g": st.column_config.NumberColumn("Váha (g)", format="%d g"),
                "kcal_100g": st.column_config.NumberColumn("Kcal/100g", format="%d")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Tu nič nie je.")

# === TAB 4: TRÉNER ===
with tab_coach:
    st.subheader("🤖 AI Poradca")
    if st.button("Poradiť", type="primary", use_container_width=True):
        df_log = get_today_log(current_user)
        df_inv = get_inventory(current_user)
        
        curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
        rem_kcal = target_kcal - curr_kcal
        
        with st.spinner("Analyzujem..."):
            prompt = f"""
            Si tréner. KLIENT: {current_user}, Cieľ: {goal}, Limit: {int(target_kcal)}, Zjedol: {int(curr_kcal)}.
            SKLAD: {df_inv[['nazov', 'vaha_g']].to_string() if not df_inv.empty else "Prázdno"}
            ÚLOHA: Zhodnoť deň a odporuč jedlo zo skladu.
            """
            try:
                res = coach_model.generate_content(prompt)
                st.info(res.text)
            except: st.error("Skús neskôr.")
