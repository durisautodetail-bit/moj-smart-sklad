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
import collections

# --- KONFIGURÁCIA ---
DB_FILE = "sklad_v7_1.db" 

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-flash-latest")
    coach_model = genai.GenerativeModel("gemini-flash-latest")
except Exception as e:
    st.error(f"Chyba konfigurácie API kľúča: {e}")

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
    # Ak je to objekt a nie zoznam
    start_obj = text.find('{')
    end_obj = text.rfind('}')
    if start_obj != -1 and end_obj != -1:
        return text[start_obj:end_obj+1]
    return text

# --- DATABÁZA (Rozšírená o logovanie času) ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, gender TEXT, age INTEGER, weight REAL, height INTEGER,
            activity TEXT, goal TEXT, target_weight REAL, allergies TEXT, dislikes TEXT,      
            coach_style TEXT, archetype TEXT, health_issues TEXT, ai_strategy TEXT, 
            is_premium INTEGER DEFAULT 0, last_updated TEXT, start_weight REAL
        )
    ''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, kategoria TEXT, vaha_g REAL, kcal_100g REAL, bielkoviny_100g REAL, sacharidy_100g REAL, tuky_100g REAL, datum_pridania TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_log (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, zjedene_g REAL, prijate_kcal REAL, prijate_b REAL, prijate_s REAL, prijate_t REAL, datum TEXT, cas TEXT)''')
    conn.commit()
    conn.close()

# --- DB FUNKCIE ---
def create_basic_user(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''INSERT OR IGNORE INTO users (username, is_premium, last_updated) VALUES (?, ?, ?)''', (username, 0, today))
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
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (owner, nazov, kategoria, vaha, 100, 5, 10, 5, today)) 
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
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    curr_time = now.strftime("%H:%M")
    c.execute('''INSERT INTO daily_log (owner, nazov, zjedene_g, prijate_kcal, prijate_b, prijate_s, prijate_t, datum, cas) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (owner, recipe_name, 0, total_kcal, 0, 0, 0, today, curr_time))
    for ing in ingredients_used:
        if ing.get('id'):
            c.execute("SELECT vaha_g FROM inventory WHERE id=?", (ing['id'],))
            row = c.fetchone()
            if row:
                new_w = row[0] - ing['amount_g']
                if new_w <= 0: c.execute("DELETE FROM inventory WHERE id=?", (ing['id'],))
                else: c.execute("UPDATE inventory SET vaha_g=? WHERE id=?", (new_w, ing['id']))
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

def get_full_log(owner):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM daily_log WHERE owner=?", conn, params=(owner,))
    conn.close()
    return df

def process_file(uploaded_file):
    if uploaded_file.type == "application/pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        page = doc.load_page(0); pix = page.get_pixmap(); img = Image.open(io.BytesIO(pix.tobytes("png")))
    else: img = Image.open(uploaded_file)
    return optimize_image(img)

# --- UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food v7.2", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    col1, col2 = st.columns([1, 2])
    with col1: st.image("https://cdn-icons-png.flaticon.com/512/2927/2927347.png", width=100)
    with col2: st.title("Smart Food"); st.write("Tvoja inteligentná chladnička.")
    name_input = st.text_input("Zadaj meno:", placeholder="Napr. Jakub")
    if st.button("🚀 Vstúpiť", type="primary", use_container_width=True):
        if name_input: st.session_state.username = name_input; create_basic_user(name_input); st.rerun()
    st.stop()

current_user = st.session_state.username
tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "👤 Profil"])

# === TAB 1: SKLAD === (Ponechané podľa v7.1)
with tabs[0]:
    st.header(f"📦 Sklad ({current_user})")
    df_inv = get_inventory(current_user)
    with st.expander("➕ Rýchlo pridať"):
        with st.form("manual"):
            c_n, c_v, c_k = st.columns([2,1,1])
            m_n = c_n.text_input("Čo?")
            m_v = c_v.number_input("Gramy", 1, 5000, 100)
            m_k = c_k.selectbox("Druh", ["Mliečne", "Mäso", "Zelenina", "Ovocie", "Trvanlivé", "Iné"])
            if st.form_submit_button("Pridať"): add_item_manual(current_user, m_n, m_v, m_k); st.rerun()
    
    if not df_inv.empty:
        df_inv['Del'] = False
        edited = st.data_editor(df_inv[['Del', 'id', 'nazov', 'vaha_g', 'kategoria']], use_container_width=True, hide_index=True)
        if edited[edited['Del']==True].any().any():
            for i, r in edited[edited['Del']==True].iterrows(): delete_item(r['id'])
            st.rerun()
    else: st.info("Sklad je prázdny.")

# === TAB 2: SKENOVANIE === (Ponechané podľa v7.1)
with tabs[1]:
    st.header("📸 Sherlock Sken")
    up = st.file_uploader("Nahraj bločky", accept_multiple_files=True)
    if up and st.button("Analyzovať AI"):
        res_items = []
        for f in up:
            img = process_file(f)
            p = "Vráť JSON zoznam potravín z bločku: [{'nazov':str, 'kategoria':str, 'vaha_g':int, 'kcal_100g':int, 'bielkoviny_100g':float, 'sacharidy_100g':float, 'tuky_100g':float}]. Ak nič nevidíš, vráť []."
            r = model.generate_content([p, img], safety_settings=SAFETY_SETTINGS)
            try: items = json.loads(clean_json_response(r.text)); res_items.extend(items)
            except: st.error(f"Chyba pri {f.name}")
        st.session_state.scan_result = res_items

    if 'scan_result' in st.session_state:
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("📥 Naskladniť všetko"): add_to_inventory(ed.to_dict('records'), current_user); del st.session_state.scan_result; st.rerun()

# === TAB 3: KUCHYŇA === (Ponechané podľa v7.1)
with tabs[2]:
    if 'view_recipe' in st.session_state and st.session_state.view_recipe:
        r = st.session_state.view_recipe
        if st.button("⬅️ Späť"): st.session_state.view_recipe = None; st.rerun()
        st.subheader(r['title'])
        if st.button("🍽️ UVARIŤ (Odpísať zo skladu)"):
            cook_recipe_from_stock(r['ingredients'], r['title'], r['kcal'], current_user)
            st.success("Uvarené!"); st.session_state.view_recipe = None; st.rerun()
        st.write(r['steps'])
    else:
        st.header("👨‍🍳 Kuchyňa")
        if st.button("✨ Vygenerovať recepty zo skladu"):
            inv = get_inventory(current_user)[['id', 'nazov', 'vaha_g']].to_json()
            p = f"Na základe skladu {inv} navrhni 3 recepty. JSON: [{{'title':str, 'kcal':int, 'ingredients':[], 'steps':[]}}]"
            res = model.generate_content(p)
            st.session_state.recepty = json.loads(clean_json_response(res.text))
        
        if 'recepty' in st.session_state:
            for rec in st.session_state.recepty:
                if st.button(rec['title']): st.session_state.view_recipe = rec; st.rerun()

# === TAB 4: PREHĽAD (NOVÁ VERZIA v7.2) ===
with tabs[3]:
    st.header("📊 Kitchen Intelligence")
    
    log_df = get_full_log(current_user)
    inv_df = get_inventory(current_user)

    if log_df.empty:
        st.info("Zatiaľ si nič neuvaril. Údaje sa zobrazia po prvom varení.")
    else:
        # --- HORIZONTÁLNE METRIKY ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Uvarených jedál", len(log_df))
        c2.metric("Položiek v sklade", len(inv_df))
        
        # Výpočet obľúbenej kategórie
        fav_cat = inv_df['kategoria'].mode()[0] if not inv_df.empty else "N/A"
        c3.metric("Dominantná zásoba", fav_cat)
        
        # Odhad najčastejšieho času varenia
        log_df['hodina'] = log_df['cas'].str.split(':').str[0].astype(int)
        busy_hour = log_df['hodina'].mode()[0]
        c4.metric("Špička v kuchyni", f"{busy_hour}:00")

        st.divider()

        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.subheader("🥗 Tvoj Chuťový Profil")
            # Analýza slov v názvoch jedál pre určenie "DNA"
            all_titles = " ".join(log_df['nazov'].tolist()).lower()
            
            # Jednoduchá vizualizácia kategórií skladu
            if not inv_df.empty:
                cat_counts = inv_df['kategoria'].value_counts()
                fig, ax = plt.subplots(figsize=(5, 3))
                cat_counts.plot(kind='pie', autopct='%1.1f%%', ax=ax, colors=['#4CAF50', '#FFC107', '#2196F3', '#FF5722', '#9C27B0'])
                ax.set_ylabel('')
                st.pyplot(fig)

        with col_right:
            st.subheader("🤖 AI Insight")
            # Gemini analyzuje históriu a dáva tip
            history_str = log_df[['nazov', 'datum']].tail(10).to_string()
            inv_str = inv_df[['nazov', 'vaha_g']].to_string()
            
            p_insight = f"""
            Ako inteligentný kuchynský asistent analyzuj tieto dáta:
            História varenia: {history_str}
            Aktuálny sklad: {inv_str}
            
            Napíš JEDNU vtipnú a JEDNU užitočnú vetu (insight) pre užívateľa. 
            Napr. o tom, že varí stále to isté, alebo že mu v sklade niečo hnije, alebo kedy najčastejšie varí.
            Odpovedaj v slovenčine.
            """
            try:
                with st.spinner("AI premýšľa..."):
                    insight_res = model.generate_content(p_insight)
                    st.info(insight_res.text)
            except:
                st.write("AI asistent je momentálne zaneprázdnený umývaním riadu.")

        st.divider()
        
        # --- SKLADOVÁ EFEKTIVITA ---
        st.subheader("⏳ Ležiaky v sklade (Potrebné spotrebovať)")
        inv_df['datum_pridania'] = pd.to_datetime(inv_df['datum_pridania'])
        inv_df['dni_v_sklade'] = (datetime.now() - inv_df['datum_pridania']).dt.days
        leziaky = inv_df.sort_values(by='dni_v_sklade', ascending=False).head(3)
        
        if not leziaky.empty:
            cols = st.columns(len(leziaky))
            for i, (idx, row) in enumerate(leziaky.iterrows()):
                with cols[i]:
                    st.error(f"**{row['nazov']}**")
                    st.caption(f"V sklade už {row['dni_v_sklade']} dní")
        
        # --- HISTÓRIA V GRAFE ---
        st.subheader("📈 Aktivita varenia (posledné dni)")
        log_df['datum'] = pd.to_datetime(log_df['datum'])
        daily_count = log_df.groupby('datum').size()
        st.line_chart(daily_count)

# === TAB 5: PROFIL ===
with tabs[4]:
    st.header("👤 Nastavenia")
    st.write(f"Prihlásený ako: **{current_user}**")
    if st.button("Odhlásiť sa"):
        st.session_state.username = None
        st.rerun()
