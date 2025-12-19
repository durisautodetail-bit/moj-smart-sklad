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
DB_FILE = "sklad_v3.db"  # Používame v3, aby fungoval aj nový profil

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
    
    # 1. Nová tabuľka USERS (Profil)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            gender TEXT,
            age INTEGER,
            weight REAL,
            height INTEGER,
            activity TEXT,
            goal TEXT,
            allergies TEXT,
            health_issues TEXT,
            last_updated TEXT
        )
    ''')

    # 2. Pôvodné tabuľky (Sklad a Log)
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

# --- FUNKCIE PRE PROFIL (NOVÉ) ---
def save_user_profile(username, gender, age, weight, height, activity, goal, allergies, health_issues):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
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

# --- FUNKCIE PRE SKLAD (PÔVODNÉ) ---
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

# TÚTO FUNKCIU SOM MINULE ZABUDOL VRÁTIŤ - PRETO NEŠLO MAZANIE
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
st.set_page_config(page_title="Smart Food v3.1", layout="wide", page_icon="🥗")
init_db()

# === 1. LOGIN ===
if 'username' not in st.session_state:
    st.session_state.username = None

if not st.session_state.username:
    st.title("🔐 Prihlásenie")
    name_input = st.text_input("Zadaj meno:")
    if st.button("Vstúpiť", type="primary"):
        if name_input:
            st.session_state.username = name_input
            st.rerun()
    st.stop()

current_user = st.session_state.username

# Načítanie profilu
db_profile = get_user_profile(current_user)
# Default hodnoty
default_gender = db_profile[1] if db_profile else "Muž"
default_age = db_profile[2] if db_profile else 30
default_weight = db_profile[3] if db_profile else 80.0
default_height = db_profile[4] if db_profile else 180
default_activity = db_profile[5] if db_profile else "Stredná"
default_goal = db_profile[6] if db_profile else "Udržiavať"
default_allergies = db_profile[7].split(",") if db_profile and db_profile[7] else []
default_health = db_profile[8] if db_profile else ""

# --- SIDEBAR ---
with st.sidebar:
    st.subheader(f"👤 {current_user}")
    if st.button("Odhlásiť"):
        st.session_state.username = None
        st.rerun()
    st.divider()
    if default_health:
        st.info(f"Zdravotný status:\n{default_health}")

# --- HLAVNÉ ZÁLOŽKY (TERAZ ICH JE 5) ---
tab_profile, tab_home, tab_scan, tab_storage, tab_coach = st.tabs(["🧬 Profil", "🏠 Prehľad", "➕ Skenovať", "📦 Sklad", "🤖 Tréner"])

# === SEGMENT 1: PROFIL (NOVÝ) ===
with tab_profile:
    st.header("🧬 Tvoj Bio-Profil")
    
    col_bio, col_med = st.columns([1, 1])
    
    with col_bio:
        st.subheader("Osobné údaje")
        p_gender = st.selectbox("Pohlavie", ["Muž", "Žena"], index=0 if default_gender=="Muž" else 1)
        p_age = st.number_input("Vek", 15, 99, default_age)
        p_weight = st.number_input("Váha (kg)", 40.0, 150.0, float(default_weight))
        p_height = st.number_input("Výška (cm)", 140, 220, default_height)
        p_act = st.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"], index=["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"].index(default_activity))
        p_goal = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"], index=["Udržiavať", "
