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
DB_FILE = "sklad_v3.db"  # ZMENA: Nová verzia DB pre nové tabuľky

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-flash-latest")
    coach_model = genai.GenerativeModel("gemini-flash-latest")
except Exception as e:
    st.error(f"Chyba konfigurácie: {e}")

# --- POMOCNÉ FUNKCIE ---
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

# --- DATABÁZA ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 1. Tabuľka USERS (Trvalý profil)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            gender TEXT,
            age INTEGER,
            weight REAL,
            height INTEGER,
            activity TEXT,
            goal TEXT,
            allergies TEXT,  -- Uložené ako text (napr. "Laktóza, Orechy")
            health_issues TEXT, -- Výsledky z krvi (napr. "Nízke železo")
            last_updated TEXT
        )
    ''')

    # 2. Tabuľky pre Sklad a Log (ako predtým)
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

# --- FUNKCIE PRE USERS ---
def save_user_profile(username, gender, age, weight, height, activity, goal, allergies, health_issues):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    # Upsert (Vložiť alebo Aktualizovať)
    c.execute('''
        INSERT INTO users (username, gender, age, weight, height, activity, goal, allergies, health_issues, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            gender=excluded.gender,
            age=excluded.age,
            weight=excluded.weight,
            height=excluded.height,
            activity=excluded.activity,
            goal=excluded.goal,
            allergies=excluded.allergies,
            health_issues=excluded.health_issues,
            last_updated=excluded.last_updated
    ''', (username, gender, age, weight, height, activity, goal, allergies, health_issues, today))
    conn.commit()
    conn.close()

def get_user_profile(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    user = c.fetchone()
    conn.close()
    return user

# --- EXISTUJÚCE FUNKCIE SKLADU ---
def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                  (owner, item.get('nazov'), item.get('kategoria'), item.get('vaha_g'), item.get('kcal_100g'), item.get('bielkoviny_100g'), item.get('sacharidy_100g'), item.get('tuky_100g'), today))
    conn.commit()
    conn.close()

def eat_item(item_id, grams_eaten, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT * FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    item = c.fetchone()
    if item:
        ratio = grams_eaten / 100
        c.execute('''INSERT INTO daily_log (owner, nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (owner, item[2], grams_eaten, item[5]*ratio, item[6]*ratio, item[7]*ratio, item[8]*ratio, today))
        new_weight = item[4] - grams_eaten
        if new_weight <= 0: c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        else: c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_weight, item_id))
    conn.commit()
    conn.close()

def get_inventory(owner):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM inventory WHERE owner=?", conn, params=(owner,))
    conn.close()
    return df

def get_today_log(owner):
    conn = sqlite3.connect(DB_FILE)
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
st.set_page_config(page_title="Smart Food v3", layout="wide", page_icon="🧬")
init_db()

# === 1. LOGIN ===
if 'username' not in st.session_state:
    st.session_state.username = None

if not st.session_state.username:
    st.title("🧬 Prihlásenie")
    name_input = st.text_input("Zadaj meno:")
    if st.button("Vstúpiť"):
        if name_input:
            st.session_state.username = name_input
            st.rerun()
    st.stop()

current_user = st.session_state.username

# Načítanie profilu z DB (ak existuje)
db_profile = get_user_profile(current_user)
# db_profile štruktúra: (username, gender, age, weight, height, activity, goal, allergies, health_issues, last_updated)

# Predvyplnenie hodnôt
default_gender = db_profile[1] if db_profile else "Muž"
default_age = db_profile[2] if db_profile else 30
default_weight = db_profile[3] if db_profile else 80.0
default_height = db_profile[4] if db_profile else 180
default_activity = db_profile[5] if db_profile else "Stredná"
default_goal = db_profile[6] if db_profile else "Udržiavať"
default_allergies = db_profile[7].split(",") if db_profile and db_profile[7] else []
default_health = db_profile[8] if db_profile else ""

