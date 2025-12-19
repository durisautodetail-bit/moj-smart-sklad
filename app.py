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
    # Načítanie kľúča z trezora
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    
    # Model Flash (Rýchly)
    model = genai.GenerativeModel("gemini-flash-latest")
    coach_model = genai.GenerativeModel("gemini-flash-latest")
    
except Exception as e:
    st.error(f"Chyba konfigurácie: {e}")

# --- OPTIMALIZÁCIA OBRÁZKA (ZRÝCHLENIE) ---
def optimize_image(image, max_width=800):
    """Zmenší obrázok na max 800px šírku pre rýchlejší prenos a AI analýzu"""
    width, height = image.size
    if width > max_width:
        ratio = max_width / width
        new_height = int(height * ratio)
        return image.resize((max_width, new_height))
    return image

# --- POMOCNÉ FUNKCIE ---
def clean_json_response(text):
    text = text.replace("```json", "").replace("```", "").strip()
    start_idx = text.find('[')
    end_idx = text.rfind(']')
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx:end_idx+1]
    return text

# --- DATABÁZA ---
def init_db():
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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

def add_to_inventory(items):
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('''
            INSERT INTO inventory (nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            item.get('nazov', 'Neznáme'), item.get('kategoria', 'Iné'), item.get('vaha_g', 0), 
            item.get('kcal_100g', 0), item.get('bielkoviny_100g', 0), 
            item.get('sacharidy_100g', 0), item.get('tuky_100g', 0), today
        ))
    conn.commit()
    conn.close()

def eat_item(item_id, grams_eaten):
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT * FROM inventory WHERE id=?", (item_id,))
    item = c.fetchone()
    
    if item:
        current_weight = item[3]
        ratio = grams_eaten / 100
        
        c.execute('''
            INSERT INTO daily_log (nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (item[1], grams_eaten, item[4]*ratio, item[5]*ratio, item[6]*ratio, item[7]*ratio, today))
        
        new_weight = current_weight - grams_eaten
        if new_weight <= 0:
            c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        else:
            c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_weight, item_id))
            
    conn.commit()
    conn.close()

def get_inventory():
    conn = sqlite3.connect('sklad.db')
    df = pd.read_sql_query("SELECT * FROM inventory", conn)
    conn.close()
    return df

def get_today_log():
    conn = sqlite3.connect('sklad.db')
    today = datetime.now().strftime("%Y-%m-%d")
    df = pd.read_sql_query("SELECT * FROM daily_log WHERE datum=?", conn, params=(today,))
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
    
    return optimize_image(img) # Tu aplikujeme zmenšenie

# --- UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food", layout="wide", page_icon="🥗")
init_db()

# --- HEADER & PROFIL (Schovaný v expanderi) ---
with st.expander("👤 Nastavenia Profilu & Cieľov"):
    c1, c2, c3 = st.columns(3)
    gender = c1.selectbox("Pohlavie", ["Muž", "Žena"])
    age = c1.number_input("Vek", 18, 99, 30)
    weight = c2.number_input("Váha (kg)", 40, 150, 80)
    height = c2.number_input("Výška (cm)", 140, 220, 180)
    activity = c3.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"])
    goal = c3.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"])

# Výpočty
factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
bmr = (10 * weight) + (6.25 * height) - (5 * age) + (5 if gender == "Muž" else -161)
tdee = bmr * factor[activity]
target_kcal = tdee - 500 if goal == "Chudnúť" else (tdee + 300 if goal == "Pribrať" else tdee)
target_b = (target_kcal * 0.30) / 4
target_s = (target_kcal * 0.40) / 4
target_t = (target_kcal * 0.30) / 9

# --- HLAVNÉ MENU (TABS) ---
tab_home, tab_scan, tab_storage, tab_coach = st.tabs(["🏠 Prehľad Dňa", "➕ Skenovať", "📦 Sklad", "🤖 Tréner"])

