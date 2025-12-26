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

# --- KONFIGURÁCIA ---
DB_FILE = "sklad_v7_1.db" # Nová verzia

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-flash-latest")
    coach_model = genai.GenerativeModel("gemini-flash-latest")
except Exception as e:
    st.error(f"Chyba konfigurácie API kľúča: {e}")

# BEZPEČNOSTNÉ NASTAVENIA (Aby AI neblokovala bločky)
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

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
        return text[start_idx:end_idx+1]
    return text

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
def create_basic_user(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''
        INSERT OR IGNORE INTO users (username, gender, age, weight, height, activity, goal, target_weight, allergies, dislikes, coach_style, archetype, health_issues, ai_strategy, is_premium, last_updated, start_weight)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        username, "Nezadané", 30, 0, 0, "Stredná", "Udržiavať", 0, "", "", 
        "Kamoš", "Začiatočník", "", "Zatiaľ žiadna stratégia.", 
        0, today, 0
    ))
    conn.commit()
    conn.close()

def update_user_profile(username, data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        UPDATE users SET 
        gender=?, age=?, weight=?, height=?, activity=?, goal=?, dislikes=?, target_weight=?, last_updated=?
        WHERE username=?
    ''', (data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'], data['dislikes'], data['target_weight'], datetime.now().strftime("%Y-%m-%d"), username))
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

def generate_progress_chart(start_weight, current_weight, target_weight, goal_type):
    fig, ax = plt.subplots(figsize=(6, 2.5))
    weights = [start_weight, current_weight, target_weight]
    labels = ["Štart", "Teraz", "Cieľ"]
    colors = ['#808080', '#FF4B4B', '#4CAF50']
    ax.bar(labels, weights, color=colors, alpha=0.8)
    if max(weights) > 0:
        min_w, max_w = min([w for w in weights if w > 0]), max(weights)
        ax.set_ylim(min_w - 5, max_w + 5)
    ax.grid(axis='y', linestyle=':', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    for i, v in enumerate(weights):
        ax.text(i, v + 0.5, f"{v} kg", ha='center', fontweight='bold', fontsize=9)
    return fig

# --- UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food v7.1", layout="wide", page_icon="🥗")
init_db()

if 'active_tab' not in st.session_state: st.session_state.active_tab = 0
if 'generated_recipes' not in st.session_state: st.session_state.generated_recipes = None
if 'view_recipe' not in st.session_state: st.session_state.view_recipe = None
if 'audit_result' not in st.session_state: st.session_state.audit_result = None
if 'wizard_step' not in st.session_state: st.session_state.wizard_step = 0
if 'wizard_plan' not in st.session_state: st.session_state.wizard_plan = []
if 'wizard_config' not in st.session_state: st.session_state.wizard_config = None
if 'wizard_options' not in st.session_state: st.session_state.wizard_options = None
if 'wizard_error' not in st.session_state: st.session_state.wizard_error = None

# === 1. LOGIN ===
if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    col1, col2 = st.columns([1, 2])
    with col1: st.image("https://cdn-icons-png.flaticon.com/512/2927/2927347.png", width=100)
    with col2: st.title("Smart Food"); st.write("Tvoja inteligentná chladnička.")
    name_input = st.text_input("Zadaj meno:", placeholder="Napr. Jakub")
    if st.button("🚀 Vstúpiť", type="primary", use_container_width=True):
        if name_input:
            st.session_state.username = name_input
            create_basic_user(name_input) 
            st.rerun()
    st.stop()

current_user = st.session_state.username
db_profile = get_user_profile(current_user)
user_is_premium = bool(db_profile[14]) if db_profile else False
p_dislikes = db_profile[9] if db_profile[9] else "" 
p_weight = db_profile[3] if db_profile[3] else 0

tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "👤 Profil"])

# === TAB 1: SKLAD ===
with tabs[0]:
    st.header(f"📦 Sklad ({current_user})")
    c1, c2 = st.columns(2)
    with c1:
        with st.expander("➕ Rýchlo pridať"):
            with st.form("manual"):
                c_n, c_v = st.columns(2)
                m_n = c_n.text_input("Čo?", placeholder="Vajíčka")
                m_v = c_v.number_input("Koľko? (g/ks)", 1, 5000, 10)
                m_k = st.selectbox("Druh", ["Mliečne", "Mäso", "Zelenina", "Iné"])
                if st.form_submit_button("Pridať"):
                    add_item_manual(current_user, m_n, m_v, m_k)
                    st.toast("Uložené")
                    st.rerun()
    df_inv = get_inventory(current_user)
    if not df_inv.empty:
        df_inv['Del'] = False
        edited = st.data_editor(df_inv[['Del', 'id', 'nazov', 'vaha_g']], column_config={"Del": st.column_config.CheckboxColumn("🗑️"), "id": None}, use_container_width=True, hide_index=True, key="inv_ed")
        if edited[edited['Del']==True].any().any() and st.button("Vyhodiť označené"):
            for i, r in edited[edited['Del']==True].iterrows(): delete_item(r['id'])
            st.rerun()
    else: st.info("Sklad je prázdny.")

# === TAB 2: SKENOVANIE (SHERLOCK MODE) ===
with tabs[1]:
    st.header("📸 Skenovanie Bločkov")
    st.info("Nahraj fotku. Ak to zlyhá, uvidíš presne prečo.")
    
    up = st.file_uploader("Nahraj fotku", accept_multiple_files=True)
    if up and st.button("Analyzovať AI"):
        res_items = []
        bar = st.progress(0)
        
        for i, f in enumerate(up):
            st.write(f"--- Analyzujem: **{f.name}** ---")
            try:
                img = process_file(f)
                
                # Prompt: Donútime ho odpovedať JSONom alebo chybou
                prompt = """
                Pozri sa na tento obrázok. Sú to bločky z obchodu?
                Vypíš všetky POTRAVINY, ktoré vidíš. Ignoruj zľavy, drogériu a medzisúčty.
                Pre každú položku odhadni kategóriu a nutričné hodnoty na 100g.
                
                DÔLEŽITÉ:
                1. Ak vidíš potraviny, vráť JSON zoznam:
                [
                    {"nazov": "Mlieko", "kategoria": "Mliečne", "vaha_g": 1000, "kcal_100g": 42, "bielkoviny_100g": 3.4, "sacharidy_100g": 5, "tuky_100g": 1.5}
                ]
                2. Ak nevidíš ŽIADNE potraviny alebo je obrázok nečitateľný, vráť prázdny zoznam: []
                
                Odpovedz len JSONom.
                """
                
                # Posielame request s vypnutými filtrami
                response = model.generate_content([prompt, img], safety_settings=SAFETY_SETTINGS)
                
                # Debug info
                raw_text = response.text
                
                # Skúsiť parsovať
                json_txt = clean_json_response(raw_text)
                items = json.loads(json_txt)
                
                if isinstance(items, list):
                    if len(items) > 0:
                        st.success(f"✅ Našiel som {len(items)} položiek.")
                        res_items.extend(items)
                    else:
                        st.warning("⚠️ AI vrátila prázdny zoznam. Nevidela žiadne potraviny.")
                        with st.expander("Vidieť, čo AI odpovedala"):
                            st.text(raw_text)
                else:
                    st.error("❌ AI nevrátila zoznam. Pozri odpoveď:")
                    st.text(raw_text)

            except Exception as e:
                st.error(f"❌ Kritická chyba pri súbore {f.name}: {e}")
                
            bar.progress((i+1)/len(up))
            
        st.session_state.scan_result = res_items

    if 'scan_result' in st.session_state and st.session_state.scan_result:
        st.divider()
        st.subheader("📝 Výsledok na kontrolu")
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("📥 Potvrdiť a Naskladniť", type="primary"):
            add_to_inventory(ed.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.toast("Naskladnené!", icon="✅")
            st.rerun()

# === TAB 3: KUCHYŇA ===
with tabs[2]:
    if st.session_state.view_recipe:
        recipe = st.session_state.view_recipe
        st.button("⬅️ Späť", on_click=lambda: st.session_state.update(view_recipe=None))
        st.header(recipe['title'])
        st.caption(f"⏱️ {recipe.get('time', '20m')} | ⚡ {recipe.get('difficulty', 'Medium')}")
        c_ing, c_steps = st.columns([1, 2])
        with c_ing:
            st.subheader("Suroviny")
            df_inv = get_inventory(current_user)
            for ing in recipe['ingredients']:
                color = "red"
                if ing.get('id'):
                    row = df_inv[df_inv['id'] == ing['id']]
                    if not row.empty and row.iloc[0]['vaha_g'] >= ing['amount_g']: color = "green"
                st.markdown(f":{color}[• {ing['name']} ({ing['amount_g']}g)]")
        with c_steps:
            st.subheader("Postup")
            for i, step in enumerate(recipe.get('steps', [])): st.checkbox(step, key=f"step_{i}")
        if st.button("🍽️ Uvariť", type="primary"):
            cook_recipe_from_stock(recipe['ingredients'], recipe['title'], recipe['kcal'], current_user)
            st.balloons(); st.session_state.view_recipe = None; st.rerun()
    else:
        st.header("👨‍🍳 Šéfkuchár")
        df_inv = get_inventory(current_user)
        mode = st.radio("Režim:", ["🔥 Hladný TERAZ", "📅 Plánovač (Wizard)"], horizontal=True)
        if mode == "🔥 Hladný TERAZ":
            if df_inv.empty: st.warning("Prázdny sklad.")
            elif st.button("✨ Čo uvariť?"):
                with st.spinner("Hľadám..."):
                    inv_json = df_inv[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                    p = f"SKLAD: {inv_json}. NEĽÚBI: {p_dislikes}. 3 recepty. JSON FORMAT: [{{ 'title': '...', 'time': '...', 'difficulty': '...', 'kcal': 0, 'ingredients': [{{ 'name': '...', 'amount_g': 0, 'id': 1 }}], 'steps': ['...'] }}]"
                    try:
                        r = model.generate_content(p)
                        st.session_state.generated_recipes = json.loads(clean_json_response(r.text))
                    except: st.error("AI error.")
            if st.session_state.generated_recipes:
                cols = st.columns(3)
                for i, r in enumerate(st.session_state.generated_recipes):
                    with cols[i]:
                        st.write(f"**{r['title']}**"); 
                        if st.button("Pozrieť", key=f"v_{i}"): st.session_state.view_recipe = r; st.rerun()
        if mode == "📅 Plánovač (Wizard)":
            # (Wizard logic simplified for space)
            if st.session_state.wizard_config is None:
                days = st.slider("Dni", 1, 5, 3); meals = st.multiselect("Jedlá", ["Obed", "Večera"], default=["Obed"])
                if st.button("Začať"): st.session_state.wizard_config = {"days": days, "meals": meals}; st.session_state.wizard_step = 0; st.session_state.wizard_plan = []; st.rerun()
            elif st.session_state.wizard_config:
                conf = st.session_state.wizard_config; total = conf['days'] * len(conf['meals']); curr = st.session_state.wizard_step
                if curr >= total:
                    st.success("Hotovo!"); 
                    for item in st.session_state.wizard_plan: 
                        if st.button(f"{item['label']}: {item['recipe']['title']}", key=f"f_{item['step_id']}"): st.session_state.view_recipe = item['recipe']; st.rerun()
                    if st.button("Zrušiť"): st.session_state.wizard_config = None; st.rerun()
                else:
                    lbl = f"Krok {curr+1}/{total}"; st.progress((curr)/total, text=lbl)
                    if not st.session_state.wizard_options:
                        with st.spinner("..."):
                            df_inv_now = get_inventory(current_user); inv_json = df_inv_now[['id','nazov', 'vaha_g']].to_json(orient='records')
                            p = f"SKLAD:{inv_json}. 3 recepty pre krok {curr}. JSON format."; 
                            try: r = model.generate_content(p); st.session_state.wizard_options = json.loads(clean_json_response(r.text))
                            except: pass
                    if st.session_state.wizard_options:
                        cols = st.columns(3)
                        for i, o in enumerate(st.session_state.wizard_options):
                            with cols[i]:
                                st.write(o['title']); 
                                if st.button("Vybrať", key=f"w_{curr}_{i}"): st.session_state.wizard_plan.append({"step_id":curr, "label":lbl, "recipe":o}); st.session_state.wizard_step += 1; st.session_state.wizard_options = None; st.rerun()

# === TAB 4: PREHĽAD ===
with tabs[3]:
    st.header("📊 Prehľad")
    if p_weight == 0:
        st.info("💡 Sledujem sklad."); 
        with st.expander("Sledovať váhu?"):
            with st.form("set_profile"):
                w = st.number_input("Váha", 40.0, 150.0); h = st.number_input("Výška", 120, 220); g = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť"])
                if st.form_submit_button("Uložiť"): update_user_profile(current_user, {"gender":"-", "age":30, "weight":w, "height":h, "activity":"Stredná", "goal":g, "dislikes":"", "target_weight":w}); st.rerun()
    else: st.metric("Váha", f"{p_weight} kg")
    st.subheader("Dnešné logy"); df_log = get_today_log(current_user); st.dataframe(df_log[['nazov', 'prijate_kcal']], hide_index=True) if not df_log.empty else st.write("Nič.")

# === TAB 5: PROFIL ===
with tabs[4]:
    st.header("👤 Profil")
    st.write(f"User: {current_user} | {'💎 Premium' if user_is_premium else '🟢 Basic'}")
    if st.button("Prepnúť verziu"): toggle_premium(current_user, not user_is_premium); st.rerun()
    st.divider(); st.subheader("🕵️‍♂️ Audit")
    audit_data = get_audit_data(current_user); count = len(audit_data)
    if count == 0: st.write("Žiadne dáta.")
    else:
        st.write(f"Mám {count} jedál."); 
        if not user_is_premium: st.warning("🔒 Premium funkcia.")
        elif st.button("🚀 Spustiť"):
            with st.spinner("..."):
                h_txt = audit_data['nazov'].to_string(index=False); p = f"Analyzuj: {h_txt}. JSON: {{score: int, verdict: str, risks: str, tip: str}}"; 
                try: r = coach_model.generate_content(p); st.json(json.loads(clean_json_response(r.text)))
                except: st.error("Chyba")
