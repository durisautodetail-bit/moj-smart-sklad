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
DB_FILE = "sklad_v6_1.db"

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

def generate_progress_chart(start_weight, target_weight, is_premium):
    fig, ax = plt.subplots(figsize=(6, 3))
    diff = abs(start_weight - target_weight)
    weeks_needed = int(diff / 0.5) if diff > 0 else 1
    if weeks_needed < 4: weeks_needed = 4
    dates = [datetime.now(), datetime.now() + timedelta(weeks=weeks_needed)]
    weights = [start_weight, target_weight]
    
    color = '#FF4B4B' if is_premium else '#808080'
    label = 'Premium Plán' if is_premium else 'Odhad (Basic)'
    
    ax.plot(dates, weights, linestyle='--', marker='o', color=color, linewidth=2, label=label)
    ax.set_title(f"Plán cesty ({weeks_needed} týždňov)", fontsize=10)
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
            username TEXT PRIMARY KEY, gender TEXT, age INTEGER, weight REAL, height INTEGER,
            activity TEXT, goal TEXT, target_weight REAL, allergies TEXT, dislikes TEXT,      
            coach_style TEXT, archetype TEXT, health_issues TEXT, ai_strategy TEXT, 
            is_premium INTEGER DEFAULT 0,
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
    c.execute('''
        INSERT INTO users (username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, archetype, health_issues, ai_strategy, is_premium, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            gender=excluded.gender, age=excluded.age, weight=excluded.weight, height=excluded.height,
            activity=excluded.activity, goal=excluded.goal, target_weight=excluded.target_weight,
            allergies=excluded.allergies, dislikes=excluded.dislikes, coach_style=excluded.coach_style,
            archetype=excluded.archetype, health_issues=excluded.health_issues, ai_strategy=excluded.ai_strategy, 
            is_premium=excluded.is_premium, last_updated=excluded.last_updated
    ''', (
        data.get('username'), data.get('gender', 'Muž'), data.get('age', 30), data.get('weight', 80), 
        data.get('height', 180), data.get('activity', 'Stredná'), data.get('goal', 'Udržiavať'), 
        data.get('target_weight', 80), data.get('allergies', ''), data.get('dislikes', ''), 
        data.get('coach_style', 'Kamoš'), data.get('archetype', 'Neznámy'),
        data.get('health_issues', ''), data.get('ai_strategy', '...'), 
        data.get('is_premium', 0), today
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

def toggle_premium(username, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_premium=? WHERE username=?", (1 if status else 0, username))
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

# NOVÁ FUNKCIA PRE HROMADNÉ VARENIE
def cook_recipe_from_stock(ingredients_used, recipe_name, total_kcal, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Zápis do logu (ako jedno jedlo)
    c.execute('''INSERT INTO daily_log (owner, nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                 (owner, recipe_name, 0, total_kcal, 0, 0, 0, today)) # Makrá zatiaľ 0 ak ich AI nevypočíta presne
    
    # Odpočítanie zo skladu
    for ing in ingredients_used:
        item_id = ing['id']
        used_g = ing['used_g']
        
        c.execute("SELECT vaha_g FROM inventory WHERE id=? AND owner=?", (item_id, owner))
        row = c.fetchone()
        if row:
            current_w = row[0]
            new_w = current_w - used_g
            if new_w <= 0:
                c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
            else:
                c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_w, item_id))
                
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
st.set_page_config(page_title="Smart Food v6.1", layout="wide", page_icon="🥗")
init_db()

if 'active_tab' not in st.session_state: st.session_state.active_tab = 0
if 'show_bridge' not in st.session_state: st.session_state.show_bridge = False
if 'generated_recipes' not in st.session_state: st.session_state.generated_recipes = None

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

# === BRIDGE (PRECHOD) ===
if st.session_state.show_bridge and 'temp_profile_data' in st.session_state:
    st.balloons()
    data = st.session_state.temp_profile_data
    is_prem = data.get('is_premium', 0)
    
    st.title("🎉 Profil pripravený!")
    st.write("📉 **Tvoja cesta:**")
    try:
        fig = generate_progress_chart(data['weight'], data['target_weight'], is_prem)
        st.pyplot(fig)
    except: pass
    
    st.markdown("---")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("📸 Poďme naskladniť kuchyňu!", type="primary", use_container_width=True):
            st.session_state.active_tab = 2
            st.session_state.show_bridge = False
            st.rerun()
    with b2:
        if st.button("🏠 Ukáž mi čo navariť", type="secondary", use_container_width=True):
            st.session_state.active_tab = 0
            st.session_state.show_bridge = False
            st.rerun()
    st.stop()

db_profile = get_user_profile(current_user)
user_is_premium = bool(db_profile[14]) if db_profile and len(db_profile) > 14 else False

# === 2. ONBOARDING (BASIC / PREMIUM) ===
if not db_profile:
    st.title(f"👋 Ahoj {current_user}!")
    st.markdown("### Vyber si, ako chceš začať:")
    
    if "onboarding_choice" not in st.session_state: st.session_state.onboarding_choice = None

    if st.session_state.onboarding_choice is None:
        c1, c2 = st.columns(2)
        with c1:
            st.info("🟢 **Štandard**")
            st.write("Chcem hlavne poriadok v sklade a recepty.")
            if st.button("Začať Štandard", type="primary", use_container_width=True):
                st.session_state.onboarding_choice = "form"
                st.rerun()
        with c2:
            st.warning("💎 **Premium (Coach)**")
            st.write("Chcem aj psychológiu a vedenie.")
            if st.button("Vyskúšať Premium", type="secondary", use_container_width=True):
                st.session_state.onboarding_choice = "chat"
                st.rerun()
        st.stop()

    # FORMULÁR (BASIC)
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
                f_dislikes = st.text_input("Čo neľúbiš? (napr. huby, kôpor)")
            if st.form_submit_button("💾 Uložiť"):
                data = {
                    "username": current_user, "gender": f_gender, "age": f_age, "weight": f_weight, "height": f_height, 
                    "activity": f_activity, "goal": f_goal, "target_weight": f_weight - 5, "allergies": "", 
                    "dislikes": f_dislikes, "coach_style": "Stručný", "archetype": "Smart Cook", "health_issues": "", 
                    "ai_strategy": "Využívaj sklad.", "is_premium": 0
                }
                save_full_profile(data)
                st.session_state.temp_profile_data = data
                st.session_state.show_bridge = True
                st.rerun()
        st.stop()

    # CHAT (PREMIUM)
    if st.session_state.onboarding_choice == "chat":
        # ... (Kód pre chat ostáva rovnaký ako v minulej verzii, pre stručnosť tu nie je duplikovaný) ...
        # Pre demo účely rovno formulár s Premium flagom
        st.info("Pre demo účely použijeme formulár, ale uloží sa ako Premium.")
        with st.form("prem_setup"):
            p_dislikes = st.text_input("Čo neľúbiš?")
            if st.form_submit_button("Start Premium"):
                 data = {"username": current_user, "gender": "Muž", "age": 30, "weight": 80, "height": 180, "activity": "Stredná", "goal": "Chudnúť", "target_weight": 75, "allergies": "", "dislikes": p_dislikes, "coach_style": "Kamoš", "archetype": "Boss", "health_issues": "", "ai_strategy": "Full AI", "is_premium": 1}
                 save_full_profile(data)
                 st.session_state.temp_profile_data = data
                 st.session_state.show_bridge = True
                 st.rerun()
        st.stop()

# === 3. HLAVNÁ APLIKÁCIA ===

# Načítanie profilu
p_weight, p_dislikes = db_profile[3], db_profile[9]
p_target_kcal = 2000 # Zjednodušené pre Basic

# Sidebar
with st.sidebar:
    st.subheader(f"👤 {current_user}")
    if user_is_premium: st.success("💎 Premium")
    else: st.info("🟢 Basic")
    
    if st.button("Prepniť Plán (Test)"):
        toggle_premium(current_user, not user_is_premium)
        st.rerun()
    
    st.divider()
    if st.button("Odhlásiť"):
        st.session_state.clear()
        st.rerun()

tabs = st.tabs(["🍽️ Kuchyňa", "💬 Asistent", "➕ Skenovať", "📦 Sklad", "👤 Profil"])

if 'active_tab' in st.session_state and st.session_state.active_tab == 2:
    st.toast("Prejdi na záložku 'Skenovať'!")
    st.session_state.active_tab = 0 

# TAB 1: KUCHYŇA (SMART COOK)
with tabs[0]:
    df_inv = get_inventory(current_user)
    df_log = get_today_log(current_user)
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    
    # Dashboard dňa
    c1, c2 = st.columns([2,1])
    c1.progress(min(curr_kcal / p_target_kcal, 1.0), text=f"Dnes: {int(curr_kcal)} kcal")
    
    st.divider()
    st.subheader("👨‍🍳 Čo budeme variť?")
    
    if df_inv.empty:
        st.warning("Tvoj sklad je prázdny. Najprv niečo naskenuj v záložke 'Skenovať'.")
    else:
        # Tlačidlo na generovanie
        if st.button("✨ Navrhni 3 jedlá zo skladu", type="primary", use_container_width=True):
            with st.spinner("Šéfkuchár prezerá tvoj sklad..."):
                inv_json = df_inv[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                prompt = f"""
                Si kreatívny šéfkuchár. Máš tento SKLAD: {inv_json}.
                UŽÍVATEĽ NEĽÚBI: {p_dislikes}.
                
                Navrhni 3 RÔZNE recepty, ktoré sa dajú uvariť (hlavne) z týchto surovín.
                
                MUSÍŠ vrátiť JSON v tomto formáte:
                [
                  {{
                    "name": "Názov jedla (kreatívny)",
                    "desc": "Stručný popis (1 veta)",
                    "kcal": 500 (odhad),
                    "ingredients_used": [
                      {{"id": 1, "used_g": 100}}, (ID musí sedieť s ID v sklade!)
                      {{"id": 5, "used_g": 50}}
                    ]
                  }},
                  ... (ďalšie 2 recepty)
                ]
                """
                try:
                    res = model.generate_content(prompt)
                    json_text = clean_json_response(res.text)
                    st.session_state.generated_recipes = json.loads(json_text)
                except Exception as e: st.error(f"Chyba AI: {e}")

        # Zobrazenie receptov
        if st.session_state.generated_recipes:
            st.write("Vyber si, na čo máš chuť:")
            cols = st.columns(3)
            for i, recipe in enumerate(st.session_state.generated_recipes):
                with cols[i]:
                    st.markdown(f"### {recipe['name']}")
                    st.caption(recipe['desc'])
                    st.write(f"🔥 cca {recipe['kcal']} kcal")
                    
                    # Výpis surovín pre kontrolu
                    with st.expander("Suroviny"):
                        for ing in recipe['ingredients_used']:
                            # Nájdeme názov podľa ID v aktuálnom df
                            item_name = df_inv[df_inv['id'] == ing['id']]['nazov'].values[0] if not df_inv[df_inv['id'] == ing['id']].empty else f"ID {ing['id']}"
                            st.write(f"- {item_name}: {ing['used_g']}g")
                    
                    if st.button(f"Uvariť & Zjesť", key=f"cook_{i}", type="secondary", use_container_width=True):
                        cook_recipe_from_stock(recipe['ingredients_used'], recipe['name'], recipe['kcal'], current_user)
                        st.balloons()
                        st.toast(f"Dobrú chuť! Suroviny boli odpísané.", icon="🍲")
                        st.session_state.generated_recipes = None # Reset
                        st.rerun()

# TAB 2: ASISTENT (Soft Freemium)
with tabs[1]:
    st.header("💬 Max - Tvoj Asistent")
    if user_is_premium:
        # Plný chat kód...
        if "day_chat_history" not in st.session_state: st.session_state.day_chat_history = []
        for msg in st.session_state.day_chat_history:
            with st.chat_message(msg["role"]): st.write(msg["content"])
        with st.form("chat_form", clear_on_submit=True):
            u_in = st.text_area("Napíš správu...")
            if st.form_submit_button("Odoslať") and u_in:
                st.session_state.day_chat_history.append({"role":"user", "content":u_in})
                with st.chat_message("user"): st.write(u_in)
                # ... (AI volanie) ...
                with st.chat_message("ai"): st.write("Som Max (Premium). Odpovedám...")
                st.session_state.day_chat_history.append({"role":"ai", "content":"Som Max (Premium). Odpovedám..."})
    else:
        st.info("💡 **Tip:** Max ti v Basic verzii pomôže s faktami.")
        st.write("Môžeš sa pýtať na kalórie potravín alebo jednoduché otázky.")
        # Zjednodušený chat pre Basic
        q = st.text_input("Otázka na potraviny:")
        if q:
            st.write(f"🤖 Max: {q} je dobrá otázka. (V Basic režime odpovedám stručne).")
        
        st.markdown("---")
        st.caption("🔒 Pre hĺbkový koučing a psychológiu potrebuješ Premium.")

# TAB 3: SKENOVANIE
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

# TAB 4: SKLAD
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

# TAB 5: PROFIL
with tabs[4]:
    st.header("Profil")
    st.write(f"Meno: {current_user}")
    st.write(f"Nemám rád: {p_dislikes}")
    if user_is_premium:
        try:
            fig = generate_progress_chart(db_profile[3], db_profile[7], True)
            st.pyplot(fig)
        except: pass
