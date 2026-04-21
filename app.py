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

# --- CONFIGURATION ET STYLE ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

# Ce bloc efface les logos et menus Streamlit pour que ce soit plus propre sur mobile
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stDeployButton {display:none;}
    </style>
    """, unsafe_allow_html=True)

LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]

# --- CONNEXION ---
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

# --- CHARGEMENT SÉCURISÉ (Empêche la disparition des données) ---
def load_mouvements(mois_cible=None):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    # On force la lecture brute pour éviter l'erreur "duplicates"
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On définit nous-mêmes les colonnes pour ne pas dépendre des erreurs du fichier
    df = pd.DataFrame(rows[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Nettoyage automatique des valeurs
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    if mois_cible:
        df = df[df["mois"] == mois_cible]
    
    return df

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = get_sheet()
    if not sheet: return
    # On calcule l'ID basé sur le nombre total de lignes réelles
    all_rows = sheet.get_all_values()
    next_id = len(all_rows)
    
    sheet.append_row([
        next_id, mois, movement_date.isoformat(),
        designation.strip(), nom.strip(), classe.strip(),
        float(entree or 0), float(sortie or 0)
    ])

def delete_mouvement(row_id):
    sheet = get_sheet()
    if not sheet: return
    all_values = sheet.get_all_values()
    for i, row in enumerate(all_values):
        if i == 0: continue
        if len(row) > 0 and str(row[0]) == str(row_id):
            sheet.delete_rows(i + 1)
            break

# --- PDF (Optimisé pour la vitesse) ---
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
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, clean_pdf_text(f"Recu de {mois}"), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    
    for k, v in [("ID", row.id), ("Nom", row.nom), ("Classe", row.classe), ("Montant", f"{row.entree + row.sortie} FCFA")]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(40, 10, f"{k}: ")
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 10, clean_pdf_text(v), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    return pdf.output()

# --- INTERFACE ---
def main():
    # Login simple
    if "auth" not in st.session_state:
        st.session_state.auth = False
    
    if not st.session_state.auth:
        pwd = st.text_input("Mot de passe", type="password")
        if pwd == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state.auth = True
            st.rerun()
        return

    st.title("Gestion de Caisse Scolaire")
    
    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_mouvements(mois)
            
            # Formulaire d'ajout
            with st.expander(f"➕ Ajouter une opération pour {mois}"):
                with st.form(f"form_{mois}", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    d = c1.date_input("Date", value=date.today())
                    n = c1.text_input("Nom de l'élève")
                    des = c2.text_input("Désignation")
                    cl = c2.text_input("Classe")
                    e = c1.number_input("Entrée (Somme reçue)", min_value=0.0)
                    s = c2.number_input("Sortie (Dépense)", min_value=0.0)
                    
                    if st.form_submit_button("Enregistrer l'opération"):
                        if n and des:
                            add_mouvement(mois, d, des, n, cl, e, s)
                            st.success("Enregistré !")
                            st.rerun()
                        else:
                            st.warning("Veuillez remplir le nom et la désignation.")

            # Tableau des données
            if not df.empty:
                st.dataframe(df[["id", "date", "nom", "classe", "entree", "sortie"]], use_container_width=True, hide_index=True)
                
                # Actions : Supprimer / Reçu
                c_del, c_pdf = st.columns(2)
                with c_del:
                    to_del = st.selectbox("Choisir une ligne à supprimer", df["id"].tolist(), key=f"del_{mois}")
                    if st.button("Supprimer définitivement", key=f"btn_del_{mois}"):
                        delete_mouvement(to_del)
                        st.rerun()
                with c_pdf:
                    sel_recu = st.selectbox("Choisir pour le reçu", df["id"].tolist(), key=f"pdf_{mois}")
                    if st.button("Générer le PDF", key=f"btn_pdf_{mois}"):
                        row = df[df["id"] == sel_recu].iloc[0]
                        pdf_data = generate_receipt_pdf(row, mois)
                        st.download_button("Télécharger le Recu", pdf_data, f"recu_{row.nom}.pdf", "application/pdf")
            else:
                st.info(f"Aucune donnée pour {mois}")

if __name__ == "__main__":
    main()