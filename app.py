import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from PIL import Image
import json
import fitz  # PyMuPDF
import io
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import time
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- KONFIGURÁCIA ---
DB_FILE = "sklad_v5_3.db" # Nová verzia pre istotu (čistý štít)

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
    if start_idx == -1: start_idx = text.find('{')
    end_idx = text.rfind(']')
    if end_idx == -1: end_idx = text.rfind('}')
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx:end_idx+1]
    return text

def generate_progress_chart(start_weight, target_weight, goal_type):
    """Vygeneruje graf cesty z bodu A do bodu B"""
    fig, ax = plt.subplots(figsize=(6, 3))
    
    # Logika času
    diff = abs(start_weight - target_weight)
    weeks_needed = int(diff / 0.5) if diff > 0 else 1
    if weeks_needed < 4: weeks_needed = 4
    
    dates = [datetime.now(), datetime.now() + timedelta(weeks=weeks_needed)]
    weights = [start_weight, target_weight]
    
    ax.plot(dates, weights, linestyle='--', marker='o', color='#FF4B4B', linewidth=2, label='Plán')
    ax.set_title(f"Tvoj plán ({weeks_needed} týždňov)", fontsize=10)
    ax.set_ylabel("Váha (kg)")
    ax.grid(True, linestyle=':', alpha=0.6)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    
    return fig

