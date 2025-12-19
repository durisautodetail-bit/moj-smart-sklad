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
api_key = st.secrets["GOOGLE_API_KEY"]

try:
    genai.configure(api_key=api_key)
    
    # === OPRAVA NÁZVOV MODELOV ===
    # Používame "gemini-flash-latest", lebo ten tvojmu účtu funguje.
    # Používame ho pre OBA úkony, aby sme sa vyhli limitom.
    
    # 1. Model na čítanie bločkov
    model = genai.GenerativeModel(
        model_name="gemini-flash-latest", 
        system_instruction="""
        Spracuj bloček do zoznamu potravín.
        Úloha:
        1. Identifikuj potravinu. Ignoruj drogériu.
        2. Odhadni váhu v gramoch (ak chýba, doplň štandard: rožok=45g, chlieb=500g, mlieko=1000g).
        3. Odhadni makrá na 100g (Kcal, Bielkoviny, Sacharidy, Tuky).
        4. Kategória: Mäso, Mliečne, Zelenina, Ovocie, Pečivo, Trvanlivé, Nápoje, Iné.
        
        Výstup JSON (vráť len čistý JSON zoznam):
        [
            {
                "nazov": "Názov",
                "kategoria": "Mliečne",
                "vaha_g": 100,
                "kcal_100g": 100,
                "bielkoviny_100g": 10,
                "sacharidy_100g": 5,
                "tuky_100g": 2
            }
        ]
        """
    )
    
    # 2. Model na Koučing (Tiež Flash - je rýchly a máš naň kvótu)
    coach_model = genai.GenerativeModel("gemini-flash-latest")
    
except Exception as e:
    st.error(f"Chyba konfigurácie: {e}")

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
            item.get('nazov', 'Neznáme'), 
            item.get('kategoria', 'Iné'), 
            item.get('vaha_g', 0), 
            item.get('kcal_100g', 0), 
            item.get('bielkoviny_100g', 0), 
            item.get('sacharidy_100g', 0), 
            item.get('tuky_100g', 0), 
            today
        ))
    conn.commit()
    conn.close()

def get_inventory():
    conn = sqlite3.connect('sklad.db')
    df = pd.read_sql_query("SELECT * FROM inventory", conn)
    conn.close()
    return df

def delete_item(item_id):
    conn = sqlite3.connect('sklad.db')
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    conn.commit()
    conn.close()

def process_file(uploaded_file):
    if uploaded_file.type == "application/pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        page = doc.load_page(0)
        zoom = 1.0 if page.rect.height < 3000 else 0.5
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        return Image.open(uploaded_file)

# --- UI APLIKÁCIE ---
st.set_page_config(page_title="AI Fitness Sklad", layout="wide", page_icon="💪")
init_db()

# BOČNÝ PANEL
with st.sidebar:
    st.header("👤 Profil")
    gender = st.selectbox("Pohlavie", ["Muž", "Žena"])
    age = st.number_input("Vek", 18, 99, 30)
    weight = st.number_input("Váha (kg)", 40, 150, 80)
    height = st.number_input("Výška (cm)", 140, 220, 180)
    activity = st.selectbox("Aktivita", ["Sedavá", "Ľahká", "Stredná", "Vysoká", "Extrémna"])
    goal = st.selectbox("Cieľ", ["Udržiavať", "Chudnúť", "Pribrať"])
    
    factor = {"Sedavá": 1.2, "Ľahká": 1.375, "Stredná": 1.55, "Vysoká": 1.725, "Extrémna": 1.9}
    bmr = (10 * weight) + (6.25 * height) - (5 * age) + (5 if gender == "Muž" else -161)
    tdee = bmr * factor[activity]
    target = tdee - 500 if goal == "Chudnúť" else (tdee + 300 if goal == "Pribrať" else tdee)
    
    st.metric("Denný cieľ", f"{int(target)} kcal")

st.title("💪 AI Fitness Sklad (Final Fix)")

tab1, tab2, tab3 = st.tabs(["📸 Hromadné Skenovanie", "🍎 Môj Sklad", "🤖 AI Tréner"])