# === TAB 1: DASHBOARD (PREHĽAD) ===
with tab_home:
    st.markdown("### 📊 Dnešný Progress")
    df_log = get_today_log()
    
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    curr_b = df_log['prijate_b'].sum() if not df_log.empty else 0
    curr_s = df_log['prijate_s'].sum() if not df_log.empty else 0
    curr_t = df_log['prijate_t'].sum() if not df_log.empty else 0
    
    # Hlavný ukazovateľ - Batéria Kalórií
    left_kcal = int(target_kcal - curr_kcal)
    color = "green" if left_kcal > 0 else "red"
    st.markdown(f"""
    <div style="background-color: #f0f2f6; padding: 20px; border-radius: 10px; text-align: center;">
        <h2 style="margin:0; color: #31333F;">Zostáva ti: <span style="color:{color}">{left_kcal} kcal</span></h2>
        <p style="margin:0;">Z cieľa {int(target_kcal)} kcal</p>
    </div>
    """, unsafe_allow_html=True)
    st.progress(min(curr_kcal / target_kcal, 1.0))
    
    # Makrá v stĺpcoch
    c1, c2, c3 = st.columns(3)
    c1.metric("🥩 Bielkoviny", f"{int(curr_b)} / {int(target_b)}g", delta=int(target_b - curr_b))
    c2.metric("🍚 Sacharidy", f"{int(curr_s)} / {int(target_s)}g", delta=int(target_s - curr_s))
    c3.metric("🥑 Tuky", f"{int(curr_t)} / {int(target_t)}g", delta=int(target_t - curr_t))
    
    st.divider()
    
    # Rýchle jedenie zo skladu
    st.subheader("🍽️ Čo si ideš dať?")
    df_inv = get_inventory()
    if not df_inv.empty:
        col_food, col_gram, col_btn = st.columns([3, 2, 2])
        selected_food_name = col_food.selectbox("Vyber jedlo", df_inv['nazov'].unique(), label_visibility="collapsed")
        
        # Získame max váhu pre hint
        item_data = df_inv[df_inv['nazov'] == selected_food_name].iloc[0]
        grams = col_gram.number_input("Gramy", 1, int(item_data['vaha_g']), 100, label_visibility="collapsed")
        
        if col_btn.button("😋 Zjesť", type="primary", use_container_width=True):
            eat_item(int(item_data['id']), grams)
            st.toast("Zapísané! Dobrú chuť.", icon="🥗")
            time.sleep(0.5)
            st.rerun()
    else:
        st.info("Sklad je prázdny. Naskenuj nákup v záložke 'Skenovať'.")

    # História
    if not df_log.empty:
        with st.expander("📜 História dnešných jedál"):
            st.dataframe(df_log[['nazov', 'zjedene_g', 'prijate_kcal']], use_container_width=True)

# === TAB 2: SKENOVANIE (OPTIMALIZOVANÉ) ===
with tab_scan:
    st.subheader("📸 Nový nákup")
    uploaded_files = st.file_uploader("Nahraj bločky (Hromadný upload)", type=["jpg", "png", "pdf"], accept_multiple_files=True)
    
    if uploaded_files:
        if st.button("Analyzovať nákup 🚀", type="primary", use_container_width=True):
            all_items = []
            progress_bar = st.progress(0)
            status = st.empty()
            
            for i, f in enumerate(uploaded_files):
                status.text(f"Analyzujem {f.name} (Optimalizujem veľkosť)...")
                try:
                    img = process_file(f) # Tu sa deje optimalizácia
                    
                    # Retry logika pre stabilitu
                    for attempt in range(3):
                        try:
                            res = model.generate_content([
                                "Spracuj bloček do JSON. Polia: nazov, kategoria, vaha_g (odhad), kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g.", 
                                img
                            ])
                            txt = clean_json_response(res.text)
                            if txt:
                                items = json.loads(txt)
                                all_items.extend(items)
                            break
                        except ResourceExhausted:
                            time.sleep(5)
                        except Exception:
                            break
                            
                except Exception as e:
                    st.error(f"Chyba: {e}")
                
                progress_bar.progress((i+1)/len(uploaded_files))
            
            status.success("Hotovo!")
            if all_items:
                st.session_state.scan_result = all_items

    if 'scan_result' in st.session_state:
        st.write("Skontroluj a potvrď:")
        edited = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic", use_container_width=True)
        if st.button("📥 Uložiť do skladu", type="primary", use_container_width=True):
            add_to_inventory(edited.to_dict('records'))
            del st.session_state.scan_result
            st.toast("Naskladnené!", icon="✅")
            st.rerun()

# === TAB 3: SKLAD ===
with tab_storage:
    st.subheader("📦 Zásoby v kuchyni")
    df_inv = get_inventory()
    
    if not df_inv.empty:
        # Prehľadná karta pre každú kategóriu? Nie, radšej tabuľka pre prehľadnosť na mobile
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
        st.info("Prázdno. Utekaj na nákup!")

# === TAB 4: TRÉNER ===
with tab_coach:
    st.subheader("🤖 Tvoj AI Poradca")
    
    if st.button("💡 Poradiť čo jesť", type="primary", use_container_width=True):
        df_log = get_today_log()
        df_inv = get_inventory()
        
        curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
        rem_kcal = target_kcal - curr_kcal
        
        with st.spinner("Analyzujem tvoj deň..."):
            prompt = f"""
            Si stručný fitness tréner.
            KLIENT:
            - Cieľ: {goal}
            - Limit: {int(target_kcal)} kcal
            - Zjedol: {int(curr_kcal)} kcal (Zostáva: {int(rem_kcal)})
            
            SKLAD (Čo má doma):
            {df_inv[['nazov', 'vaha_g']].to_string() if not df_inv.empty else "Prázdno"}
            
            ÚLOHA:
            1. Krátke zhodnotenie dňa.
            2. Odporuč 1 konkrétne jedlo zo skladu na teraz.
            3. Buď motivujúci.
            """
            try:
                res = coach_model.generate_content(prompt)
                st.info(res.text)
            except:
                st.error("Tréner si dáva pauzu (API Limit). Skús o chvíľu.")
