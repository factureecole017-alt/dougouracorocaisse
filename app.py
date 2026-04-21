import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import json
import re
import unicodedata
from pathlib import Path
from datetime import date
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# --- CONFIGURATION ---
LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# --- CONNEXION GOOGLE SHEETS ---
def get_sheet_client():
    try:
        secret_json = st.secrets["GCP_JSON"]
        creds_dict = json.loads(secret_json)
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        st.stop()

def init_db():
    client = get_sheet_client()
    spreadsheet = client.open("Caisse Scolaire")
    return spreadsheet.get_worksheet(0)

# --- FONCTIONS DE DONNÉES ---
def load_mouvements():
    try:
        sheet = init_db()
        records = sheet.get_all_records()
        if not records:
            return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
        df = pd.DataFrame(records)
        df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
        df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"Erreur de chargement : {e}")
        return pd.DataFrame()

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = init_db()
    new_id = len(sheet.get_all_values())
    sheet.append_row([new_id, mois, movement_date.isoformat(), designation, nom, classe, float(entree), float(sortie)])

def delete_mouvement(row_id):
    sheet = init_db()
    data = sheet.get_all_records()
    for index, row in enumerate(data):
        if int(row['id']) == int(row_id):
            sheet.delete_rows(index + 2)
            break

# --- UTILITAIRES PDF ---
def clean_pdf_text(value):
    return str(value).encode("latin-1", "replace").decode("latin-1")

def money(value):
    return f"{float(value):,.0f}".replace(",", " ") + " FCFA"

def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, clean_pdf_text(SCHOOL_NAME), ln=True, align='C')
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, clean_pdf_text(SCHOOL_PHONE), ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "REÇU DE CAISSE", ln=True, align='C')
    pdf.ln(5)
    
    amount = row.entree if float(row.entree) > 0 else row.sortie
    fields = [("N° Reçu", row.id), ("Date", row.date), ("Élève", row.nom), ("Classe", row.classe), ("Motif", row.designation), ("Montant", money(amount))]
    
    for label, val in fields:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(40, 10, f"{label}: ", 0)
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 10, clean_pdf_text(val), 0, ln=True)
    
    pdf.ln(20)
    pdf.cell(0, 10, "Signature de la Direction: ___________________", align='R', ln=True)
    return pdf.output()

# --- INTERFACE ---
def show_month(mois, all_df):
    st.subheader(f"Opérations de {mois}")
    
    with st.expander(f"➕ Ajouter une opération"):
        with st.form(f"form_{mois}"):
            c1, c2 = st.columns(2)
            m_date = c1.date_input("Date", date.today())
            nom = c2.text_input("Nom de l'élève")
            design = c1.text_input("Désignation")
            classe = c2.text_input("Classe")
            entree = c1.number_input("Entrée", min_value=0.0, step=500.0)
            sortie = c2.number_input("Sortie", min_value=0.0, step=500.0)
            if st.form_submit_button("Valider"):
                add_mouvement(mois, m_date, design, nom, classe, entree, sortie)
                st.rerun()

    df_mois = all_df[all_df["mois"] == mois]
    if not df_mois.empty:
        st.dataframe(df_mois, use_container_width=True)
        
        st.divider()
        st.subheader("🗑️ Suppression & 📄 Reçus")
        # Sélection par nom d'élève pour plus de clarté
        options = {f"ID {r.id} | {r.nom}": r.id for r in df_mois.itertuples()}
        selected_label = st.selectbox("Sélectionner une ligne", list(options.keys()), key=f"sel_{mois}")
        selected_id = options[selected_label]
        
        col1, col2 = st.columns(2)
        if col1.button("Supprimer cette ligne", type="primary", key=f"del_{mois}"):
            delete_mouvement(selected_id)
            st.rerun()
            
        row_data = df_mois[df_mois["id"] == selected_id].iloc[0]
        col2.download_button("Imprimer le Reçu (PDF)", data=generate_receipt_pdf(row_data, mois), file_name=f"Recu_{row_data.nom}.pdf", mime="application/pdf")
    else:
        st.info("Aucune donnée enregistrée pour ce mois.")

def main():
    st.set_page_config(page_title="Caisse Dougouracoro", layout="wide")
    
    # Vérification simple du mot de passe
    if "auth" not in st.session_state:
        st.session_state.auth = False
        
    if not st.session_state.auth:
        st.title("Connexion")
        pwd = st.text_input("Mot de passe", type="password")
        if st.button("Entrer"):
            if pwd == st.secrets["MON_MOT_DE_PASSE"]:
                st.session_state.auth = True
                st.rerun()
            else: st.error("Code incorrect")
        return

    st.title("🏫 Gestion de caisse scolaire")
    all_df = load_mouvements()
    
    # Sidebar
    st.sidebar.header("Tableau de Bord")
    solde = all_df["entree"].sum() - all_df["sortie"].sum() if not all_df.empty else 0
    st.sidebar.metric("Solde Global", money(solde))
    if st.sidebar.button("Déconnexion"):
        st.session_state.auth = False
        st.rerun()

    # Onglets
    tabs = st.tabs(MONTHS)
    for tab, mois in zip(tabs, MONTHS):
        with tab:
            show_month(mois, all_df)

if __name__ == "__main__":
    main()