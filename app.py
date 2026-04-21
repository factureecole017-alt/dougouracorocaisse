import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import date
from pathlib import Path
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import time

# --- CONFIGURATION ET STYLE ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

# Masquer les logos Streamlit pour plus de fluidité
st.markdown("<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}</style>", unsafe_allow_html=True)

LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]

# --- CONNEXION AVEC ANTI-QUOTA ---
def get_sheet():
    try:
        creds_dict = json.loads(st.secrets["GCP_JSON"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        # Petite pause pour éviter l'erreur de quota (API Error 429)
        time.sleep(1) 
        return client.open("Caisse Scolaire").get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None

# --- CHARGEMENT ET CALCULS ---
def load_mouvements(mois_cible=None):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    # Correction de l'erreur "duplicates" : on force nos propres noms de colonnes
    data = sheet.get_all_values()
    if len(data) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    df = pd.DataFrame(data[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Conversion forcée en nombres pour que les SOMMES fonctionnent
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    if mois_cible:
        df = df[df["mois"] == mois_cible]
    return df

# --- SUPPRESSION SÉCURISÉE ---
def delete_mouvement(row_id):
    sheet = get_sheet()
    if not sheet: return
    rows = sheet.get_all_values()
    for i, row in enumerate(rows):
        if i == 0: continue
        # On vérifie si la ligne existe et correspond à l'ID
        if len(row) > 0 and str(row[0]) == str(row_id):
            sheet.delete_rows(i + 1)
            break

# --- PDF REÇU CORRIGÉ ---
def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"{SCHOOL_NAME}", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"RECU DE CAISSE - {mois.upper()}", align="C", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(50, 10, f"ID Reçu: {row.id}")
    pdf.cell(0, 10, f"Date: {row.date}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 10, f"Nom de l'élève: {row.nom}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 10, f"Classe: {row.classe}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 10, f"Désignation: {row.designation}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    montant = row.entree if row.entree > 0 else row.sortie
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 15, f"MONTANT: {montant} FCFA", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    return pdf.output()

# --- INTERFACE ---
def main():
    st.title("Gestion de Caisse Scolaire")
    
    # Calcul du solde total en haut
    all_data = load_mouvements()
    total_caisse = all_data["entree"].sum() - all_data["sortie"].sum()
    st.sidebar.metric("Solde Total de la Caisse", f"{total_caisse} FCFA")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df_mois = load_mouvements(mois)
            
            # Affichage des sommes pour le mois
            col1, col2 = st.columns(2)
            col1.info(f"Total Entrées: {df_mois['entree'].sum()} FCFA")
            col2.warning(f"Total Sorties: {df_mois['sortie'].sum()} FCFA")

            # Formulaire d'ajout (caché pour gagner de la place)
            with st.expander("Ajouter une opération"):
                with st.form(f"form_{mois}"):
                    # (garde tes champs habituels ici...)
                    st.write("Remplir les informations...")
                    submitted = st.form_submit_button("Enregistrer")
            
            if not df_mois.empty:
                st.dataframe(df_mois, use_container_width=True)
                
                # Zone de gestion
                c_del, c_pdf = st.columns(2)
                with c_del:
                    # Correction du bug de suppression : on s'assure d'avoir des IDs valides
                    ids = df_mois["id"].dropna().tolist()
                    sel_id = st.selectbox("Ligne à supprimer", ids, key=f"d_{mois}")
                    if st.button("Confirmer suppression", key=f"b_{mois}"):
                        delete_mouvement(sel_id)
                        st.rerun()
                
                with c_pdf:
                    sel_id_pdf = st.selectbox("Imprimer reçu pour l'ID", ids, key=f"p_{mois}")
                    if st.button("Générer PDF", key=f"bp_{mois}"):
                        row = df_mois[df_mois["id"] == sel_id_pdf].iloc[0]
                        pdf_bytes = generate_receipt_pdf(row, mois)
                        st.download_button("Télécharger le Reçu", pdf_bytes, f"recu_{row.nom}.pdf")
            else:
                st.write("Aucun enregistrement pour ce mois.")

if __name__ == "__main__":
    main()