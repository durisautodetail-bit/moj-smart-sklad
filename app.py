import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import fitz  # PyMuPDF
import io
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- KONFIGURÁCIA ---
DB_FILE = "sklad_v6_2.db"

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
    
    # Dáta pre graf (Zjednodušená projekcia)
    weights = [start_weight, current_weight, target_weight]
    labels = ["Štart", "Teraz", "Cieľ"]
    colors = ['#gray', '#FF4B4B', '#4CAF50']
    
    ax.bar(labels, weights, color=colors, alpha=0.8)
    ax.set_ylim(min(weights)-5, max(weights)+5)
    ax.grid(axis='y', linestyle=':', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    
    # Pridanie hodnôt nad stĺpce
    for i, v in enumerate(weights):
        ax.text(i, v + 0.5, f"{v} kg", ha='center', fontweight='bold')
        
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
    # Ak je to nový user, start_weight = weight
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
    # Defaultné makrá pre manuálne pridanie (AI by ich mohlo doplniť neskôr)
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
        item_id = ing['id']
        used_g = ing['used_g']
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

def get_history_log(owner, limit=10):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT datum, nazov, prijate_kcal FROM daily_log WHERE owner=? ORDER BY id DESC LIMIT ?", conn, params=(owner, limit))
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
st.set_page_config(page_title="Smart Food v6.2", layout="wide", page_icon="🥗")
init_db()

# Session State
if 'active_tab' not in st.session_state: st.session_state.active_tab = 0
if 'show_bridge' not in st.session_state: st.session_state.show_bridge = False
if 'generated_recipes' not in st.session_state: st.session_state.generated_recipes = None
if 'weekly_plan' not in st.session_state: st.session_state.weekly_plan = None

# === 1. LOGIN ===
if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    st.title("🥗 Smart Food")
    st.caption("Tvoj osobný sklad a tréner")
    name_input = st.text_input("Meno:", placeholder="Napr. Jakub")
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
    st.title("🎉 Profil hotový!")
    st.success("Tvoja cesta začína dnes.")
    
    b1, b2 = st.columns(2)
    with b1:
        if st.button("Prejsť do aplikácie ➡️", type="primary", use_container_width=True):
            st.session_state.active_tab = 0
            st.session_state.show_bridge = False
            st.rerun()
    st.stop()

db_profile = get_user_profile(current_user)
user_is_premium = bool(db_profile[14]) if db_profile and len(db_profile) > 14 else False

# === 2. ONBOARDING ===
if not db_profile:
    st.title(f"👋 Ahoj {current_user}!")
    st.info("Rýchle nastavenie profilu predtým, než začneme.")
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

# --- MAIN APP LOGIC ---

# Načítanie dát
p_weight = db_profile[3]
p_start_weight = db_profile[16] if len(db_profile) > 16 and db_profile[16] else p_weight
p_target_w = db_profile[7]
p_goal = db_profile[6]
p_dislikes = db_profile[9]
p_arch = db_profile[11]

# Kalkulácia kalórií
factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
tdee = (10 * p_weight + 6.25 * db_profile[4] - 5 * db_profile[2] + (5 if db_profile[1] == "Muž" else -161)) * factor.get(db_profile[5], 1.375)
target_kcal = tdee - 500 if p_goal == "Chudnúť" else (tdee + 300 if p_goal == "Pribrať" else tdee)

# MENU TABY
tabs = st.tabs(["📊 Prehľad", "👨‍🍳 Kuchyňa", "📦 Sklad", "➕ Skenovať", "🤖 AI Tréner"])

# === TAB 1: PREHĽAD (DASHBOARD) ===
with tabs[0]:
    st.subheader("👋 Vitaj späť!")
    
    # KARTY (Metrics)
    c1, c2, c3 = st.columns(3)
    df_log = get_today_log(current_user)
    curr_kcal = df_log['prijate_kcal'].sum() if not df_log.empty else 0
    
    c1.metric("Aktuálna váha", f"{p_weight} kg", delta=f"{p_weight - p_start_weight:.1f} kg", delta_color="inverse")
    c2.metric("Cieľ", f"{p_target_w} kg")
    c3.metric("Dnes prijaté", f"{int(curr_kcal)} kcal", f"z {int(target_kcal)}")
    
    st.markdown("---")
    
    # GRAF PROGRESU
    col_graph, col_input = st.columns([2, 1])
    with col_graph:
        st.caption("📉 Tvoj progres")
        fig = generate_progress_chart(p_start_weight, p_weight, p_target_w, p_goal)
        st.pyplot(fig)
        
    with col_input:
        with st.container(border=True):
            st.caption("⚖️ Aktualizácia váhy")
            new_w = st.number_input("Nová váha (kg)", value=float(p_weight), step=0.5, label_visibility="collapsed")
            if st.button("Uložiť váhu", use_container_width=True):
                update_weight(current_user, new_w)
                st.toast("Váha uložená!", icon="✅")
                time.sleep(0.5)
                st.rerun()

    # MOTIVÁCIA (Basic vs Premium)
    if user_is_premium:
        st.info(f"💡 **Tip dňa:** Tvoj archetyp je {p_arch}. Nezabudni na bielkoviny!")
    else:
        st.caption("🚀 Odomkni AI Coacha pre denné tipy a motiváciu.")

# === TAB 2: KUCHYŇA (KITCHEN) ===
with tabs[1]:
    st.header("👨‍🍳 Tvoja Kuchyňa")
    
    # Výber režimu
    mode = st.radio("Čo ideme robiť?", ["🔥 Hladný TERAZ", "📅 Plánujem TÝŽDEŇ"], horizontal=True)
    
    df_inv = get_inventory(current_user)
    
    # --- MODE A: HLADNÝ TERAZ ---
    if mode == "🔥 Hladný TERAZ":
        if df_inv.empty:
            st.warning("⚠️ Prázdny sklad. Najprv pridaj potraviny v záložke 'Sklad' alebo 'Skenovať'.")
        else:
            if st.button("✨ Vymysli 3 rýchlovky zo skladu", type="primary", use_container_width=True):
                with st.spinner("Miešam ingrediencie..."):
                    inv_json = df_inv[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                    prompt = f"""
                    SKLAD: {inv_json}. NEĽÚBI: {p_dislikes}.
                    Navrhni 3 jednoduché recepty TERAZ.
                    JSON FORMAT: [{{ "name": "...", "desc": "...", "kcal": 0, "ingredients_used": [{{"id": 1, "used_g": 100}}] }}]
                    """
                    try:
                        res = model.generate_content(prompt)
                        st.session_state.generated_recipes = json.loads(clean_json_response(res.text))
                    except: st.error("AI momentálne oddychuje. Skús znova.")
            
            # Zobrazenie výsledkov
            if st.session_state.generated_recipes:
                cols = st.columns(3)
                for i, r in enumerate(st.session_state.generated_recipes):
                    with cols[i]:
                        with st.container(border=True):
                            st.subheader(r['name'])
                            st.caption(r['desc'])
                            st.write(f"🔥 {r['kcal']} kcal")
                            if st.button("Uvariť & Zjesť", key=f"now_{i}", type="secondary", use_container_width=True):
                                cook_recipe_from_stock(r['ingredients_used'], r['name'], r['kcal'], current_user)
                                st.balloons()
                                st.session_state.generated_recipes = None
                                st.rerun()

    # --- MODE B: PLÁNOVAČ TÝŽDEŇ ---
    if mode == "📅 Plánujem TÝŽDEŇ":
        with st.container(border=True):
            st.subheader("🛠️ Nastavenie plánu")
            days = st.slider("Na koľko dní?", 1, 7, 3)
            meals = st.multiselect("Ktoré jedlá chceš?", ["Raňajky", "Obed", "Večera", "Snack"], default=["Obed", "Večera"])
            
            if st.button("📝 Vygenerovať Menu", type="primary", use_container_width=True):
                if df_inv.empty:
                    st.warning("Prázdny sklad.")
                else:
                    with st.spinner(f"Plánujem {days} dní..."):
                        inv_str = df_inv['nazov'].to_string()
                        prompt = f"""
                        Vytvor jedálniček na {days} dní. Zahrň: {', '.join(meals)}.
                        Sklad: {inv_str}. Neľúbi: {p_dislikes}.
                        Výstup len textový formát (Markdown tabuľka).
                        """
                        try:
                            res = model.generate_content(prompt)
                            st.session_state.weekly_plan = res.text
                        except: st.error("Chyba AI.")
        
        if st.session_state.weekly_plan:
            st.markdown("### 🗓️ Tvoj Plán")
            st.markdown(st.session_state.weekly_plan)
            st.info("💡 Tip: Pre uvarenie konkrétneho jedla sa prepni na 'Hladný TERAZ' alebo si ho manuálne zapíš.")

    st.divider()
    # HISTÓRIA JEDÁL
    with st.expander("📜 História zjedených jedál", expanded=False):
        hist = get_history_log(current_user)
        if not hist.empty:
            st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.write("Zatiaľ žiadna história.")

# === TAB 3: SKLAD (INVENTORY) ===
with tabs[2]:
    st.header("📦 Sklad potravín")
    
    # SEKCIA 1: Rýchle manuálne pridanie
    with st.expander("➕ Pridať manuálne (bez skenovania)", expanded=False):
        with st.form("manual_add"):
            c1, c2, c3 = st.columns([2, 1, 1])
            m_nazov = c1.text_input("Názov", placeholder="napr. Ryža")
            m_vaha = c2.number_input("Gramy/Kusy", 1, 5000, 100)
            m_kat = c3.selectbox("Kategória", ["Mliečne", "Mäso", "Pečivo", "Zelenina", "Iné"])
            if st.form_submit_button("Pridať do skladu"):
                add_item_manual(current_user, m_nazov, m_vaha, m_kat)
                st.toast(f"{m_nazov} pridané!", icon="📦")
                st.rerun()

    # SEKCIA 2: Tabuľka a Mazanie
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        # Prehľadnejšie zobrazenie
        st.write(f"Máš **{len(df_inv)}** položiek v sklade.")
        
        # Data Editor s možnosťou mazania
        df_inv['Vyhodiť'] = False
        edited = st.data_editor(
            df_inv[['Vyhodiť', 'id', 'nazov', 'vaha_g', 'kategoria']], 
            column_config={
                "Vyhodiť": st.column_config.CheckboxColumn("🗑️", help="Označ na vyhodenie", default=False),
                "id": None # Skryť ID
            },
            use_container_width=True,
            hide_index=True,
            key="inv_editor"
        )
        
        # Tlačidlo na vykonanie zmien
        to_delete = edited[edited['Vyhodiť'] == True]
        if not to_delete.empty:
            if st.button(f"🗑️ Vyhodiť označené ({len(to_delete)})", type="secondary", use_container_width=True):
                for i, r in to_delete.iterrows():
                    delete_item(r['id'])
                st.toast("Položky odstránené.", icon="🗑️")
                st.rerun()
    else:
        st.info("Sklad je prázdny. Naskenuj bloček alebo pridaj manuálne.")

# === TAB 4: SKENOVANIE ===
with tabs[3]:
    st.header("📸 Skenovať bloček")
    st.caption("Nahraj fotku bločku a AI automaticky vytiahne potraviny.")
    
    uples = st.file_uploader("Nahrať súbor", type=["jpg", "png", "pdf"], accept_multiple_files=True)
    if uples and st.button("Analyzovať bloček", type="primary"):
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
        st.write("Skontroluj výsledok:")
        edited = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("📥 Naskladniť všetko", type="primary", use_container_width=True):
            add_to_inventory(edited.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.toast("Naskladnené!", icon="✅")
            st.rerun()

# === TAB 5: AI TRÉNER (NA KONCI) ===
with tabs[4]:
    st.header("🤖 AI Tréner Max")
    if user_is_premium:
        if "day_chat_history" not in st.session_state: st.session_state.day_chat_history = []
        for msg in st.session_state.day_chat_history:
            with st.chat_message(msg["role"]): st.write(msg["content"])
        
        with st.form("chat_ai", clear_on_submit=True):
            user_msg = st.text_area("Napíš správu...", height=80)
            if st.form_submit_button("Odoslať"):
                st.session_state.day_chat_history.append({"role": "user", "content": user_msg})
                with st.chat_message("user"): st.write(user_msg)
                with st.spinner("Max premýšľa..."):
                    try:
                        res = coach_model.generate_content(f"User: {user_msg}")
                        st.session_state.day_chat_history.append({"role": "ai", "content": res.text})
                        with st.chat_message("ai"): st.write(res.text)
                    except: st.error("Chyba spojenia.")
    else:
        st.warning("🔒 AI Tréner je dostupný v Premium verzii.")
        st.info("V Basic verzii ti AI pomáha len s receptami v kuchyni.")
