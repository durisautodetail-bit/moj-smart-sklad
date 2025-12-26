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
import matplotlib.dates as mdates

# --- KONFIGURÁCIA ---
DB_FILE = "sklad_v6_6.db"

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

def generate_progress_chart(start_weight, current_weight, target_weight, goal_type):
    fig, ax = plt.subplots(figsize=(6, 2.5))
    weights = [start_weight, current_weight, target_weight]
    labels = ["Štart", "Teraz", "Cieľ"]
    colors = ['#808080', '#FF4B4B', '#4CAF50']
    ax.bar(labels, weights, color=colors, alpha=0.8)
    min_w, max_w = min(weights), max(weights)
    ax.set_ylim(min_w - 5, max_w + 5)
    ax.grid(axis='y', linestyle=':', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    for i, v in enumerate(weights):
        ax.text(i, v + 0.5, f"{v} kg", ha='center', fontweight='bold', fontsize=9)
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
            last_updated TEXT, start_weight REAL
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
    start_w = data.get('weight', 80)
    c.execute('''
        INSERT INTO users (username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, archetype, health_issues, ai_strategy, is_premium, last_updated, start_weight)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        data.get('is_premium', 0), today, start_w
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

def add_item_manual(owner, nazov, vaha, kategoria):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                 (owner, nazov, kategoria, vaha, 100, 5, 10, 5, today)) 
    conn.commit()
    conn.close()

def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (owner, item.get('nazov'), item.get('kategoria'), item.get('vaha_g'), item.get('kcal_100g'), item.get('bielkoviny_100g'), item.get('sacharidy_100g'), item.get('tuky_100g'), today))
    conn.commit()
    conn.close()

def cook_recipe_from_stock(ingredients_used, recipe_name, total_kcal, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''INSERT INTO daily_log (owner, nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (owner, recipe_name, 0, total_kcal, 0, 0, 0, today))
    for ing in ingredients_used:
        if ing.get('id'):
            item_id = ing['id']
            used_g = ing['amount_g']
            c.execute("SELECT vaha_g FROM inventory WHERE id=? AND owner=?", (item_id, owner))
            row = c.fetchone()
            if row:
                current_w = row[0]
                new_w = current_w - used_g
                if new_w <= 0: c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
                else: c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_w, item_id))
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

def get_history_log(owner, limit=100):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT datum, nazov, prijate_kcal FROM daily_log WHERE owner=? ORDER BY id DESC LIMIT ?", conn, params=(owner, limit))
    conn.close()
    return df

def get_audit_data(owner):
    conn = sqlite3.connect(DB_FILE)
    limit_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    df = pd.read_sql_query("SELECT datum, nazov FROM daily_log WHERE owner=? AND datum >= ?", conn, params=(owner, limit_date))
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
st.set_page_config(page_title="Smart Food v6.6", layout="wide", page_icon="🥗")
init_db()

# Session State & WIZARD VARIABLES
if 'active_tab' not in st.session_state: st.session_state.active_tab = 0
if 'show_bridge' not in st.session_state: st.session_state.show_bridge = False
if 'generated_recipes' not in st.session_state: st.session_state.generated_recipes = None
if 'view_recipe' not in st.session_state: st.session_state.view_recipe = None
if 'audit_result' not in st.session_state: st.session_state.audit_result = None

# Wizard State
if 'wizard_step' not in st.session_state: st.session_state.wizard_step = 0
if 'wizard_plan' not in st.session_state: st.session_state.wizard_plan = [] # Uložené vybrané jedlá
if 'wizard_config' not in st.session_state: st.session_state.wizard_config = None # Nastavenia (dni, jedlá)
if 'wizard_options' not in st.session_state: st.session_state.wizard_options = None # Aktuálne 3 možnosti na výber

# === 1. LOGIN ===
if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    st.title("🥗 Smart Food")
    name_input = st.text_input("Meno:", placeholder="Napr. Jakub")
    if st.button("Vstúpiť", type="primary"):
        if name_input:
            st.session_state.username = name_input
            st.rerun()
    st.stop()

current_user = st.session_state.username

# === BRIDGE ===
if st.session_state.show_bridge and 'temp_profile_data' in st.session_state:
    st.balloons()
    data = st.session_state.temp_profile_data
    st.title("🎉 Profil hotový!")
    try:
        fig = generate_progress_chart(data['weight'], data['weight'], data['target_weight'], data['goal'])
        st.pyplot(fig)
    except: pass
    if st.button("Prejsť do aplikácie ➡️", type="primary"):
        st.session_state.active_tab = 0
        st.session_state.show_bridge = False
        st.rerun()
    st.stop()

db_profile = get_user_profile(current_user)
user_is_premium = bool(db_profile[14]) if db_profile and len(db_profile) > 14 else False

# === 2. ONBOARDING ===
if not db_profile:
    st.title(f"👋 Ahoj {current_user}!")
    with st.form("quick_setup"):
        col1, col2 = st.columns(2)
        with col1:
            f_gender = st.selectbox("Pohlavie", ["Muž", "Žena"])
            f_age = st.number_input("Vek", 15, 99, 30)
            f_weight = st.number_input("Váha (kg)", 40.0, 180.0, 80.0, step=0.5)
            f_height = st.number_input("Výška (cm)", 120, 220, 180)
        with col2:
            f_activity = st.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká"])
            f_goal = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"])
            f_dislikes = st.text_input("Čo neľúbiš?", placeholder="napr. huby, ryby")
        if st.form_submit_button("Uložiť a spustiť 🚀"):
            data = {
                "username": current_user, "gender": f_gender, "age": f_age, "weight": f_weight, "height": f_height, 
                "activity": f_activity, "goal": f_goal, "target_weight": f_weight - 5, "allergies": "", 
                "dislikes": f_dislikes, "coach_style": "Kamoš", "archetype": "User", "health_issues": "", 
                "ai_strategy": "Využívaj sklad.", "is_premium": 0
            }
            save_full_profile(data)
            st.session_state.temp_profile_data = data
            st.session_state.show_bridge = True
            st.rerun()
    st.stop()

# --- MAIN APP ---
p_weight = db_profile[3]
p_start_weight = db_profile[16] if len(db_profile) > 16 and db_profile[16] else p_weight
p_target_w = db_profile[7]
p_goal = db_profile[6]
p_dislikes = db_profile[9]
p_arch = db_profile[11]

factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
tdee = (10 * p_weight + 6.25 * db_profile[4] - 5 * db_profile[2] + (5 if db_profile[1] == "Muž" else -161)) * factor.get(db_profile[5], 1.375)
target_kcal = tdee - 500 if p_goal == "Chudnúť" else (tdee + 300 if p_goal == "Pribrať" else tdee)

tabs = st.tabs(["📊 Prehľad", "👨‍🍳 Kuchyňa", "📦 Sklad", "➕ Skenovať", "👤 Profil & Audit"])

# === TAB 1: PREHĽAD ===
with tabs[0]:
    st.subheader("👋 Vitaj späť!")
    c1, c2, c3 = st.columns(3)
    df_log = get_today_log(current_user)
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    c1.metric("Aktuálna váha", f"{p_weight} kg", delta=f"{p_weight - p_start_weight:.1f} kg", delta_color="inverse")
    c2.metric("Cieľ", f"{p_target_w} kg")
    c3.metric("Dnes prijaté", f"{int(curr_kcal)} kcal", f"z {int(target_kcal)}")
    st.markdown("---")
    col_graph, col_input = st.columns([2, 1])
    with col_graph:
        try:
            fig = generate_progress_chart(p_start_weight, p_weight, p_target_w, p_goal)
            st.pyplot(fig)
        except Exception as e: st.error(f"Graf error: {e}")
    with col_input:
        with st.container(border=True):
            st.caption("⚖️ Aktualizácia váhy")
            new_w = st.number_input("Nová váha (kg)", value=float(p_weight), step=0.5, label_visibility="collapsed")
            if st.button("Uložiť váhu", use_container_width=True):
                update_weight(current_user, new_w)
                st.toast("Váha uložená!", icon="✅")
                time.sleep(0.5)
                st.rerun()

# === TAB 2: KUCHYŇA (WIZARD) ===
with tabs[1]:
    
    # 1. DETAIL RECEPTU (AK JE VYBRATÝ)
    if st.session_state.view_recipe:
        recipe = st.session_state.view_recipe
        st.button("⬅️ Späť na zoznam", on_click=lambda: st.session_state.update(view_recipe=None))
        st.header(recipe['title'])
        st.caption(f"⏱️ {recipe.get('time', '20m')} | ⚡ {recipe.get('difficulty', 'Medium')}")
        m = recipe.get('macros', {'b':0, 's':0, 't':0})
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Kalórie", f"{recipe.get('kcal', 0)}")
        k2.metric("Bielkoviny", f"{m.get('b')}g")
        k3.metric("Sacharidy", f"{m.get('s')}g")
        k4.metric("Tuky", f"{m.get('t')}g")
        st.divider()
        c_ing, c_steps = st.columns([1, 2])
        with c_ing:
            st.subheader("🛒 Suroviny")
            df_inv = get_inventory(current_user)
            for ing in recipe['ingredients']:
                status_icon = "🔴"
                status_color = "red"
                stock_info = "(Chýba)"
                if ing.get('id'):
                    row = df_inv[df_inv['id'] == ing['id']]
                    if not row.empty:
                        stock_val = row.iloc[0]['vaha_g']
                        needed = ing['amount_g']
                        if stock_val >= needed:
                            status_icon = "🟢"
                            status_color = "green"
                            stock_info = f"(Máš {int(stock_val)}g)"
                        else:
                            status_icon = "🟠"
                            status_color = "orange"
                            stock_info = f"(Máš len {int(stock_val)}g)"
                st.markdown(f":{status_color}[{status_icon} **{ing['name']}** - {ing['amount_g']}g] {stock_info}")
        with c_steps:
            st.subheader("👨‍🍳 Postup")
            steps = recipe.get('steps', [])
            for i, step in enumerate(steps):
                st.checkbox(f"**Krok {i+1}:** {step}", key=f"step_{i}")
        st.divider()
        if st.button("🍽️ Uvarené! (Odpísať zo skladu)", type="primary", use_container_width=True):
            cook_recipe_from_stock(recipe['ingredients'], recipe['title'], recipe['kcal'], current_user)
            st.balloons()
            st.toast("Dobrú chuť!", icon="😋")
            st.session_state.view_recipe = None
            st.rerun()

    # 2. HLAVNÉ MENU (WIZARD LOGIKA)
    else:
        st.header("👨‍🍳 Šéfkuchár")
        mode = st.radio("Režim:", ["🔥 Hladný TERAZ", "📅 Plánovač (Wizard)"], horizontal=True)
        df_inv = get_inventory(current_user)
        
        # --- MODE A: TERAZ ---
        if mode == "🔥 Hladný TERAZ":
            if df_inv.empty:
                st.warning("Prázdny sklad.")
            else:
                if st.button("✨ Vymysli 3 recepty zo skladu", type="primary", use_container_width=True):
                    with st.spinner("Analyzujem chute..."):
                        inv_json = df_inv[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                        prompt = f"""
                        SKLAD: {inv_json}. NEĽÚBI: {p_dislikes}.
                        Vytvor 3 detailné recepty.
                        JSON FORMAT:
                        [
                          {{
                            "title": "Názov", "time": "20 min", "difficulty": "Easy", "kcal": 500,
                            "macros": {{"b": 30, "s": 50, "t": 10}},
                            "ingredients": [
                              {{"name": "Ryža", "amount_g": 100, "id": 1}},
                              {{"name": "Voda", "amount_g": 200, "id": null}}
                            ],
                            "steps": ["Umy ryžu", "Var 15 minút"]
                          }}
                        ]
                        """
                        try:
                            res = model.generate_content(prompt)
                            st.session_state.generated_recipes = json.loads(clean_json_response(res.text))
                        except Exception as e: st.error(f"Chyba: {e}")

                if st.session_state.generated_recipes:
                    st.write("Vyber si:")
                    cols = st.columns(3)
                    for i, r in enumerate(st.session_state.generated_recipes):
                        with cols[i]:
                            with st.container(border=True):
                                st.subheader(r['title'])
                                st.write(f"⏱️ {r.get('time')} | 🔥 {r.get('kcal')} kcal")
                                if st.button("👀 Pozrieť recept", key=f"view_{i}", use_container_width=True):
                                    st.session_state.view_recipe = r
                                    st.rerun()

        # --- MODE B: WIZARD (NOVINKA) ---
        if mode == "📅 Plánovač (Wizard)":
            
            # Fáza 1: Konfigurácia
            if st.session_state.wizard_config is None:
                with st.container(border=True):
                    st.subheader("🛠️ Nastavenie plánu")
                    days = st.slider("Počet dní", 1, 7, 3)
                    meals = st.multiselect("Jedlá", ["Raňajky", "Obed", "Večera"], default=["Obed", "Večera"])
                    
                    if st.button("🚀 Začať plánovať", type="primary", use_container_width=True):
                        if not meals:
                            st.error("Vyber aspoň jedno jedlo.")
                        elif df_inv.empty:
                            st.warning("Sklad je prázdny. Naskladni najprv.")
                        else:
                            st.session_state.wizard_config = {"days": days, "meals": meals}
                            st.session_state.wizard_step = 0
                            st.session_state.wizard_plan = []
                            st.session_state.wizard_options = None
                            st.rerun()

            # Fáza 2: Interaktívny výber (Slučka)
            elif st.session_state.wizard_config:
                conf = st.session_state.wizard_config
                total_steps = conf['days'] * len(conf['meals'])
                current_step = st.session_state.wizard_step
                
                # Ak sme prešli všetky kroky -> Fáza 3: Výsledok
                if current_step >= total_steps:
                    st.balloons()
                    st.success("✅ Plán je hotový!")
                    st.write("Tu je tvoje menu na mieru:")
                    
                    for item in st.session_state.wizard_plan:
                        with st.container(border=True):
                            c1, c2 = st.columns([4, 1])
                            c1.markdown(f"**{item['label']}**: {item['recipe']['title']}")
                            if c2.button("Otvoriť", key=f"fin_{item['step_id']}"):
                                st.session_state.view_recipe = item['recipe']
                                st.rerun()
                    
                    if st.button("🔄 Začať znova"):
                        st.session_state.wizard_config = None
                        st.session_state.wizard_plan = []
                        st.rerun()
                
                # Stále plánujeme
                else:
                    day_idx = current_step // len(conf['meals'])
                    meal_idx = current_step % len(conf['meals'])
                    day_num = day_idx + 1
                    meal_name = conf['meals'][meal_idx]
                    label = f"Deň {day_num} - {meal_name}"
                    
                    st.progress((current_step) / total_steps, text=f"Krok {current_step + 1} z {total_steps}: {label}")
                    st.subheader(f"🤔 Na čo máš chuť? ({label})")
                    
                    # Generovanie možností (ak nie sú)
                    if st.session_state.wizard_options is None:
                        with st.spinner(f"AI šéfkuchár vymýšľa 3 možnosti pre {label}..."):
                            inv_json = df_inv[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                            prompt = f"""
                            SKLAD: {inv_json}. NEĽÚBI: {p_dislikes}.
                            Vymysli 3 RÔZNE recepty pre: {label}.
                            Musia byť chuťovo odlišné (napr. Sýte vs Ľahké vs Sladké).
                            JSON FORMAT: (rovnaký ako vyššie - title, ingredients, steps...)
                            """
                            try:
                                res = model.generate_content(prompt)
                                st.session_state.wizard_options = json.loads(clean_json_response(res.text))
                            except: st.error("Chyba AI. Skús refresh.")
                    
                    # Zobrazenie 3 kariet
                    if st.session_state.wizard_options:
                        opts = st.session_state.wizard_options
                        cols = st.columns(3)
                        for i, opt in enumerate(opts):
                            with cols[i]:
                                with st.container(border=True):
                                    st.markdown(f"### {opt['title']}")
                                    st.caption(f"🔥 {opt.get('kcal')} kcal | ⏱️ {opt.get('time')}")
                                    st.write(f"_{opt.get('difficulty', 'Medium')}_")
                                    
                                    if st.button("Vybrať toto 👆", key=f"opt_{current_step}_{i}", use_container_width=True):
                                        # Uloženie výberu
                                        st.session_state.wizard_plan.append({
                                            "step_id": current_step,
                                            "label": label,
                                            "recipe": opt
                                        })
                                        # Posun na ďalší krok a vymazanie cache
                                        st.session_state.wizard_step += 1
                                        st.session_state.wizard_options = None
                                        st.rerun()

    st.divider()
    with st.expander("📜 História jedál"):
        hist = get_history_log(current_user)
        st.dataframe(hist, use_container_width=True, hide_index=True)

# === TAB 3: SKLAD ===
with tabs[2]:
    st.header("📦 Sklad")
    with st.expander("➕ Pridať manuálne"):
        with st.form("manual"):
            c1, c2, c3 = st.columns([2, 1, 1])
            m_n = c1.text_input("Názov")
            m_v = c2.number_input("Gramy", 1, 5000, 100)
            m_k = c3.selectbox("Kat", ["Mäso", "Zelenina", "Príloha", "Mliečne", "Iné"])
            if st.form_submit_button("Uložiť"):
                add_item_manual(current_user, m_n, m_v, m_k)
                st.toast("Pridané")
                st.rerun()
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        df_inv['Del'] = False
        edited = st.data_editor(df_inv[['Del', 'id', 'nazov', 'vaha_g']], column_config={"Del": st.column_config.CheckboxColumn("🗑️"), "id": None}, use_container_width=True, hide_index=True, key="inv_ed")
        to_del = edited[edited['Del']==True]
        if not to_del.empty and st.button("Zmazať označené"):
            for i, r in to_del.iterrows(): delete_item(r['id'])
            st.rerun()
    else: st.info("Prázdno")

# === TAB 4: SKENOVANIE ===
with tabs[3]:
    st.header("📸 Sken")
    up = st.file_uploader("Bloček", accept_multiple_files=True)
    if up and st.button("Analyzovať"):
        res_items = []
        bar = st.progress(0)
        for i, f in enumerate(up):
            try:
                img = process_file(f)
                r = model.generate_content(["JSON: nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g.", img])
                res_items.extend(json.loads(clean_json_response(r.text)))
            except: pass
            bar.progress((i+1)/len(up))
        st.session_state.scan_result = res_items
    if 'scan_result' in st.session_state:
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("Naskladniť"):
            add_to_inventory(ed.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.rerun()

# === TAB 5: PROFIL & AUDIT ===
with tabs[4]:
    st.header("👤 Môj Profil")
    c1, c2 = st.columns(2)
    with c1:
        st.write(f"**Meno:** {current_user}")
        st.write(f"**Archetyp:** {p_arch}")
        st.write(f"**Cieľ:** {p_goal}")
    with c2:
        if user_is_premium: st.success("💎 PREMIUM")
        else: st.info("🟢 BASIC")
        if st.button("Prepnúť plán (Test)", key="toggle_prem_prof"):
            toggle_premium(current_user, not user_is_premium)
            st.rerun()
    st.divider()
    st.subheader("🕵️‍♂️ Smart Audit")
    if user_is_premium:
        audit_data = get_audit_data(current_user)
        if len(audit_data) < 5:
            st.info("💡 Zbieram dáta (aspoň 5 jedál).")
        else:
            if st.button("🚀 Spustiť Analýzu"):
                with st.spinner("Analyzujem..."):
                    h_txt = audit_data.to_string(index=False)
                    p = f"Audit histórie: {h_txt}. JSON: {{score: int, verdict: str, stereotypes: str, risks: str, shopping_tip: str}}"
                    try:
                        r = coach_model.generate_content(p)
                        st.session_state.audit_result = json.loads(clean_json_response(r.text))
                    except: st.error("Chyba AI")
            if st.session_state.audit_result:
                res = st.session_state.audit_result
                st.progress(res['score']/10, text=f"Skóre: {res['score']}/10")
                c1, c2 = st.columns(2)
                with c1:
                    st.error(f"⚠️ {res['risks']}")
                    st.info(f"🔄 {res['stereotypes']}")
                with c2:
                    st.success(f"🛒 {res['shopping_tip']}")
                    st.write(f"📝 {res['verdict']}")
    else: st.warning("🔒 Premium funkcia.")
    st.divider()
    st.subheader("💬 Max")
    if user_is_premium:
        if "chat" not in st.session_state: st.session_state.chat = []
        for m in st.session_state.chat:
            with st.chat_message(m["role"]): st.write(m["content"])
        with st.form("chat_profil"):
            u = st.text_area("...", height=80)
            if st.form_submit_button("Odoslať"):
                st.session_state.chat.append({"role":"user", "content":u})
                with st.chat_message("user"): st.write(u)
                r = coach_model.generate_content(f"User: {u}").text
                st.session_state.chat.append({"role":"ai", "content":r})
                with st.chat_message("ai"): st.write(r)
    else: st.caption("Prepnúť na Premium pre chat.")
