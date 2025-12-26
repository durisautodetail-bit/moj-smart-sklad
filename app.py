# === TAB 1: SKLAD (S tlačidlom na rýchly nákup) ===
with tabs[0]:
    st.header(f"📦 Sklad užívateľa {current_user}")
    
    # --- TESTOVACIE TLAČIDLO PRIAMO TU ---
    col_test, col_add = st.columns([1, 2])
    with col_test:
        if st.button("🛒 TEST: Nákup 150€", type="primary"):
            seed_test_data(current_user)
            st.toast("Naskladnené! Refreshujem...")
            time.sleep(1)
            st.rerun()
            
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
        st.info("Tvoj sklad je prázdny. Klikni na tlačidlo 'TEST: Nákup 150€' hore, alebo naskenuj bloček.")
