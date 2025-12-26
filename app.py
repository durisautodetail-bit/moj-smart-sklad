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
DB_FILE = "sklad_v7_4.db"

try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    st.error(f"Chyba konfigurácie API kľúča: {e}")

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# --- 2. POMOCNÉ FUNKCIE ---
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
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        owner TEXT, 
        nazov TEXT, 
        kategoria TEXT, 
        vaha_g REAL, 
        kcal_100g REAL, 
        bielkoviny_100g REAL DEFAULT 0,
        sacharidy_100g REAL DEFAULT 0,
        tuky_100g REAL DEFAULT 0,
        datum_pridania TEXT
    )''')
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
    c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
              (owner, nazov, kategoria, vaha, 100, 10, 10, 5, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def add_to_inventory(items, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in items:
        b = item.get('bielkoviny_100g', 0)
        s = item.get('sacharidy_100g', 0)
        t = item.get('tuky_100g', 0)
        c.execute('''INSERT INTO inventory (owner, nazov, kategoria, vaha_g, kcal_100g, bielkoviny_100g, sacharidy_100g, tuky_100g, datum_pridania) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                  (owner, item.get('nazov'), item.get('kategoria'), item.get('vaha_g'), item.get('kcal_100g', 100), b, s, t, today))
    conn.commit()
    conn.close()

