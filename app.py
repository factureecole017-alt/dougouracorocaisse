import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import date
from pathlib import Path
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

# Style pour mobile : cache le logo Streamlit et les menus pour libérer de l'espace
st.markdown("""<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;} .stDeployButton {display:none;}</style>""", unsafe_allow_html=True)

LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
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

# --- 3. LOGIQUE MÉTIER ---
def load_data(mois_selectionne=None):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    values = sheet.get_all_values()
    if len(values) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On définit nos colonnes manuellement pour ignorer les erreurs du fichier Excel
    df = pd.DataFrame(values[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Conversion forcée en nombres pour les calculs (indispensable !)
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    if mois_selectionne:
        df = df[df["mois"] == mois_selectionne]
    return df

def delete_row(id_a_supprimer):
    sheet = get_sheet()
    if not sheet: return
    data = sheet.get_all_values()
    # On cherche l'ID dans la première colonne (index 0)
    for i, row in enumerate(data):
        if i == 0: continue
        if len(row) > 0 and str(row[0]) == str(id_a_supprimer):
            sheet.delete_rows(i + 1)
            break

# --- 4. IMPRESSION PDF ---
def make_pdf(row, mois):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, SCHOOL_NAME, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"RECU DE CAISSE - {mois.upper()}", border=1, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, f"ID: {row['id']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 10, f"Nom: {row['nom']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 10, f"Classe: {row['classe']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 10, f"Désignation: {row['designation']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    montant = float(row['entree']) if float(row['entree']) > 0 else float(row['sortie'])
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 15, f"MONTANT: {montant} FCFA", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return pdf.output()

# --- 5. INTERFACE ---
def main():
    if "auth" not in st.session_state: st.session_state.auth = False
    if not st.session_state.auth:
        pwd = st.text_input("Mot de passe", type="password")
        if pwd == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state.auth = True
            st.rerun()
        return

    st.title("Gestion de Caisse")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_data(mois)
            
            # Affichage des sommes
            c1, c2 = st.columns(2)
            c1.metric("Entrées", f"{df['entree'].sum()} FCFA")
            c2.metric("Sorties", f"{df['sortie'].sum()} FCFA")

            # Formulaire d'ajout
            with st.expander("Ajouter une opération"):
                with st.form(f"f_{mois}"):
                    d = st.date_input("Date", value=date.today())
                    nom = st.text_input("Nom élève")
                    des = st.text_input("Désignation")
                    cl = st.text_input("Classe")
                    e = st.number_input("Entrée", min_value=0.0)
                    s = st.number_input("Sortie", min_value=0.0)
                    if st.form_submit_button("Enregistrer"):
                        sheet = get_sheet()
                        new_id = len(sheet.get_all_values())
                        sheet.append_row([str(new_id), mois, d.isoformat(), des, nom, cl, str(e), str(s)])
                        st.rerun()

            if not df.empty:
                st.dataframe(df[["id", "nom", "designation", "entree", "sortie"]], hide_index=True)
                
                # --- SUPPRESSION & IMPRESSION ---
                st.divider()
                col_del, col_pdf = st.columns(2)
                
                with col_del:
                    id_list = df["id"].tolist()
                    sel_id = st.selectbox("Sélectionner l'ID pour SUPPRIMER", id_list, key=f"d_{mois}")
                    if st.button("Supprimer définitivement", key=f"bd_{mois}", type="primary"):
                        delete_row(sel_id)
                        st.rerun()
                
                with col_pdf:
                    sel_id_p = st.selectbox("Sélectionner l'ID pour IMPRIMER", id_list, key=f"p_{mois}")
                    if st.button("Générer le PDF", key=f"bp_{mois}"):
                        row_data = df[df["id"] == sel_id_p].iloc[0]
                        pdf_bytes = make_pdf(row_data, mois)
                        st.download_button("Télécharger le Reçu", pdf_bytes, f"recu_{sel_id_p}.pdf", "application/pdf")
            else:
                st.info("Aucune donnée.")

if __name__ == "__main__":
    main()