# === TAB 1: SKENOVANIE ===
with tab1:
    st.subheader("Nahraj bločky (Multi-Upload)")
    uploaded_files = st.file_uploader("Vyber súbory", type=["jpg", "png", "pdf"], accept_multiple_files=True)
    
    if uploaded_files:
        st.write(f"Súborov na spracovanie: {len(uploaded_files)}")
        
        if st.button("Analyzovať všetko 🚀", type="primary"):
            all_items = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i, uploaded_file in enumerate(uploaded_files):
                status_text.text(f"Analyzujem: {uploaded_file.name}...")
                try:
                    image = process_file(uploaded_file)
                    
                    # Ošetrenie preťaženia API (Retry logic)
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = model.generate_content(["Analyzuj bloček.", image])
                            break # Ak prejde, vyskočíme z cyklu
                        except ResourceExhausted:
                            status_text.warning(f"Limit API dosiahnutý. Čakám 10 sekúnd... (Pokus {attempt+1}/{max_retries})")
                            time.sleep(10)
                        except Exception as inner_e:
                            if "404" in str(inner_e):
                                st.error("Chyba modelu: Názov modelu je nesprávny. Kontaktuj vývojára.")
                                break
                            raise inner_e

                    clean_text = clean_json_response(response.text)
                    if clean_text:
                        items = json.loads(clean_text)
                        if isinstance(items, list):
                            all_items.extend(items)
                except Exception as e:
                    st.error(f"Chyba pri {uploaded_file.name}: {e}")
                
                progress_bar.progress((i + 1) / len(uploaded_files))
                time.sleep(1) 
            
            status_text.text("Hotovo!")
            if all_items:
                st.session_state.scan_result = all_items
                st.success(f"✅ Hotovo! Nájdených {len(all_items)} položiek.")

    if 'scan_result' in st.session_state and st.session_state.scan_result:
        df_scan = pd.DataFrame(st.session_state.scan_result)
        edited_scan = st.data_editor(df_scan, use_container_width=True, num_rows="dynamic")
        
        if st.button("📥 Naskladniť Všetko"):
            items_to_save = edited_scan.to_dict('records')
            add_to_inventory(items_to_save)
            del st.session_state.scan_result
            st.toast("Uložené!", icon="✅")
            time.sleep(1)
            st.rerun()

# === TAB 2: SKLAD ===
with tab2:
    st.subheader("📦 Zásoby")
    df = get_inventory()
    
    if not df.empty:
        df['Total Kcal'] = (df['vaha_g'] / 100) * df['kcal_100g']
        df['Total B'] = (df['vaha_g'] / 100) * df['bielkoviny_100g']
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Energia", f"{int(df['Total Kcal'].sum())} kcal")
        c2.metric("Bielkoviny", f"{int(df['Total B'].sum())} g")
        c3.metric("Položiek", len(df))
        
        st.divider()
        
        df['Vybrať'] = False
        edited_df = st.data_editor(
            df[['Vybrať', 'id', 'nazov', 'vaha_g', 'kcal_100g', 'bielkoviny_100g', 'tuky_100g', 'sacharidy_100g']],
            hide_index=True,
            use_container_width=True
        )
        
        sel = edited_df[edited_df['Vybrať'] == True]
        if not sel.empty:
            if st.button(f"🗑️ Zjesť ({len(sel)})"):
                for i, r in sel.iterrows():
                    delete_item(r['id'])
                st.rerun()
    else:
        st.info("Sklad je prázdny.")

# === TAB 3: AI TRÉNER ===
with tab3:
    st.header("🤖 AI Poradca")
    if st.button("Poradiť 🧠"):
        df = get_inventory()
        if not df.empty:
            with st.spinner("Analyzujem (Model Flash)..."):
                inv_txt = df.to_string()
                prof_txt = f"Cieľ: {goal}, Váha: {weight}, Aktivita: {activity}, Limit: {int(target)} kcal"
                prompt = f"Si tréner. Profil: {prof_txt}. Sklad: {inv_txt}. 1. Hodnotenie skladu? 2. Varovanie? 3. Recept?"
                
                try:
                    res = coach_model.generate_content(prompt)
                    st.markdown(res.text)
                except ResourceExhausted:
                    st.error("⚠️ Príliš veľa požiadaviek. Počkaj 10 sekúnd a skús to znova.")
                except Exception as e:
                    st.error(f"Chyba: {e}")
        else:

            st.warning("Prázdny sklad.")
