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

# --- CONFIGURATION INITIALE ---
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
        st.error(f"Erreur de connexion Google : {e}")
        st.stop()

def init_db():
    try:
        client = get_sheet_client()
        # On ouvre par le nom exact du fichier
        spreadsheet = client.open("Caisse Scolaire")
        return spreadsheet.get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur d'accès au fichier : {e}")
        st.stop()

# --- GESTION DES DONNÉES ---
def load_mouvements():
    try:
        sheet = init_db()
        records = sheet.get_all_records()
        if not records:
            return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
        df = pd.DataFrame(records)
        # Nettoyage des types
        df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
        df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"Erreur de chargement : {e}")
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    try:
        sheet = init_db()
        new_id = len(sheet.get_all_values())
        sheet.append_row([new_id, mois, movement_date.isoformat(), designation.strip(), nom.strip(), classe.strip(), float(entree or 0), float(sortie or 0)])
        st.success("Opération enregistrée !")
    except Exception as e:
        st.error(f"Erreur d'ajout : {e}")

def delete_mouvement(row_id):
    try:
        sheet = init_db()
        data = sheet.get_all_records()
        for index, row in enumerate(data):
            if int(row['id']) == int(row_id):
                sheet.delete_rows(index + 2)
                st.success("Ligne supprimée !")
                break
    except Exception as e:
        st.error(f"Erreur de suppression : {e}")

# --- UTILITAIRES & PDF ---
def money(value):
    return f"{float(value):,.0f}".replace(",", " ") + " FCFA"

def clean_pdf_text(value):
    return str(value).encode("latin-1", "replace").decode("latin-1")

def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, clean_pdf_text(SCHOOL_NAME), ln=True, align='C')
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "REÇU DE CAISSE", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 12)
    amount = row.entree if float(row.entree) > 0 else row.sortie
    pdf.cell(0, 10, f"Date: {row.date}", ln=True)
    pdf.cell(0, 10, f"Nom: {row.nom}", ln=True)
    pdf.cell(0, 10, f"Classe: {row.classe}", ln=True)
    pdf.cell(0, 10, f"Motif: {row.designation}", ln=True)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, f"Montant: {money(amount)}", ln=True)
    return pdf.output()

# --- INTERFACE ---
def check_password():
    if st.session_state.get("authenticated"): return True
    st.title("Connexion")
    password = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter"):
        if password == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else: st.error("Mot de passe incorrect.")
    return False

def show_month(mois, all_df):
    st.header(f"Opérations de {mois}")
    
    # Formulaire d'ajout
    with st.expander(f"Ajouter une opération pour {mois}"):
        with st.form(f"form_{mois}"):
            col1, col2 = st.columns(2)
            m_date = col1.date_input("Date", date.today())
            design = col1.text_input("Désignation")
            nom = col2.text_input("Nom de l'élève")
            classe = col2.text_input("Classe")
            entree = col1.number_input("Entrée (Somme reçue)", min_value=0.0)
            sortie = col2.number_input("Sortie (Dépense)", min_value=0.0)
            if st.form_submit_button("Enregistrer"):
                add_mouvement(mois, m_date, design, nom, classe, entree, sortie)
                st.rerun()

    # Affichage des données
    df_mois = all_df[all_df["mois"] == mois]
    if not df_mois.empty:
        st.dataframe(df_mois)
        
        # Actions (Suppression / Reçu)
        st.subheader("Actions")
        selected_id = st.selectbox("Choisir une opération", df_mois["id"].tolist(), format_func=lambda x: f"ID {x} - {df_mois[df_mois['id']==x]['nom'].values[0]}")
        col_del, col_rec = st.columns(2)
        
        if col_del.button("Supprimer", key=f"del_{mois}", type="primary"):
            delete_mouvement(selected_id)
            st.rerun()
            
        row = df_mois[df_mois["id"] == selected_id].iloc[0]
        col_rec.download_button("Télécharger le reçu", data=generate_receipt_pdf(row, mois), file_name=f"Recu_{row.nom}.pdf", mime="application/pdf")
    else:
        st.info("Aucune donnée pour ce mois.")

def main():
    st.set_page_config(page_title="Caisse scolaire", layout="wide")
    if not check_password(): return
    
    all_df = load_mouvements()
    
    # Sidebar
    st.sidebar.title("Tableau de Bord")
    total_e = all_df["entree"].sum()
    total_s = all_df["sortie"].sum()
    st.sidebar.metric("Solde Total", money(total_e - total_s))
    if st.sidebar.button("Déconnexion"):
        st.session_state["authenticated"] = False
        st.rerun()

    # Onglets
    tabs = st.tabs(MONTHS)
    for tab, mois in zip(tabs, MONTHS):
        with tab:
            show_month(mois, all_df)

if __name__ == "__main__":
    main()