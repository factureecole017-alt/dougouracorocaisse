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

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

# Nettoyage visuel pour mobile (cache les éléments inutiles de Streamlit)
st.markdown("""<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;} .stDeployButton {display:none;}</style>""", unsafe_allow_html=True)

LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]

# --- 2. CONNEXION ---
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

# --- 3. GESTION DES DONNÉES (BLINDÉE) ---
def load_mouvements(mois_cible=None):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    # On force la lecture par position pour éviter l'erreur "duplicates"
    data = sheet.get_all_values()
    if len(data) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On ignore les titres du fichier Excel et on impose les nôtres
    df = pd.DataFrame(data[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Nettoyage forcé pour que les SOMMES fonctionnent enfin
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    if mois_cible:
        df = df[df["mois"] == mois_cible]
    
    # Calcul des soldes
    df["solde"] = df["entree"] - df["sortie"]
    return df

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = get_sheet()
    if not sheet: return
    # L'ID est simplement le nombre de lignes + 1
    next_id = len(sheet.get_all_values())
    sheet.append_row([
        str(next_id), mois, movement_date.isoformat(),
        designation.strip(), nom.strip(), classe.strip(),
        str(float(entree)), str(float(sortie))
    ])

def delete_mouvement(row_id):
    sheet = get_sheet()
    if not sheet: return
    data = sheet.get_all_values()
    # On cherche l'ID dans la colonne 0
    for i, row in enumerate(data):
        if i == 0: continue
        if len(row) > 0 and str(row[0]) == str(row_id):
            sheet.delete_rows(i + 1)
            break

# --- 4. PDF ---
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
    fields = [("N° Reçu", row.id), ("Nom Éléve", row.nom), ("Classe", row.classe), 
              ("Désignation", row.designation), ("Montant", f"{row.entree + row.sortie} FCFA")]
    for k, v in fields:
        pdf.cell(40, 10, f"{k}:", border=0)
        pdf.cell(0, 10, clean_pdf_text(v), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return pdf.output()

# --- 5. INTERFACE ---
def main():
    # Login
    if "auth" not in st.session_state: st.session_state.auth = False
    if not st.session_state.auth:
        pwd = st.text_input("Mot de passe", type="password")
        if pwd == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state.auth = True
            st.rerun()
        return

    # Titre
    c_l, c_t = st.columns([1, 6])
    if LOGO_PATH.exists(): c_l.image(str(LOGO_PATH), width=70)
    c_t.title("Gestion de Caisse Scolaire")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_mouvements(mois)
            
            # 1. Sommes du mois
            e_total = df['entree'].sum()
            s_total = df['sortie'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Reçu", f"{e_total} FCFA")
            c2.metric("Total Dépensé", f"{s_total} FCFA")
            c3.metric("Solde Mensuel", f"{e_total - s_total} FCFA")

            # 2. Formulaire d'ajout
            with st.expander(f"➕ Ajouter une opération"):
                with st.form(f"form_{mois}", clear_on_submit=True):
                    d = st.date_input("Date", value=date.today())
                    n = st.text_input("Nom de l'élève")
                    cl = st.text_input("Classe")
                    des = st.text_input("Désignation")
                    ent = st.number_input("Somme Reçue", min_value=0.0)
                    sor = st.number_input("Somme Sortie", min_value=0.0)
                    if st.form_submit_button("Enregistrer"):
                        if n and des:
                            add_mouvement(mois, d, des, n, cl, ent, sor)
                            st.rerun()

            # 3. Tableau et Actions
            if not df.empty:
                st.dataframe(df[["id", "date", "nom", "designation", "entree", "sortie"]], hide_index=True, use_container_width=True)
                
                col_a, col_b = st.columns(2)
                with col_a:
                    st.subheader("Supprimer")
                    sel_del = st.selectbox("Ligne à supprimer", df["id"].tolist(), key=f"del_{mois}")
                    if st.button("Confirmer Suppression", key=f"bdel_{mois}", type="primary"):
                        delete_mouvement(sel_del)
                        st.rerun()
                with col_b:
                    st.subheader("Imprimer")
                    sel_pdf = st.selectbox("Reçu pour l'élève", df["id"].tolist(), key=f"pdf_{mois}")
                    if st.button("Générer Reçu", key=f"bpdf_{mois}"):
                        row_pdf = df[df["id"] == sel_pdf].iloc[0]
                        st.download_button("Télécharger PDF", generate_receipt_pdf(row_pdf, mois), f"recu_{row_pdf.nom}.pdf")
            else:
                st.info("Aucune donnée pour ce mois.")

if __name__ == "__main__":
    main()