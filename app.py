import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import json
from pathlib import Path
from datetime import date
from fpdf import FPDF

# --- CONFIGURATION ---
LOGO_PATH = Path("logo.png") 
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]

# --- CONNEXION ---
def get_sheet_client():
    creds_dict = json.loads(st.secrets["GCP_JSON"])
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

# --- FONCTION PDF AVEC LOGO ---
def generate_receipt_pdf(row):
    pdf = FPDF()
    pdf.add_page()
    
    # AJOUT DU LOGO DANS LE PDF
    if LOGO_PATH.exists():
        # Place le logo en haut à gauche (x=10, y=8, largeur=30)
        pdf.image(str(LOGO_PATH), 10, 8, 33)
        pdf.set_y(45) # Descend le texte pour ne pas chevaucher le logo
    
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, SCHOOL_NAME, ln=True, align='C')
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, SCHOOL_PHONE, ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "REÇU DE CAISSE", ln=True, align='C', border=1)
    pdf.ln(10)
    
    # Données du reçu
    amount = row.entree if float(row.entree) > 0 else row.sortie
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, f"N° Reçu : {row.id}", ln=True)
    pdf.cell(0, 10, f"Date : {row.date}", ln=True)
    pdf.cell(0, 10, f"Élève : {row.nom}", ln=True)
    pdf.cell(0, 10, f"Classe : {row.classe}", ln=True)
    pdf.cell(0, 10, f"Motif : {row.designation}", ln=True)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, f"Montant : {float(amount):,.0f} FCFA".replace(",", " "), ln=True)
    
    return pdf.output()

# --- INTERFACE ---
st.set_page_config(page_title="Gestion Caisse", layout="wide")

# Affichage du Logo dans l'application
col1, col2 = st.columns([1, 6])
if LOGO_PATH.exists():
    col1.image(str(LOGO_PATH), width=100)
col2.title("Gestion de caisse scolaire")

# Chargement des données (Assure-toi que les colonnes du Sheets sont en minuscules)
try:
    client = get_sheet_client()
    sh = client.open("Caisse Scolaire").get_worksheet(0)
    df = pd.DataFrame(sh.get_all_records())
    df.columns = [c.lower().strip() for c in df.columns]
    
    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df_mois = df[df['mois'] == mois]
            if not df_mois.empty:
                st.dataframe(df_mois, use_container_width=True)
                
                # Partie Impression
                st.divider()
                st.subheader("📄 Imprimer un reçu")
                selected_nom = st.selectbox("Choisir l'élève", df_mois['nom'].tolist(), key=f"print_{mois}")
                row_to_print = df_mois[df_mois['nom'] == selected_nom].iloc[0]
                
                st.download_button(
                    label="Télécharger le Reçu PDF",
                    data=generate_receipt_pdf(row_to_print),
                    file_name=f"Recu_{selected_nom}.pdf",
                    mime="application/pdf"
                )
            else:
                st.info("Aucune donnée.")
except Exception as e:
    st.error(f"Erreur : {e}")