def update_item_details(item_id, nazov, kategoria, vaha, kcal, b, s, t, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''UPDATE inventory 
                 SET nazov=?, kategoria=?, vaha_g=?, kcal_100g=?, bielkoviny_100g=?, sacharidy_100g=?, tuky_100g=? 
                 WHERE id=? AND owner=?''', 
              (nazov, kategoria, vaha, kcal, b, s, t, item_id, owner))
    conn.commit()
    conn.close()

def update_inventory_weight(updates, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for u in updates:
        c.execute("UPDATE inventory SET vaha_g=? WHERE id=? AND owner=?", (u['vaha_g'], u['id'], owner))
    conn.commit()
    conn.close()

def delete_item(item_id, owner):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE id=? AND owner=?", (item_id, owner))
    conn.commit()
    conn.close()

def seed_test_data(owner):
    nakup = [
        {'nazov': 'Kuracie prsia', 'kategoria': 'Mäso', 'vaha_g': 1500, 'kcal_100g': 165, 'bielkoviny_100g': 31, 'sacharidy_100g': 0, 'tuky_100g': 3.6},
        {'nazov': 'Hovädzie zadné', 'kategoria': 'Mäso', 'vaha_g': 1000, 'kcal_100g': 250, 'bielkoviny_100g': 26, 'sacharidy_100g': 0, 'tuky_100g': 15},
        {'nazov': 'Vajcia L (30ks)', 'kategoria': 'Mliečne', 'vaha_g': 1800, 'kcal_100g': 155, 'bielkoviny_100g': 13, 'sacharidy_100g': 1.1, 'tuky_100g': 11},
        {'nazov': 'Mlieko polotučné', 'kategoria': 'Mliečne', 'vaha_g': 6000, 'kcal_100g': 46, 'bielkoviny_100g': 3.3, 'sacharidy_100g': 4.8, 'tuky_100g': 1.5},
        {'nazov': 'Maslo 82%', 'kategoria': 'Mliečne', 'vaha_g': 500, 'kcal_100g': 717, 'bielkoviny_100g': 0.8, 'sacharidy_100g': 0.6, 'tuky_100g': 81},
        {'nazov': 'Syr Eidam', 'kategoria': 'Mliečne', 'vaha_g': 1000, 'kcal_100g': 350, 'bielkoviny_100g': 25, 'sacharidy_100g': 2, 'tuky_100g': 26},
        {'nazov': 'Zemiaky', 'kategoria': 'Zelenina', 'vaha_g': 5000, 'kcal_100g': 77, 'bielkoviny_100g': 2, 'sacharidy_100g': 17, 'tuky_100g': 0.1},
        {'nazov': 'Cibuľa', 'kategoria': 'Zelenina', 'vaha_g': 2000, 'kcal_100g': 40, 'bielkoviny_100g': 1.1, 'sacharidy_100g': 9, 'tuky_100g': 0.1},
        {'nazov': 'Ryža Basmati', 'kategoria': 'Trvanlivé', 'vaha_g': 2000, 'kcal_100g': 365, 'bielkoviny_100g': 7, 'sacharidy_100g': 77, 'tuky_100g': 0.6},
        {'nazov': 'Špagety', 'kategoria': 'Trvanlivé', 'vaha_g': 1500, 'kcal_100g': 350, 'bielkoviny_100g': 12, 'sacharidy_100g': 75, 'tuky_100g': 1.5},
        {'nazov': 'Jablká', 'kategoria': 'Ovocie', 'vaha_g': 2000, 'kcal_100g': 52, 'bielkoviny_100g': 0.3, 'sacharidy_100g': 14, 'tuky_100g': 0.2},
        {'nazov': 'Olivový olej', 'kategoria': 'Trvanlivé', 'vaha_g': 1000, 'kcal_100g': 884, 'bielkoviny_100g': 0, 'sacharidy_100g': 0, 'tuky_100g': 100}
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
st.set_page_config(page_title="Smart Food v7.4", layout="wide", page_icon="🥗")
init_db()

if 'username' not in st.session_state: st.session_state.username = None
if 'active_plan' not in st.session_state: st.session_state.active_plan = [] 

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

# === TAB 1: SKLAD (NOVÝ MANAŽMENT) ===
with tabs[0]:
    st.header(f"📦 Manažér Skladu")
    df_inv = get_inventory(current_user)

    if df_inv.empty:
        st.info("Sklad je prázdny. Začni pridaním surovín alebo naskenovaním bločku.")
        with st.expander("➕ Pridať prvú položku", expanded=True):
             with st.form("manual_add_empty"):
                n = st.text_input("Názov")
                v = st.number_input("Množstvo (g)", 1, 10000, 100)
                k = st.selectbox("Kategória", ["Mäso", "Mliečne", "Zelenina", "Ovocie", "Trvanlivé", "Iné"])
                if st.form_submit_button("Uložiť"):
                    add_item_manual(current_user, n, v, k)
                    st.rerun()
    else:
        c_search, c_filter = st.columns([2, 2])
        with c_search:
            search_query = st.text_input("🔍 Hľadať surovinu", placeholder="Napr. mlieko, vajcia...")
        with c_filter:
            all_cats = df_inv['kategoria'].unique().tolist()
            selected_cats = st.multiselect("Filtrovať kategórie", all_cats, default=all_cats)
        
        df_filtered = df_inv[df_inv['kategoria'].isin(selected_cats)]
        if search_query:
            df_filtered = df_filtered[df_filtered['nazov'].str.contains(search_query, case=False)]

        st.caption("Tip: Klikni na začiatok riadku pre detail suroviny. Váhu môžeš prepísať priamo tu.")
        
        column_config = {
            "id": None, 
            "owner": None,
            "kcal_100g": None,
            "bielkoviny_100g": None,
            "sacharidy_100g": None,
            "tuky_100g": None,
            "datum_pridania": None,
            "nazov": st.column_config.TextColumn("Názov", disabled=True),
            "kategoria": st.column_config.TextColumn("Kategória", width="small", disabled=True),
            "vaha_g": st.column_config.NumberColumn("Množstvo (g)", min_value=0, max_value=10000, step=10, format="%d g")
        }

        edited_df = st.data_editor(
            df_filtered,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
            key="inventory_editor",
            on_change=None,
            selection_mode="single-row"
        )

        changes = []
        for index, row in edited_df.iterrows():
            orig_row = df_inv[df_inv['id'] == row['id']]
            if not orig_row.empty:
                orig_w = orig_row.iloc[0]['vaha_g']
                if row['vaha_g'] != orig_w:
                    changes.append({'id': row['id'], 'vaha_g': row['vaha_g']})
        
        if changes:
            update_inventory_weight(changes, current_user)
            st.toast("Váha aktualizovaná!")
            time.sleep(0.5)
            st.rerun()

        selection = st.session_state.inventory_editor.get("selection", {"rows": []})
        
        if selection["rows"]:
            idx = selection["rows"][0]
            selected_row = df_filtered.iloc[idx]
            
            st.divider()
            st.subheader(f"📝 Detail: {selected_row['nazov']}")
            
            with st.form("edit_item_form"):
                c1, c2, c3 = st.columns(3)
                new_nazov = c1.text_input("Názov", selected_row['nazov'])
                new_vaha = c2.number_input("Váha (g)", 0, 10000, int(selected_row['vaha_g']))
                kat_opts = ["Mäso", "Mliečne", "Zelenina", "Ovocie", "Trvanlivé", "Iné"]
                curr_kat = selected_row['kategoria'] if selected_row['kategoria'] in kat_opts else "Iné"
                new_kat = c3.selectbox("Kategória", kat_opts, index=kat_opts.index(curr_kat))
                
                st.write("📊 **Nutričné hodnoty na 100g**")
                m1, m2, m3, m4 = st.columns(4)
                new_kcal = m1.number_input("Kcal", 0, 1000, int(selected_row['kcal_100g']))
                new_b = m2.number_input("Bielkoviny", 0.0, 100.0, float(selected_row.get('bielkoviny_100g', 0)))
                new_s = m3.number_input("Sacharidy", 0.0, 100.0, float(selected_row.get('sacharidy_100g', 0)))
                new_t = m4.number_input("Tuky", 0.0, 100.0, float(selected_row.get('tuky_100g', 0)))
                
                col_save, col_del = st.columns([1, 1])
                with col_save:
                    if st.form_submit_button("💾 Uložiť zmeny", type="primary", use_container_width=True):
                        update_item_details(selected_row['id'], new_nazov, new_kat, new_vaha, new_kcal, new_b, new_s, new_t, current_user)
                        st.success("Uložené!")
                        time.sleep(1)
                        st.rerun()
                with col_del:
                    if st.form_submit_button("🗑️ Odstrániť surovinu", type="secondary", use_container_width=True):
                        delete_item(selected_row['id'], current_user)
                        st.warning("Položka odstránená.")
                        time.sleep(1)
                        st.rerun()

    st.divider()
    with st.expander("➕ Pridať novú položku manuálne"):
        with st.form("manual_add_bottom"):
            c_n, c_v, c_k = st.columns([2,1,1])
            m_n = c_n.text_input("Čo pridávame?")
            m_v = c_v.number_input("Gramy", 1, 5000, 100)
            m_k = c_k.selectbox("Druh", ["Mäso", "Mliečne", "Zelenina", "Ovocie", "Trvanlivé", "Iné"])
            if st.form_submit_button("Pridať do skladu"):
                add_item_manual(current_user, m_n, m_v, m_k)
                st.rerun()

# === TAB 2: SKENOVANIE ===
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
                prompt = """
                Vráť striktný JSON zoznam potravín z bločku. 
                Formát: [{'nazov':str, 'kategoria':str, 'vaha_g':int, 'kcal_100g':int, 'bielkoviny_100g':float, 'sacharidy_100g':float, 'tuky_100g':float}].
                Odhadni nutričné hodnoty ak nie sú viditeľné.
                """
                response = model.generate_content([prompt, img], safety_settings=SAFETY_SETTINGS)
                items = json.loads(clean_json_response(response.text))
                res_items.extend(items)
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
    st.header("👨‍🍳 Inteligentná Kuchyňa")
    inv_df = get_inventory(current_user)
    
    if inv_df.empty:
        st.warning("Najprv doplň sklad, aby som ti mohol navrhnúť recepty.")
    else:
        mode = st.radio("Čo chceš robiť?", ["🔥 Hladný TERAZ", "📅 Plánovač (3 Dni)"], horizontal=True)
        st.divider()

        if mode == "🔥 Hladný TERAZ":
            st.caption("Rýchly návrh jedla z toho, čo máš v sklade.")
            if st.button("✨ Vygenerovať 3 nápady"):
                inv_json = inv_df[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                p = f"Na základe týchto zásob: {inv_json} navrhni 3 rôzne recepty na TERAZ. JSON formát: [{'title':str, 'kcal':int, 'ingredients':[{'name':str, 'amount_g':int, 'id':int}], 'steps':[str]}]"
                try:
                    with st.spinner("Šéfkuchár vymýšľa recepty..."):
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
                            with st.expander("Postup"):
                                for s in r['steps']: st.write(f"• {s}")
                            if st.button(f"Uvariť", key=f"now_{idx}"):
                                cook_recipe_from_stock(r['ingredients'], r['title'], r['kcal'], current_user)
                                st.balloons()
                                st.rerun()

        elif mode == "📅 Plánovač (3 Dni)":
            st.caption("AI ti vytvorí rozpis jedál na 3 dni dopredu.")
            if st.button("🗓️ Vytvoriť plán na 3 dni"):
                inv_json = inv_df[['id', 'nazov', 'vaha_g']].to_json(orient='records')
                p = f"""
                Si plánovač jedál. Mám tento sklad: {inv_json}.
                Vytvor plán na 3 dni (Obed 1, Obed 2, Obed 3).
                Musí to byť striktný JSON: 
                [
                    {{'day': 'Deň 1', 'title': '...', 'kcal': 0, 'ingredients': [{{'name':'...', 'amount_g':0, 'id':0}}], 'steps': ['...']}},
                    {{'day': 'Deň 2', 'title': '...', 'kcal': 0, 'ingredients': [{{'name':'...', 'amount_g':0, 'id':0}}], 'steps': ['...']}},
                    {{'day': 'Deň 3', 'title': '...', 'kcal': 0, 'ingredients': [{{'name':'...', 'amount_g':0, 'id':0}}], 'steps': ['...']}}
                ]
                """
                try:
                    with st.spinner("Tvorím plán..."):
                        res = model.generate_content(p)
                        st.session_state.active_plan = json.loads(clean_json_response(res.text))
                except: st.error("Chyba pri generovaní plánu.")

            if st.session_state.active_plan:
                st.subheader("Tvoj plán varenia")
                for i, item in enumerate(st.session_state.active_plan):
                    with st.expander(f"📅 {item['day']}: {item['title']} ({item['kcal']} kcal)"):
                        st.write("**Suroviny:**")
                        for ing in item['ingredients']:
                            st.write(f"- {ing['name']} ({ing['amount_g']}g)")
                        st.write("**Postup:**")
                        for s in item['steps']: st.write(f"- {s}")
                        
                        if st.button(f"🍽️ Uvariť {item['day']}", key=f"plan_{i}"):
                            cook_recipe_from_stock(item['ingredients'], item['title'], item['kcal'], current_user)
                            st.success(f"Uvarené!")
                            time.sleep(1)
                            st.rerun()

# === TAB 4: PREHĽAD ===
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
        
        if 'cas' in log_df.columns and not log_df['cas'].isnull().all():
            peak = log_df['cas'].str.split(':').str[0].mode()[0]
            m3.metric("Tvoj čas varenia", f"{peak}:00")

        st.divider()
        cl, cr = st.columns(2)
        with cl:
            st.subheader("💡 AI Postreh")
            if st.button("Získať analýzu zvykov"):
                try:
                    h_str = log_df[['nazov', 'datum']].tail(5).to_string()
                    s_str = inv_df[['nazov', 'kategoria']].to_string()
                    p_in = f"Analyzuj históriu: {h_str} a sklad: {s_str}. Napíš vtipný a užitočný postreh v 2 vetách."
                    res_in = model.generate_content(p_in)
                    st.session_state.last_insight = res_in.text
                except: st.error("API limit vyčerpaný.")
            if 'last_insight' in st.session_state:
                st.info(st.session_state.last_insight)

        with cr:
            st.subheader("⌛ Čo treba minúť?")
            if not inv_df.empty:
                inv_df['datum_pridania'] = pd.to_datetime(inv_df['datum_pridania'])
                inv_df['dni'] = (datetime.now() - inv_df['datum_pridania']).dt.days
                oldest = inv_df.sort_values(by='dni', ascending=False).head(3)
                for _, row in oldest.iterrows():
                    st.warning(f"**{row['nazov']}** (v sklade už {row['dni']} dní)")

        st.subheader("📈 Aktivita")
        log_df['datum'] = pd.to_datetime(log_df['datum'])
        st.line_chart(log_df.groupby('datum').size())

# === TAB 5: PROFIL ===
with tabs[4]:
    st.header("👤 Nastavenia")
    st.write(f"Prihlásený užívateľ: **{current_user}**")
    
    st.divider()
    st.subheader("🛠 Vývojárske nástroje")
    st.info("⚠️ Tlačidlá na rýchle testovanie.")
    
    if st.button("🛒 Nasimulovať nákup za 150€", use_container_width=True, type="primary"):
        seed_test_data(current_user)
        st.success("Sklad naplnený!")
        time.sleep(1)
        st.rerun()

    if st.button("🗑️ Vymazať celý sklad", use_container_width=True):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM inventory WHERE owner=?", (current_user,))
        conn.commit()
        conn.close()
        st.warning("Sklad vyprázdnený.")
        time.sleep(1)
        st.rerun()

    st.divider()
    if st.button("🚪 Odhlásiť sa"):
        st.session_state.clear()
        st.rerun()

