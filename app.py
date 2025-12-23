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
DB_FILE = "sklad_v4_1.db" # Nová DB pre nové polia

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
    
    # 1. USERS - Pridané: target_weight, dislikes, coach_style
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            gender TEXT,
            age INTEGER,
            weight REAL,
            height INTEGER,
            activity TEXT,
            goal TEXT,
            target_weight REAL, -- NOVÉ
            allergies TEXT,
            dislikes TEXT,      -- NOVÉ (čo mi nechutí)
            coach_style TEXT,   -- NOVÉ (osobnosť)
            health_issues TEXT,
            ai_strategy TEXT,
            last_updated TEXT
        )
    ''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, kategoria TEXT, vaha_g REAL, kcal_100g REAL, bielkoviny_100g REAL, sacharidy_100g REAL, tuky_100g REAL, datum_pridania TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_log (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, zjedene_g REAL, prijate_kcal REAL, prijate_b REAL, prijate_s REAL, prijate_t REAL, datum TEXT)''')
    conn.commit()
    conn.close()

# --- DB FUNKCIE ---
def save_user_profile(username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, health_issues, ai_strategy):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''
        INSERT INTO users (username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, health_issues, ai_strategy, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            gender=excluded.gender, age=excluded.age, weight=excluded.weight, height=excluded.height,
            activity=excluded.activity, goal=excluded.goal, target_weight=excluded.target_weight,
            allergies=excluded.allergies, dislikes=excluded.dislikes, coach_style=excluded.coach_style,
            health_issues=excluded.health_issues, ai_strategy=excluded.ai_strategy, last_updated=excluded.last_updated
    ''', (username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, health_issues, ai_strategy, today))
    conn.commit()
    conn.close()