# --- SIDEBAR NAVIGÁCIA ---
with st.sidebar:
    st.title(f"👤 {current_user}")
    if st.button("Odhlásiť"):
        st.session_state.username = None
        st.rerun()
    st.divider()
    st.info(f"Zdravotný status: \n{default_health if default_health else 'Zatiaľ nezadané'}")

# --- HLAVNÉ ZÁLOŽKY ---
tab_profile, tab_home, tab_scan, tab_coach = st.tabs(["🧬 Profil & Zdravie", "🏠 Prehľad Dňa", "➕ Skenovať", "🤖 Tréner"])

# === SEGMENT 1: PROFIL & BIO-DATA ===
with tab_profile:
    st.header("🧬 Tvoj Bio-Profil")
    st.caption("Čím viac o sebe vyplníš, tým lepšie ti AI poradí.")
    
    col_bio, col_med = st.columns([1, 1])
    
    with col_bio:
        st.subheader("Základné údaje")
        p_gender = st.selectbox("Pohlavie", ["Muž", "Žena"], index=0 if default_gender=="Muž" else 1)
        p_age = st.number_input("Vek", 15, 99, default_age)
        p_weight = st.number_input("Váha (kg)", 40.0, 150.0, float(default_weight))
        p_height = st.number_input("Výška (cm)", 140, 220, default_height)
        p_act = st.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"], index=["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"].index(default_activity))
        p_goal = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"], index=["Udržiavať", "Chudnúť", "Pribrať"].index(default_goal))
        
        st.subheader("🚫 Intolerancie")
        p_allergies = st.multiselect("Čomu sa vyhýbaš?", 
                                     ["Laktóza", "Lepok", "Histamín", "Orechy", "Morské plody", "Sója"],
                                     default=default_allergies)

    with col_med:
        st.subheader("🩸 Analýza Krvi / Lekárska správa")
        st.write("Nahraj fotku alebo PDF výsledkov z laboratória. AI extrahuje kľúčové nedostatky.")
        
        med_file = st.file_uploader("Nahraj výsledky", type=["jpg", "png", "pdf"])
        p_health_issues = st.text_area("Aktuálne zdravotné záznamy (Editovateľné)", value=default_health, height=150)
        
        if med_file:
            if st.button("Analyzovať výsledky 🩺", type="primary"):
                with st.spinner("Dr. AI analyzuje tvoje výsledky..."):
                    img = process_file(med_file)
                    try:
                        prompt = """
                        Analyzuj túto lekársku správu/výsledky krvi.
                        Hľadaj LEN abnormality (nedostatok vitamínov, vysoký cholesterol, anémia, atď.).
                        Výstup napíš ako stručný zoznam bodov v slovenčine. Napr:
                        - Nízka hladina Železa
                        - Deficit Vitamínu D
                        Ignoruj všetko, čo je v norme.
                        """
                        res = model.generate_content([prompt, img])
                        p_health_issues = res.text  # Prepíšeme text area výsledkom
                        st.success("Analýza hotová! Skontroluj text nižšie a Ulož Profil.")
                        st.session_state.temp_health_res = res.text
                        st.rerun() # Refresh aby sa updateol text area (workaround)
                    except Exception as e:
                        st.error(f"Chyba: {e}")

    st.divider()
    if st.button("💾 ULOŽIŤ PROFIL", type="primary", use_container_width=True):
        allergies_str = ",".join(p_allergies)
        save_user_profile(current_user, p_gender, p_age, p_weight, p_height, p_act, p_goal, allergies_str, p_health_issues)
        st.toast("Profil úspešne uložený!", icon="✅")

# Výpočty cieľov (Dynamické podľa inputov)
factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
bmr = (10 * p_weight) + (6.25 * p_height) - (5 * p_age) + (5 if p_gender == "Muž" else -161)
tdee = bmr * factor[p_act]
target_kcal = tdee - 500 if p_goal == "Chudnúť" else (tdee + 300 if p_goal == "Pribrať" else tdee)
target_b = (target_kcal * 0.30) / 4
target_s = (target_kcal * 0.40) / 4
target_t = (target_kcal * 0.30) / 9

