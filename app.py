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

# --- 1. KONFIGURÁCIA A BEZPEČNOSŤ ---
DB_FILE = "sklad_v7_1.db" 

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    # Gemini 1.5 Flash je ideálny pre rýchlosť a prácu s obrázkami bločkov
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    st.error(f"Chyba konfigurácie API kľúča: {e}")

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# --- 2. POMOCNÉ FUNKCIE (Utility) ---
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

# --- 3. DATABÁZOVÉ OPERÁCIE ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, is_premium INTEGER DEFAULT 0, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, kategoria TEXT, vaha_g REAL, kcal_100g REAL, datum_pridania TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_log (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, nazov TEXT, prijate_kcal REAL, datum TEXT, cas TEXT)''')
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

def seed_test_data(owner):
    nakup = [
        {'nazov': 'Kuracie prsia', 'kategoria': 'Mäso', 'vaha_g': 1500, 'kcal_100g': 165},
        {'nazov': 'Hovädzie zadné', 'kategoria': 'Mäso', 'vaha_g': 1000, 'kcal_100g': 250},
        {'nazov': 'Vajcia L', 'kategoria': 'Mliečne', 'vaha_g': 1800, 'kcal_100g': 155},
        {'nazov': 'Mlieko polotučné', 'kategoria': 'Mliečne', 'vaha_g': 6000, 'kcal_100g': 46},
        {'nazov': 'Maslo 82%', 'kategoria': 'Mliečne', 'vaha_g': 500, 'kcal_100g': 717},
        {'nazov': 'Zemiaky', 'kategoria': 'Zelenina', 'vaha_g': 5000, 'kcal_100g': 77},
        {'nazov': 'Cibuľa', 'kategoria': 'Zelenina', 'vaha_g': 2000, 'kcal_100g': 40},
        {'nazov': 'Ryža Basmati', 'kategoria': 'Trvanlivé', 'vaha_g': 2000, 'kcal_100g': 365},
        {'nazov': 'Špagety', 'kategoria': 'Trvanlivé', 'vaha_g': 1500, 'kcal_100g': 350},
        {'nazov': 'Jablká', 'kategoria': 'Ovocie', 'vaha_g': 2000, 'kcal_100g': 52}
    ]
    add_to_inventory(nakup, owner)

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