def get_user_profile(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    user = c.fetchone()
    conn.close()
    return user

# ... (Ostatné DB funkcie: add_to_inventory, eat_item, delete_item, get_inventory, get_today_log ostávajú rovnaké ako v minulej verzii) ...
def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (owner, item.get('nazov'), item.get('kategoria'), item.get('vaha_g'), item.get('kcal_100g'), item.get('bielkoviny_100g'), item.get('sacharidy_100g'), item.get('tuky_100g'), today))
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
        c.execute('''INSERT INTO daily_log (owner, nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (owner, item[2], grams_eaten, item[5]*ratio, item[6]*ratio, item[7]*ratio, item[8]*ratio, today))
        new_weight = item[4] - grams_eaten
        if new_weight <= 0: c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        else: c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_weight, item_id))
    conn.commit()
    conn.close()

def delete_item(item_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
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
st.set_page_config(page_title="Smart Food v4.1", layout="wide", page_icon="🧬")
init_db()

# === LOGIN ===
if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    st.title("🔐 Prihlásenie")
    name_input = st.text_input("Zadaj meno:")
    if st.button("Vstúpiť", type="primary"):
        if name_input:
            st.session_state.username = name_input
            st.rerun()
    st.stop()

current_user = st.session_state.username
db_profile = get_user_profile(current_user)

# Helper pre bezpečné načítanie indexov
def safe_get(idx, default): return db_profile[idx] if db_profile and len(db_profile) > idx else default

# Načítanie dát
# DB Struktura: 0:user, 1:gender, 2:age, 3:weight, 4:height, 5:activity, 6:goal, 7:target_w, 8:al, 9:dislikes, 10:style, 11:health, 12:strat, 13:update
health_text = st.session_state.temp_health if 'temp_health' in st.session_state else safe_get(11, "")
strategy_text = st.session_state.temp_strategy if 'temp_strategy' in st.session_state else safe_get(12, "")

default_gender = safe_get(1, "Muž")
default_age = safe_get(2, 30)
default_weight = safe_get(3, 80.0)
default_height = safe_get(4, 180)
default_activity = safe_get(5, "Stredná")
default_goal = safe_get(6, "Udržiavať")
default_target_w = safe_get(7, 75.0)
default_allergies = safe_get(8, "").split(",") if safe_get(8, "") else []
default_dislikes = safe_get(9, "")
default_style = safe_get(10, "Láskavý Motivátor")

# --- SIDEBAR ---
with st.sidebar:
    st.subheader(f"👤 {current_user}")
    if st.button("Odhlásiť"):
        st.session_state.username = None
        for k in ['temp_health', 'temp_strategy']: 
            if k in st.session_state: del st.session_state[k]
        st.rerun()

# --- TABS ---
tab_profile, tab_home, tab_scan, tab_storage, tab_coach = st.tabs(["🧬 Profil", "🏠 Prehľad", "➕ Skenovať", "📦 Sklad", "🤖 Tréner"])

# === TAB 1: PROFIL MAX ===
with tab_profile:
    st.header("🧬 Tvoj Komplexný Profil")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.subheader("1. Fyzické parametre")
        p_gender = st.selectbox("Pohlavie", ["Muž", "Žena"], index=0 if default_gender=="Muž" else 1)
        p_age = st.number_input("Vek", 15, 99, default_age)
        p_weight = st.number_input("Aktuálna Váha (kg)", 40.0, 200.0, float(default_weight))
        p_target_w = st.number_input("🎯 Cieľová Váha (kg)", 40.0, 200.0, float(default_target_w))
        p_height = st.number_input("Výška (cm)", 100, 250, default_height)
        
    with col2:
        st.subheader("2. Režim & Preferencie")
        p_act = st.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"], index=["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"].index(default_activity))
        p_goal = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"], index=["Udržiavať", "Chudnúť", "Pribrať"].index(default_goal))
        p_style = st.selectbox("🎭 Osobnosť Trénera", ["Prísny Vojenče", "Láskavý Motivátor", "Stručný Analytik"], index=["Prísny Vojenče", "Láskavý Motivátor", "Stručný Analytik"].index(default_style))
        st.caption("*Toto ovplyvní ako s tebou bude AI komunikovať.*")

    with col3:
        st.subheader("3. Obmedzenia")
        p_allergies = st.multiselect("Alergie / Intolerancie", ["Laktóza", "Lepok", "Histamín", "Orechy", "Morské plody", "Sója"], default=default_allergies)
        p_dislikes = st.text_area("🤮 Čo NEĽÚBIŠ? (napr. ryby, kôpor)", value=default_dislikes, height=100)
    
    st.divider()
    
    # METABOLICKÝ DASHBOARD (Vizuálna kalkulačka)
    factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
    bmr = (10 * p_weight) + (6.25 * p_height) - (5 * p_age) + (5 if p_gender == "Muž" else -161)
    tdee = bmr * factor[p_act]
    bmi = p_weight / ((p_height/100)**2)
    
    st.subheader("⚡ Tvoj Metabolický Motor")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("BMI Index", f"{bmi:.1f}", help="Body Mass Index")
    m2.metric("BMR (Kľud)", f"{int(bmr)} kcal", help="Koľko spáliš, ak budeš len ležať")
    m3.metric("TDEE (Pohyb)", f"{int(tdee)} kcal", help="Koľko spáliš reálne s aktivitou")
    
    if p_goal == "Chudnúť": target_kcal = tdee - 500
    elif p_goal == "Pribrať": target_kcal = tdee + 300
    else: target_kcal = tdee
    
    m4.metric("🎯 Tvoj Cieľ", f"{int(target_kcal)} kcal", delta=f"{int(target_kcal - tdee)} kcal")
    
    st.divider()
    
    # 4. LEKÁRSKA SEKCIA
    st.subheader("4. Zdravotná Analýza")
    med_file = st.file_uploader("Nahraj lekársku správu (PDF/IMG)", type=["jpg", "png", "pdf"])
    if med_file and st.button("Analyzovať Krv 🩺", type="primary"):
        with st.spinner("AI analyzuje bio-markery..."):
            try:
                img = process_file(med_file)
                res = model.generate_content(["Analyzuj lekársku správu. Vypíš len abnormality. Napr: Nízke železo, Vysoký cholesterol.", img])
                st.session_state.temp_health = res.text
                st.rerun()
            except Exception as e: st.error(e)
    p_health_issues = st.text_area("Výsledok analýzy:", value=health_text, height=80)
    
    st.divider()
    
    # 5. GENERÁCIA STRATÉGIE
    if st.button("🤖 Vygenerovať Stratégiu na mieru", type="primary", use_container_width=True):
        with st.spinner("Spracovávam všetky vstupy (váha, chuť, zdravie, psychika)..."):
            prompt = f"""
            Si nutričný tréner s osobnosťou: {p_style}.
            KLIENT: {p_gender}, {p_age}r, {p_weight}kg (BMI: {bmi:.1f}).
            CIEĽ: Zmena z {p_weight}kg na {p_target_w}kg ({p_goal}).
            ALERGIE: {p_allergies}. NEMÁ RÁD: {p_dislikes}.
            ZDRAVIE: {p_health_issues}.
            METABOLIZMUS: BMR {int(bmr)}, TDEE {int(tdee)}.
            
            ÚLOHA:
            1. Oslov klienta v štýle tvojej osobnosti ({p_style}).
            2. Vypočítaj, ako dlho asi potrvá dostať sa na {p_target_w}kg zdravým tempom.
            3. Nastav 3 hlavné pravidlá (zohľadni, čo nemá rád a zdravotný stav).
            """
            try:
                res = coach_model.generate_content(prompt)
                st.session_state.temp_strategy = res.text
                st.rerun()
            except Exception as e: st.error(f"Chyba AI: {e}")

    if strategy_text:
        st.success(f"📋 **TVOJA STRATÉGIA:**\n\n{strategy_text}")
    
    # ULOŽENIE
    if st.button("💾 ULOŽIŤ PROFIL", type="secondary", use_container_width=True):
        al_str = ",".join(p_allergies)
        fin_strat = st.session_state.temp_strategy if 'temp_strategy' in st.session_state else strategy_text
        fin_health = st.session_state.temp_health if 'temp_health' in st.session_state else p_health_issues
        
        save_user_profile(current_user, p_gender, p_age, p_weight, p_height, p_act, p_goal, p_target_w, al_str, p_dislikes, p_style, fin_health, fin_strat)
        st.toast("Profil uložený!", icon="✅")

# Výpočet makier pre ďalšie taby
target_b = (target_kcal * 0.30) / 4

# === TAB 2: PREHĽAD ===
with tab_home:
    # Zobrazenie stratégie
    if strategy_text:
        with st.expander(f"📋 TVOJ PLÁN ({default_style})", expanded=False): st.write(strategy_text)
    
    if health_text: st.error(f"⚠️ Zdravotné záznamy: {health_text}")

    df_log = get_today_log(current_user)
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    curr_b = df_log['prijate_b'].sum() if not df_log.empty else 0
    left = int(target_kcal - curr_kcal)
    color = "green" if left > 0 else "red"
    
    st.markdown(f"<div style='background-color:#f0f2f6;padding:15px;border-radius:10px;text-align:center;'><h2>Zostáva: <span style='color:{color}'>{left} kcal</span></h2><p>Cieľ: {int(target_kcal)}</p></div>", unsafe_allow_html=True)
    st.progress(min(curr_kcal / target_kcal, 1.0))
    st.metric("Bielkoviny", f"{int(curr_b)}/{int(target_b)}g")
    
    st.divider()
    st.subheader("🍽️ Rýchle jedenie")
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        c1, c2, c3 = st.columns([3,2,2])
        sel = c1.selectbox("Jedlo", df_inv['nazov'].unique(), label_visibility="collapsed")
        item = df_inv[df_inv['nazov'] == sel].iloc[0]
        gr = c2.number_input("Gramy", 1, int(item['vaha_g']), 100, label_visibility="collapsed")
        if c3.button("Zjesť", type="primary", use_container_width=True):
            eat_item(int(item['id']), gr, current_user)
            st.toast("Zapísané!", icon="🥗")
            st.rerun()
    else: st.info("Prázdny sklad.")

# === TAB 3: SKENOVANIE ===
with tab_scan:
    uples = st.file_uploader("Bločky", type=["jpg", "png", "pdf"], accept_multiple_files=True)
    if uples and st.button("Analyzovať", type="primary", use_container_width=True):
        all_items = []
        bar = st.progress(0)
        for i, f in enumerate(uples):
            try:
                img = process_file(f)
                res = model.generate_content(["Spracuj bloček do JSON: nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g.", img])
                all_items.extend(json.loads(clean_json_response(res.text)))
            except Exception as e: st.error(f"Chyba: {e}")
            bar.progress((i+1)/len(uples))
        st.session_state.scan_result = all_items
    if 'scan_result' in st.session_state:
        edited = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic", use_container_width=True)
        if st.button("📥 Naskladniť", type="primary", use_container_width=True):
            add_to_inventory(edited.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.toast("Uložené!", icon="✅")
            st.rerun()

# === TAB 4: SKLAD ===
with tab_storage:
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        df_inv['Vybrať'] = False
        edited = st.data_editor(df_inv[['Vybrať','id','nazov','vaha_g','kcal_100g']], use_container_width=True, hide_index=True)
        sel = edited[edited['Vybrať']==True]
        if not sel.empty and st.button(f"🗑️ Vyhodiť ({len(sel)})", type="secondary"):
            for i, r in sel.iterrows(): delete_item(r['id'])
            st.rerun()
    else: st.info("Sklad je prázdny.")

# === TAB 5: TRÉNER ===
with tab_coach:
    if st.button("Poradiť", type="primary", use_container_width=True):
        df_inv = get_inventory(current_user)
        inv_str = df_inv[['nazov', 'vaha_g']].to_string() if not df_inv.empty else "Nič"
        
        # Tréner používa osobnosť aj dislajky
        prompt = f"""
        Si expert s osobnosťou: {default_style}. KLIENT: {current_user}.
        STRATÉGIA: {strategy_text}.
        NEMÁ RÁD: {default_dislikes}.
        VAROVANIA: {health_text}.
        DENNÝ STAV: {int(curr_kcal)} / {int(target_kcal)} kcal.
        SKLAD: {inv_str}.
        
        1. Zhodnoť deň (v tvojom štýle).
        2. Odporuč jedlo zo skladu (ak mu niečo nechutí, nenúť ho!).
        """
        try:
            with st.spinner(f"Analyzujem..."):
                st.markdown(coach_model.generate_content(prompt).text)
        except Exception as e: st.error(f"Chyba: {e}")