# === SEGMENT 2: PREHĽAD (HOME) ===
with tab_home:
    st.subheader(f"Dnešný prehľad")
    
    # Zobrazenie zdravotných varovaní
    if p_health_issues and len(p_health_issues) > 5:
        st.warning(f"⚠️ Zohľadňujem tvoje zdravotné záznamy: {p_health_issues.splitlines()[0]}...")
    
    df_log = get_today_log(current_user)
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    curr_b = df_log['prijate_b'].sum() if not df_log.empty else 0
    
    left = int(target_kcal - curr_kcal)
    st.progress(min(curr_kcal / target_kcal, 1.0))
    st.caption(f"{int(curr_kcal)} / {int(target_kcal)} kcal (Zostáva: {left})")
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Bielkoviny", f"{int(curr_b)}/{int(target_b)}g")
    # ... (zvyšok metrík)

    # Rýchle jedenie
    st.divider()
    st.write("🍽️ Rýchle jedenie zo skladu")
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        c_f, c_g, c_b = st.columns([3,2,2])
        sel_food = c_f.selectbox("Jedlo", df_inv['nazov'].unique(), label_visibility="collapsed")
        item = df_inv[df_inv['nazov'] == sel_food].iloc[0]
        gr = c_g.number_input("Gramy", 1, int(item['vaha_g']), 100, label_visibility="collapsed")
        if c_b.button("Zjesť", type="primary"):
            eat_item(int(item['id']), gr, current_user)
            st.toast("Mňam!", icon="🥗")
            st.rerun()

# === SEGMENT 3: SKENOVANIE (NÁKUP) ===
with tab_scan:
    st.header("📸 Naskladnenie")
    uples = st.file_uploader("Bločky", type=["jpg","png","pdf"], accept_multiple_files=True)
    if uples and st.button("Analyzovať"):
        res_items = []
        bar = st.progress(0)
        for i, f in enumerate(uples):
            try:
                img = process_file(f)
                resp = model.generate_content([
                    "JSON zoznam potravín: nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g.", img
                ])
                d = json.loads(clean_json_response(resp.text))
                res_items.extend(d)
            except: pass
            bar.progress((i+1)/len(uples))
        st.session_state.scan = res_items
    
    if 'scan' in st.session_state:
        edited = st.data_editor(pd.DataFrame(st.session_state.scan), num_rows="dynamic")
        if st.button("📥 Uložiť"):
            add_to_inventory(edited.to_dict('records'), current_user)
            del st.session_state.scan
            st.rerun()

# === SEGMENT 4: AI PORADCA (S BIO KONTEXTOM) ===
with tab_coach:
    st.header("🤖 Bio-Tréner")
    if st.button("Požiadať o radu", type="primary"):
        df_inv = get_inventory(current_user)
        inv_str = df_inv[['nazov', 'vaha_g']].to_string() if not df_inv.empty else "Nič"
        
        prompt = f"""
        Si nutričný expert.
        PROFIL KLIENTA:
        - Meno: {current_user} ({p_gender}, {p_age}r)
        - Cieľ: {p_goal}
        - Intolerancie: {", ".join(p_allergies)}
        - ZDRAVOTNÉ PROBLÉMY (KĽÚČOVÉ): {p_health_issues}
        
        DENNÝ STAV:
        - Zjedol: {int(curr_kcal)} / {int(target_kcal)} kcal
        
        SKLAD:
        {inv_str}
        
        ÚLOHA:
        1. Navrhni jedlo zo skladu, ktoré rešpektuje jeho zdravotné problémy (napr. ak má málo železa, nájdi niečo so železom. Ak má intoleranciu, vyhni sa jej).
        2. Ak v sklade nič vhodné nie je, povedz čo má dokúpiť.
        """
        with st.spinner("Analyzujem tvoju biológiu a sklad..."):
            try:
                r = coach_model.generate_content(prompt)
                st.markdown(r.text)
            except Exception as e: st.error(e)

