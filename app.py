import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import date
from pathlib import Path
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import unicodedata
import re

# --- CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

# Masquer les menus Streamlit pour plus de propreté sur mobile
st.markdown("""<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}</style>""", unsafe_allow_html=True)

LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]

# --- CONNEXION SÉCURISÉE ---
def get_sheet():
    try:
        creds_dict = json.loads(st.secrets["GCP_JSON"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open("Caisse Scolaire").get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None

# --- ACTIONS SUR LES DONNÉES ---
def load_mouvements(mois_cible=None):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    # On force la lecture par index de colonne pour éviter l'erreur "duplicates"
    data = sheet.get_all_values()
    if len(data) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On définit nos propres colonnes proprement
    df = pd.DataFrame(data[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Nettoyage des chiffres (très important pour les calculs)
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    if mois_cible:
        df = df[df["mois"] == mois_cible]
    
    # Calculs automatiques
    df["solde"] = df["entree"] - df["sortie"]
    df["solde_cumule"] = df["solde"].cumsum()
    return df

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = get_sheet()
    if not sheet: return
    all_rows = sheet.get_all_values()
    next_id = len(all_rows) # Génère l'ID automatiquement
    
    sheet.append_row([
        str(next_id), mois, movement_date.isoformat(),
        designation.strip(), nom.strip(), classe.strip(),
        str(entree), str(sortie)
    ])

def delete_mouvement(row_id):
    sheet = get_sheet()
    if not sheet: return
    # On récupère tout en texte brut pour trouver la ligne
    data = sheet.get_all_values()
    for i, row in enumerate(data):
        if i == 0: continue # On saute l'entête
        if len(row) > 0 and str(row[0]) == str(row_id):
            sheet.delete_rows(i + 1)
            break

# --- FONCTIONS PDF (Version Rapide) ---
def clean_pdf_text(value):
    return str(value).encode("latin-1", "replace").decode("latin-1")

def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.add_page()
    if LOGO_PATH.exists():
        pdf.image(str(LOGO_PATH), x=87, y=10, w=36)
        pdf.ln(40)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, clean_pdf_text(SCHOOL_NAME), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, clean_pdf_text(f"REÇU DE CAISSE - {mois}"), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    
    pdf.set_font("Helvetica", "", 11)
    infos = [("ID", row.id), ("Nom", row.nom), ("Classe", row.classe), 
             ("Désignation", row.designation), ("Montant", f"{row.entree + row.sortie} FCFA")]
    for k, v in infos:
        pdf.cell(40, 10, f"{k}:", border=0)
        pdf.cell(0, 10, clean_pdf_text(v), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return pdf.output()

# --- INTERFACE ---
def main():
    if "auth" not in st.session_state: st.session_state.auth = False
    if not st.session_state.auth:
        pwd = st.text_input("Mot de passe", type="password")
        if pwd == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state.auth = True
            st.rerun()
        return

    # Titre et Logo
    c_l, c_t = st.columns([1, 5])
    if LOGO_PATH.exists(): c_l.image(str(LOGO_PATH), width=70)
    c_t.title("Gestion de Caisse Scolaire")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_mouvements(mois)
            
            # Résumé financier du mois
            c1, c2, c3 = st.columns(3)
            c1.metric("Entrées", f"{df['entree'].sum()} FCFA")
            c2.metric("Sorties", f"{df['sortie'].sum()} FCFA")
            c3.metric("Solde", f"{df['entree'].sum() - df['sortie'].sum()} FCFA")

            # Formulaire d'ajout
            with st.expander(f"➕ Ajouter pour {mois}"):
                with st.form(f"f_{mois}"):
                    d = st.date_input("Date", value=date.today())
                    n = st.text_input("Nom de l'élève")
                    cl = st.text_input("Classe")
                    des = st.text_input("Désignation (ex: Inscription)")
                    e = st.number_input("Entrée (Reçu)", min_value=0.0)
                    s = st.number_input("Sortie (Dépense)", min_value=0.0)
                    if st.form_submit_button("Enregistrer"):
                        add_mouvement(mois, d, des, n, cl, e, s)
                        st.success("Enregistré !")
                        st.rerun()

            if not df.empty:
                st.dataframe(df[["id", "date", "nom", "designation", "entree", "sortie"]], hide_index=True, use_container_width=True)
                
                # Zone de suppression et PDF
                col_a, col_b = st.columns(2)
                with col_a:
                    sel_del = st.selectbox("Choisir une ligne à supprimer", df["id"].tolist(), key=f"s_{mois}")
                    if st.button("Supprimer la ligne", key=f"b_{mois}", type="primary"):
                        delete_mouvement(sel_id := sel_del)
                        st.rerun()
                with col_b:
                    sel_pdf = st.selectbox("Choisir pour le reçu", df["id"].tolist(), key=f"p_{mois}")
                    if st.button("Générer Reçu PDF", key=f"bp_{mois}"):
                        row = df[df["id"] == sel_pdf].iloc[0]
                        st.download_button("Télécharger", generate_receipt_pdf(row, mois), f"recu_{row.nom}.pdf")
            else:
                st.info("Aucune opération.")

if __name__ == "__main__":
    main()