# --- DATABÁZA ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            gender TEXT,
            age INTEGER,
            weight REAL,
            height INTEGER,
            activity TEXT,
            goal TEXT,
            target_weight REAL,
            allergies TEXT,
            dislikes TEXT,      
            coach_style TEXT,
            archetype TEXT,
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
def save_full_profile(data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    username = data.get('username')
    c.execute('''
        INSERT INTO users (username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, archetype, health_issues, ai_strategy, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            gender=excluded.gender, age=excluded.age, weight=excluded.weight, height=excluded.height,
            activity=excluded.activity, goal=excluded.goal, target_weight=excluded.target_weight,
            allergies=excluded.allergies, dislikes=excluded.dislikes, coach_style=excluded.coach_style,
            archetype=excluded.archetype, health_issues=excluded.health_issues, ai_strategy=excluded.ai_strategy, 
            last_updated=excluded.last_updated
    ''', (
        username, 
        data.get('gender', 'Muž'), 
        data.get('age', 30), 
        data.get('weight', 80), 
        data.get('height', 180), 
        data.get('activity', 'Stredná'), 
        data.get('goal', 'Udržiavať'), 
        data.get('target_weight', 80), 
        data.get('allergies', ''), 
        data.get('dislikes', ''), 
        data.get('coach_style', 'Kamoš'),
        data.get('archetype', 'Neznámy'),
        data.get('health_issues', ''), 
        data.get('ai_strategy', 'Stratégia sa generuje...'), 
        today
    ))
    conn.commit()
    conn.close()

def update_weight(username, new_weight):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("UPDATE users SET weight=?, last_updated=? WHERE username=?", (new_weight, today, username))
    conn.commit()
    conn.close()

def get_user_profile(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    user = c.fetchone()
    conn.close()
    return user

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
st.set_page_config(page_title="Smart Food v5.3", layout="wide", page_icon="🥗")
init_db()

# Session State pre Navigáciu
if 'active_tab' not in st.session_state: st.session_state.active_tab = 0
if 'show_bridge' not in st.session_state: st.session_state.show_bridge = False

# === 1. LOGIN ===
if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    st.title("🔐 Prihlásenie")
    name_input = st.text_input("Tvoje meno:", placeholder="Napr. Jakub")
    if st.button("Vstúpiť", type="primary"):
        if name_input:
            st.session_state.username = name_input
            st.rerun()
    st.stop()

current_user = st.session_state.username

# === BRIDGE (PRECHOD) - Musí byť tu, aby sa zobrazil aj po reštarte ===
if st.session_state.show_bridge and 'temp_profile_data' in st.session_state:
    st.balloons()
    data = st.session_state.temp_profile_data
    
    st.title("🎉 Profil pripravený!")
    st.info(f"🧬 Tvoj Archetyp: **{data.get('archetype', 'Neznámy')}**")
    
    st.write("📉 **Tvoja cesta k úspechu:**")
    fig = generate_progress_chart(data['weight'], data['target_weight'], data['goal'])
    st.pyplot(fig)
    
    st.markdown("---")
    st.subheader("Čo chceš urobiť teraz?")
    
    b1, b2 = st.columns(2)
    with b1:
        if st.button("📸 Poďme naskladniť kuchyňu!", type="primary", use_container_width=True):
            st.session_state.active_tab = 2 # Scan tab
            st.session_state.show_bridge = False
            st.rerun()
    with b2:
        if st.button("🏠 Iba ukáž prehľad", type="secondary", use_container_width=True):
            st.session_state.active_tab = 0 # Home tab
            st.session_state.show_bridge = False
            st.rerun()
    st.stop()

# Načítanie profilu
db_profile = get_user_profile(current_user)

# === 2. ONBOARDING (AK NIE JE PROFIL) ===
if not db_profile:
    st.title(f"👋 Ahoj {current_user}!")
    st.markdown("### Ako si chceš nastaviť svoj profil?")
    
    if "onboarding_choice" not in st.session_state:
        st.session_state.onboarding_choice = None

    # RÁZCESTIE
    if st.session_state.onboarding_choice is None:
        c1, c2 = st.columns(2)
        with c1:
            st.info("⚡ **Nemám čas**")
            st.write("Rýchlo vyplním vek, váhu a cieľ.")
            if st.button("Vybrať FORMULÁR 📝", type="primary", use_container_width=True):
                st.session_state.onboarding_choice = "form"
                st.rerun()
        
        with c2:
            st.success("💎 **Chcem stratégiu na mieru**")
            st.write("Pokecám si s Maxom (AI).")
            if st.button("Vybrať POKEC S MAXOM 💬", type="primary", use_container_width=True):
                st.session_state.onboarding_choice = "chat"
                st.rerun()
        st.stop()

    # --- CESTA A: FORMULÁR ---
    if st.session_state.onboarding_choice == "form":
        st.subheader("⚡ Rýchle nastavenie")
        with st.form("quick_setup"):
            col1, col2 = st.columns(2)
            with col1:
                f_gender = st.selectbox("Pohlavie", ["Muž", "Žena"])
                f_age = st.number_input("Vek", 15, 99, 30)
                f_weight = st.number_input("Váha (kg)", 40.0, 180.0, 80.0)
                f_height = st.number_input("Výška (cm)", 120, 220, 180)
            with col2:
                f_activity = st.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká"])
                f_goal = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"])
            
            submitted = st.form_submit_button("💾 Uložiť")
            if submitted:
                # Generovanie dát
                data = {
                    "username": current_user, "gender": f_gender, "age": f_age, 
                    "weight": f_weight, "height": f_height, "activity": f_activity, 
                    "goal": f_goal, "target_weight": f_weight - 5 if f_goal == "Chudnúť" else f_weight + 5,
                    "allergies": "", "dislikes": "", "coach_style": "Stručný", 
                    "archetype": "Pragmatik ⚡",
                    "health_issues": "", "ai_strategy": "Základná stratégia: Dodržuj kalórie a hýb sa."
                }
                save_full_profile(data)
                st.session_state.temp_profile_data = data
                st.session_state.show_bridge = True
                st.rerun()

    # --- CESTA B: CHAT ---
    if st.session_state.onboarding_choice == "chat":
        st.subheader("💬 Interview s Maxom")
        if "onboarding_history" not in st.session_state:
            st.session_state.onboarding_history = [
                {"role": "model", "parts": [f"Čau {current_user}! Som Max. 🍎 Poďme nastaviť tvoj plán. Napíš mi naraz svoj **vek, výšku, váhu** a hlavne: **Prečo chceš zmeniť postavu?**"]}
            ]
        
        for msg in st.session_state.onboarding_history:
            with st.chat_message("ai" if msg["role"] == "model" else "user"): st.write(msg["parts"][0])
        
        user_input = st.chat_input("Odpíš Maxovi...")
        
        if user_input:
            with st.chat_message("user"): st.write(user_input)
            st.session_state.onboarding_history.append({"role": "user", "parts": [user_input]})
            
            with st.spinner("Max analyzuje..."):
                chat_context = "\n".join([f"{m['role']}: {m['parts'][0]}" for m in st.session_state.onboarding_history])
                system_prompt = f"""
                Si Max, nutričný kouč. Audit klienta {current_user}.
                Zisti postupne: 1. Fyzické parametre + Cieľ, 2. Životný štýl, 3. Chute.
                Prideľ "Archetyp". Ak máš všetko, napíš: "Ďakujem, mám všetko! Vytváram tvoj profil..."
                História: {chat_context}
                """
                try:
                    res = model.generate_content(system_prompt)
                    ai_reply = res.text
                    with st.chat_message("ai"): st.write(ai_reply)
                    st.session_state.onboarding_history.append({"role": "model", "parts": [ai_reply]})
                    
                    if "Ďakujem, mám všetko" in ai_reply:
                        with st.status("Generujem tvoju identitu...", expanded=True):
                            extract_prompt = f"""
                            Vytiahni JSON z chatu: {chat_context}
                            JSON: {{
                                "username": "{current_user}", "gender": "Muž/Žena", "age": int, "weight": float, "height": int,
                                "activity": "Stredná", "goal": "Chudnúť", "target_weight": float,
                                "allergies": "", "dislikes": "", "coach_style": "Kamoš",
                                "archetype": "Názov (2 slová)",
                                "health_issues": "", "ai_strategy": "5 viet stratégie."
                            }}
                            """
                            ext_res = model.generate_content(extract_prompt)
                            data = json.loads(clean_json_response(ext_res.text))
                            save_full_profile(data)
                            st.session_state.temp_profile_data = data
                            st.session_state.show_bridge = True
                            st.rerun()
                except Exception as e: st.error(e)
        st.stop()

# === 3. HLAVNÁ APLIKÁCIA ===

# RE-CHECK LOGIKA (BEZPEČNÁ VERZIA)
# Skontrolujeme, či máme dátum a či je platný
try:
    if db_profile and len(db_profile) > 14 and db_profile[14]:
        last_up = datetime.strptime(db_profile[14], "%Y-%m-%d")
        days_diff = (datetime.now() - last_up).days

        if days_diff >= 7:
            with st.expander(f"⚠️ **Týždenná kontrola s Maxom!** (Prešlo {days_diff} dní)", expanded=True):
                st.write(f"Naposledy sme mali **{db_profile[3]} kg**. Ako je to dnes?")
                new_w_input = st.number_input("Nová váha (kg):", value=db_profile[3], key="recheck_w")
                if st.button("Aktualizovať váhu"):
                    update_weight(current_user, new_w_input)
                    st.toast("Váha aktualizovaná! Max upravuje plán...", icon="🔄")
                    time.sleep(1)
                    st.rerun()
except Exception as e:
    # Ak sa dátum nedá prečítať, len ticho preskočíme re-check (aby appka nepadla)
    pass

# Načítanie profilu
p_weight, p_height, p_age, p_gender = db_profile[3], db_profile[4], db_profile[2], db_profile[1]
p_act, p_goal, p_strat, p_arch = db_profile[5], db_profile[6], db_profile[13], db_profile[11]
target_w = db_profile[7]

# Sidebar
with st.sidebar:
    st.subheader(f"👤 {current_user}")
    st.caption(f"Archetyp: **{p_arch}**")
    st.progress((p_weight - target_w)/p_weight if p_goal=="Chudnúť" else 0, text="Cieľ")
    if st.button("Odhlásiť"):
        st.session_state.clear()
        st.rerun()

# Výpočty
factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
bmr = (10 * p_weight) + (6.25 * p_height) - (5 * p_age) + (5 if p_gender == "Muž" else -161)
tdee = bmr * factor.get(p_act, 1.375)
target_kcal = tdee - 500 if p_goal == "Chudnúť" else (tdee + 300 if p_goal == "Pribrať" else tdee)
target_b = (target_kcal * 0.30) / 4

# TABS
tabs = st.tabs(["🏠 Prehľad", "💬 Max (AI)", "➕ Skenovať", "📦 Sklad", "👤 Profil"])

if 'active_tab' in st.session_state and st.session_state.active_tab == 2:
    st.toast("Prejdi na záložku 'Skenovať' pre naskladnenie!", icon="point_up")
    st.session_state.active_tab = 0 

# --- TAB 1: PREHĽAD ---
with tabs[0]:
    if p_strat:
        with st.expander(f"📋 Stratégia ({p_arch})", expanded=False): st.write(p_strat)
    
    df_log = get_today_log(current_user)
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    left = int(target_kcal - curr_kcal)
    color = "green" if left > 0 else "red"
    
    st.markdown(f"<div style='background-color:#f0f2f6;padding:15px;border-radius:10px;text-align:center;'><h2>Zostáva: <span style='color:{color}'>{left} kcal</span></h2><p>Cieľ: {int(target_kcal)}</p></div>", unsafe_allow_html=True)
    st.progress(min(curr_kcal / target_kcal, 1.0))
    
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
    else: st.info("Sklad je prázdny.")

# --- TAB 2: AI ASISTENT ---
with tabs[1]:
    st.header("💬 Max - Tvoj Asistent")
    if "day_chat_history" not in st.session_state: st.session_state.day_chat_history = []
    for msg in st.session_state.day_chat_history:
        with st.chat_message(msg["role"]): st.write(msg["content"])
            
    user_msg = st.chat_input("Pýtaj sa Maxa...")
    if user_msg:
        st.session_state.day_chat_history.append({"role": "user", "content": user_msg})
        with st.chat_message("user"): st.write(user_msg)
        with st.spinner("Max premýšľa..."):
            df_inv = get_inventory(current_user)
            inv_str = df_inv[['nazov', 'vaha_g']].to_string() if not df_inv.empty else "Prázdno"
            prompt = f"Si Max ({p_arch}). KLIENT: {current_user}, {p_goal}. STRATÉGIA: {p_strat}. ZJEDOL: {int(curr_kcal)}/{int(target_kcal)}. SKLAD: {inv_str}. OTÁZKA: {user_msg}"
            try:
                res = coach_model.generate_content(prompt)
                st.session_state.day_chat_history.append({"role": "ai", "content": res.text})
                with st.chat_message("ai"): st.write(res.text)
            except Exception as e: st.error(e)

# --- TAB 3: SKENOVANIE ---
with tabs[2]:
    st.header("📸 Skenovanie")
    uples = st.file_uploader("Bločky", type=["jpg", "png", "pdf"], accept_multiple_files=True)
    if uples and st.button("Analyzovať", type="primary"):
        all_items = []
        bar = st.progress(0)
        for i, f in enumerate(uples):
            try:
                img = process_file(f)
                res = model.generate_content(["JSON zoznam: nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g.", img])
                all_items.extend(json.loads(clean_json_response(res.text)))
            except: pass
            bar.progress((i+1)/len(uples))
        st.session_state.scan_result = all_items
    if 'scan_result' in st.session_state:
        edited = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("📥 Naskladniť", type="primary"):
            add_to_inventory(edited.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.rerun()

# --- TAB 4: SKLAD ---
with tabs[3]:
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        df_inv['Vybrať'] = False
        edited = st.data_editor(df_inv[['Vybrať','id','nazov','vaha_g','kcal_100g']], use_container_width=True, hide_index=True)
        sel = edited[edited['Vybrať']==True]
        if not sel.empty and st.button(f"🗑️ Vyhodiť ({len(sel)})", type="secondary"):
            for i, r in sel.iterrows(): delete_item(r['id'])
            st.rerun()
    else: st.info("Sklad je prázdny.")

# --- TAB 5: PROFIL ---
with tabs[4]:
    st.header("Tvoj Profil")
    st.info(f"Archetyp: **{p_arch}**")
    st.write(f"Váha: {p_weight} kg -> {target_w} kg")
    # Zobrazenie grafu
    fig = generate_progress_chart(p_weight, target_w, p_goal)
    st.pyplot(fig)
