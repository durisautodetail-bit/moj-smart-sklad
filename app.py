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
DB_FILE = "sklad_v7_1.db" 

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    # Používame Flash pre rýchlosť a efektivitu
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    st.error(f"Chyba konfigurácie API kľúča: {e}")

# Nastavenia pre stabilitu AI
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
    start_obj = text.find('{')
    end_obj = text.rfind('}')
    if start_obj != -1 and end_obj != -1:
        return text[start_obj:end_obj+1]
    return text

# --- DATABÁZA ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, is_premium INTEGER DEFAULT 0, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, kategoria TEXT, vaha_g REAL, kcal_100g REAL, bielkoviny_100g REAL, sacharidy_100g REAL, tuky_100g REAL, datum_pridania TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_log (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, zjedene_g REAL, prijate_kcal REAL, prijate_b REAL, prijate_s REAL, prijate_t REAL, datum TEXT, cas TEXT)''')
    conn.commit()
    conn.close()

def create_basic_user(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (username, is_premium, last_updated) VALUES (?, 0, ?)', (username, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def add_item_manual(owner, nazov, vaha, kategoria):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, datum_pridania) VALUES (?, ?, ?, ?, ?, ?)', 
              (owner, nazov, kategoria, vaha, 100, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        c.execute('INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, datum_pridania) VALUES (?, ?, ?, ?, ?, ?)', 
                  (owner, item.get('nazov'), item.get('kategoria'), item.get('vaha_g'), item.get('kcal_100g', 100), today))
    conn.commit()
    conn.close()

def cook_recipe_from_stock(ingredients_used, recipe_name, total_kcal, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now()
    c.execute('INSERT INTO daily_log (owner, nazov, prijate_kcal, datum, cas) VALUES (?, ?, ?, ?, ?)', 
              (owner, recipe_name, total_kcal, now.strftime("%Y-%m-%d"), now.strftime("%H:%M")))
    for ing in ingredients_used:
        if ing.get('id'):
            c.execute("UPDATE inventory SET vaha_g = vaha_g - ? WHERE id = ?", (ing['amount_g'], ing['id']))
            c.execute("DELETE FROM inventory WHERE vaha_g <= 0")
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
        pix = doc.load_page(0).get_pixmap()
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else: img = Image.open(uploaded_file)
    return optimize_image(img)

# --- UI START ---
st.set_page_config(page_title="Smart Food v7.2.1", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    st.title("🥗 Smart Food")
    name = st.text_input("Zadaj meno:")
    if st.button("Vstúpiť") and name:
        st.session_state.username = name
        create_basic_user(name)
        st.rerun()
    st.stop()

current_user = st.session_state.username
tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "👤 Profil"])

# === TAB 1: SKLAD ===
with tabs[0]:
    st.header("📦 Aktuálny Sklad")
    df_inv = get_inventory(current_user)
    with st.expander("➕ Pridať manuálne"):
        with st.form("manual_add"):
            n = st.text_input("Názov")
            v = st.number_input("Gramy", 1, 5000, 100)
            k = st.selectbox("Kategória", ["Mäso", "Mliečne", "Zelenina", "Ovocie", "Trvanlivé", "Iné"])
            if st.form_submit_button("Uložiť"):
                add_item_manual(current_user, n, v, k)
                st.rerun()
    
    if not df_inv.empty:
        st.data_editor(df_inv[['id', 'nazov', 'vaha_g', 'kategoria']], use_container_width=True, hide_index=True)
    else: st.info("Sklad je prázdny. Naskenuj bloček!")

# === TAB 2: SKENOVANIE (FIXED RATE LIMIT) ===
with tabs[1]:
    st.header("📸 Sherlock Sken")
    st.write("Skener s ochranou proti preťaženiu API.")
    up = st.file_uploader("Nahraj bločky", accept_multiple_files=True)
    
    if up and st.button("Analyzovať cez AI"):
        res_items = []
        progress_bar = st.progress(0)
        
        for i, f in enumerate(up):
            try:
                img = process_file(f)
                prompt = "Vráť JSON zoznam potravín: [{'nazov':str, 'kategoria':str, 'vaha_g':int}]. Iba JSON."
                
                # Volanie AI
                response = model.generate_content([prompt, img], safety_settings=SAFETY_SETTINGS)
                items = json.loads(clean_json_response(response.text))
                res_items.extend(items)
                
                # OCHRANA: Krátka pauza medzi súbormi
                time.sleep(1.5) 
                
            except Exception as e:
                if "429" in str(e):
                    st.error(f"⚠️ Limit API vyčerpaný. Čakám na uvoľnenie...")
                    time.sleep(5)
                else: st.error(f"Chyba pri {f.name}: {e}")
            
            progress_bar.progress((i + 1) / len(up))
            
        st.session_state.scan_result = res_items

    if 'scan_result' in st.session_state:
        st.subheader("Kontrola údajov")
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("📥 Potvrdiť a Naskladniť"):
            add_to_inventory(ed.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.rerun()

# === TAB 3: KUCHYŇA ===
with tabs[2]:
    st.header("👨‍🍳 Inteligentný Šéfkuchár")
    inv_df = get_inventory(current_user)
    
    if inv_df.empty:
        st.warning("Sklad je prázdny, AI nemá z čoho variť.")
    else:
        if st.button("✨ Čo môžem uvariť?"):
            inv_json = inv_df[['id', 'nazov', 'vaha_g']].to_json(orient='records')
            p = f"Na základe skladu {inv_json} navrhni 3 recepty. JSON: [{{'title':str, 'kcal':int, 'ingredients':[{'name':str, 'amount_g':int, 'id':int}], 'steps':[str]}}]"
            try:
                res = model.generate_content(p)
                st.session_state.recepty = json.loads(clean_json_response(res.text))
            except: st.error("API Error pri generovaní receptov.")

    if 'recepty' in st.session_state:
        for r in st.session_state.recepty:
            with st.expander(f"📖 {r['title']} ({r['kcal']} kcal)"):
                st.write("**Postup:**")
                for step in r['steps']: st.write(f"- {step}")
                if st.button(f"Uvariť {r['title']}", key=r['title']):
                    cook_recipe_from_stock(r['ingredients'], r['title'], r['kcal'], current_user)
                    st.success("Suroviny odpočítané!"); time.sleep(1); st.rerun()

# === TAB 4: PREHĽAD (FIXED INSIGHT) ===
with tabs[3]:
    st.header("📊 Kitchen Intelligence")
    log_df = get_full_log(current_user)
    inv_df = get_inventory(current_user)

    if log_df.empty:
        st.info("Var častejšie, aby sme mali čo analyzovať!")
    else:
        # Horné metriky
        m1, m2, m3 = st.columns(3)
        m1.metric("Počet jedál", len(log_df))
        m2.metric("Položky v sklade", len(inv_df))
        if 'cas' in log_df.columns:
            peak = log_df['cas'].str.split(':').str[0].mode()[0]
            m3.metric("Najčastejší čas", f"{peak}:00")

        st.divider()
        c_left, c_right = st.columns(2)

        with c_left:
            st.subheader("💡 AI Kuchynský Insight")
            # AI voláme iba po kliknutí, aby sme neplytvali limitom pri každom prepnutí tabu
            if st.button("Generovať analýzu stravovania"):
                try:
                    with st.spinner("Analyzujem tvoje zvyky..."):
                        h_str = log_df[['nazov', 'datum']].tail(5).to_string()
                        s_str = inv_df[['nazov', 'kategoria']].to_string()
                        p_in = f"Analyzuj históriu: {h_str} a sklad: {s_str}. Napíš vtipný a užitočný postreh v 2 vetách."
                        res_in = model.generate_content(p_in)
                        st.session_state.last_insight = res_in.text
                except: st.error("API je momentálne vyťažené.")
            
            if 'last_insight' in st.session_state:
                st.info(st.session_state.last_insight)

        with c_right:
            st.subheader("⌛ Ležiaky v sklade")
            inv_df['datum_pridania'] = pd.to_datetime(inv_df['datum_pridania'])
            inv_df['dni'] = (datetime.now() - inv_df['datum_pridania']).dt.days
            oldest = inv_df.sort_values(by='dni', ascending=False).head(3)
            for _, row in oldest.iterrows():
                st.warning(f"**{row['nazov']}** (v sklade {row['dni']} dní)")

        st.subheader("📈 Aktivita varenia")
        log_df['datum'] = pd.to_datetime(log_df['datum'])
        st.line_chart(log_df.groupby('datum').size())

# === TAB 5: PROFIL ===
with tabs[4]:
    st.header("👤 Nastavenia")
    st.write(f"Užívateľ: **{current_user}**")
    if st.button("Vymazať session a odhlásiť"):
        st.session_state.clear()
        st.rerun()