# --- 4. UI APLIKÁCIE ---
st.set_page_config(page_title="Smart Food v7.2.1", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if not st.session_state.username:
    st.title("🥗 Smart Food")
    st.subheader("Tvoja inteligentná kuchyňa")
    name = st.text_input("Zadaj svoje meno pre štart:")
    if st.button("🚀 Vstúpiť") and name:
        st.session_state.username = name
        create_basic_user(name)
        st.rerun()
    st.stop()

current_user = st.session_state.username
tabs = st.tabs(["📦 Sklad", "➕ Skenovať", "👨‍🍳 Kuchyňa", "📊 Prehľad", "👤 Profil"])

# === TAB 1: SKLAD ===
with tabs[0]:
    st.header(f"📦 Sklad užívateľa {current_user}")
    df_inv = get_inventory(current_user)
    
    with st.expander("➕ Pridať položku ručne"):
        with st.form("manual_add"):
            n = st.text_input("Názov potraviny")
            v = st.number_input("Množstvo (g/ml)", 1, 10000, 100)
            k = st.selectbox("Kategória", ["Mäso", "Mliečne", "Zelenina", "Ovocie", "Trvanlivé", "Iné"])
            if st.form_submit_button("Uložiť do skladu"):
                add_item_manual(current_user, n, v, k)
                st.toast("Položka pridaná!")
                st.rerun()
    
    if not df_inv.empty:
        st.data_editor(df_inv[['id', 'nazov', 'vaha_g', 'kategoria']], use_container_width=True, hide_index=True)
    else:
        st.info("Tvoj sklad je prázdny. Skús naskenovať bloček alebo použi simuláciu v Profile.")

# === TAB 2: SKENOVANIE (S OCHRANOU API) ===
with tabs[1]:
    st.header("📸 Skenovanie bločkov")
    st.write("Nahraj fotky bločkov. Systém automaticky rozpozná potraviny.")
    up = st.file_uploader("Vyber súbory (JPG, PNG, PDF)", accept_multiple_files=True)
    
    if up and st.button("Spustiť AI analýzu"):
        res_items = []
        progress_bar = st.progress(0)
        
        for i, f in enumerate(up):
            try:
                img = process_file(f)
                prompt = "Vráť striktný JSON zoznam potravín z tohto bločku: [{'nazov':str, 'kategoria':str, 'vaha_g':int}]. Ignoruj nepotravinový tovar."
                
                # AI Volanie
                response = model.generate_content([prompt, img], safety_settings=SAFETY_SETTINGS)
                items = json.loads(clean_json_response(response.text))
                res_items.extend(items)
                
                # Rate limit protection (pauza medzi súbormi)
                time.sleep(2.0) 
                
            except Exception as e:
                if "429" in str(e):
                    st.error("⚠️ API je preťažené. Čakám 5 sekúnd...")
                    time.sleep(5)
                else: st.error(f"Chyba pri súbore {f.name}: {e}")
            
            progress_bar.progress((i + 1) / len(up))
            
        st.session_state.scan_result = res_items

    if 'scan_result' in st.session_state:
        st.subheader("📝 Skontrolovať a potvrdiť")
        ed = st.data_editor(pd.DataFrame(st.session_state.scan_result), num_rows="dynamic")
        if st.button("📥 Naskladniť potvrdené položky"):
            add_to_inventory(ed.to_dict('records'), current_user)
            del st.session_state.scan_result
            st.success("Sklad aktualizovaný!")
            st.rerun()

# === TAB 3: KUCHYŇA ===
with tabs[2]:
    st.header("👨‍🍳 Čo budeme variť?")
    inv_df = get_inventory(current_user)
    
    if inv_df.empty:
        st.warning("Najprv doplň sklad, aby som ti mohol navrhnuť recepty.")
    else:
        if st.button("✨ Vygenerovať nápady zo zásob"):
            inv_json = inv_df[['id', 'nazov', 'vaha_g']].to_json(orient='records')
            p = f"Na základe týchto zásob: {inv_json} navrhni 3 rôzne recepty. JSON formát: [{'title':str, 'kcal':int, 'ingredients':[{'name':str, 'amount_g':int, 'id':int}], 'steps':[str]}]"
            try:
                with st.spinner("AI šéfkuchár premýšľa..."):
                    res = model.generate_content(p)
                    st.session_state.recepty = json.loads(clean_json_response(res.text))
            except: st.error("Nepodarilo sa spojiť s AI kuchárom.")

    if 'recepty' in st.session_state:
        cols = st.columns(3)
        for idx, r in enumerate(st.session_state.recepty):
            with cols[idx % 3]:
                with st.container(border=True):
                    st.subheader(r['title'])
                    st.write(f"🔥 {r['kcal']} kcal")
                    with st.expander("Zobraziť postup"):
                        for s in r['steps']: st.write(f"• {s}")
                    if st.button(f"Uvariť {idx}", key=f"btn_{idx}"):
                        cook_recipe_from_stock(r['ingredients'], r['title'], r['kcal'], current_user)
                        st.balloons()
                        st.rerun()

# === TAB 4: PREHĽAD (KITCHEN INTELLIGENCE) ===
with tabs[3]:
    st.header("📊 Prehľad a štatistiky")
    log_df = get_full_log(current_user)
    inv_df = get_inventory(current_user)

    if log_df.empty:
        st.info("Tu uvidíš analýzu, keď uvaríš svoje prvé jedlo.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Počet varení", len(log_df))
        m2.metric("Položiek v sklade", len(inv_df))
        
        # Špička v kuchyni
        if 'cas' in log_df.columns and not log_df['cas'].isnull().all():
            peak = log_df['cas'].str.split(':').str[0].mode()[0]
            m3.metric("Tvoj čas varenia", f"{peak}:00")

        st.divider()
        cl, cr = st.columns(2)

        with cl:
            st.subheader("💡 AI Kuchynský Postreh")
            if st.button("Získať analýzu zvykov"):
                try:
                    h_str = log_df[['nazov', 'datum']].tail(5).to_string()
                    s_str = inv_df[['nazov', 'kategoria']].to_string()
                    p_in = f"Analyzuj históriu: {h_str} a sklad: {s_str}. Napíš jeden vtipný a jeden užitočný postreh k stravovaniu v 2 vetách."
                    res_in = model.generate_content(p_in)
                    st.session_state.last_insight = res_in.text
                except: st.error("API limit vyčerpaný, skús neskôr.")
            
            if 'last_insight' in st.session_state:
                st.info(st.session_state.last_insight)

        with cr:
            st.subheader("⌛ Čo treba minúť?")
            inv_df['datum_pridania'] = pd.to_datetime(inv_df['datum_pridania'])
            inv_df['dni'] = (datetime.now() - inv_df['datum_pridania']).dt.days
            oldest = inv_df.sort_values(by='dni', ascending=False).head(3)
            for _, row in oldest.iterrows():
                st.warning(f"**{row['nazov']}** (v sklade už {row['dni']} dní)")

        st.subheader("📈 Tvoja aktivita")
        log_df['datum'] = pd.to_datetime(log_df['datum'])
        st.line_chart(log_df.groupby('datum').size())

# === TAB 5: PROFIL A TESTOVANIE ===
with tabs[4]:
    st.header("👤 Nastavenia")
    st.write(f"Prihlásený užívateľ: **{current_user}**")
    
    st.divider()
    st.subheader("🛠 Vývojárske nástroje")
    st.write("Použi tieto tlačidlá na otestovanie funkcií bez nutnosti skenovania.")
    
    if st.button("🛒 Nasimulovať nákup za 150€", use_container_width=True, type="primary"):
        seed_test_data(current_user)
        st.success("Sklad bol naplnený testovacím nákupom!")
        time.sleep(1)
        st.rerun()

    if st.button("🗑️ Vymazať celý sklad", use_container_width=True):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM inventory WHERE owner=?", (current_user,))
        conn.commit()
        conn.close()
        st.warning("Sklad bol vyprázdnený.")
        time.sleep(1)
        st.rerun()

    st.divider()
    if st.button("🚪 Odhlásiť sa"):
        st.session_state.clear()
        st.rerun()
