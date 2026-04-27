import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
import os
from datetime import date
import time
from fpdf import FPDF

# --- CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

hide_st_style = '''
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            div[data-baseweb="tab-list"] {
                overflow-x: auto !important;
                flex-wrap: nowrap !important;
                scrollbar-width: thin;
            }
            button[data-baseweb="tab"] {
                white-space: nowrap !important;
                flex-shrink: 0 !important;
            }
            </style>
            '''
st.markdown(hide_st_style, unsafe_allow_html=True)

SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
LOGO_PATH = "logo.png"
DIRECTOR_PHONE = "+223 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août"]
COLS = ["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"]
NUM_COLS = ["entree", "sortie"]

# --- CHARGEMENT SÉCURISÉ DES SECRETS ---
def _load_gcp_credentials():
    try:
        # On essaie d'abord de lire le secret comme un dictionnaire direct
        raw = st.secrets["GCP_JSON"]
        if isinstance(raw, dict):
            return dict(raw)
        
        # Si c'est une chaîne, on la nettoie et on la décode
        text = str(raw).strip()
        creds_dict = json.loads(text, strict=False)
        
        # Correction spécifique pour la clé privée
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            
        return creds_dict
    except Exception as e:
        st.error(f"Erreur technique de clé : {e}")
        return None

# --- CONNEXION GOOGLE SHEETS ---
@st.cache_resource(show_spinner=False)
def get_sheet():
    try:
        creds_dict = _load_gcp_credentials()
        if not creds_dict:
            return None
            
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        # Assure-toi que le nom du fichier est exact
        return client.open("Caisse Scolaire").get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None

# --- CHARGEMENT DES DONNÉES ---
def load_data(mois_selectionne):
    sheet = get_sheet()
    if sheet is None: return pd.DataFrame(columns=COLS)

    try:
        data = sheet.get_all_values()
        if not data or len(data) <= 1:
            return pd.DataFrame(columns=COLS)

        # Transformation en DataFrame
        df = pd.DataFrame(data[1:], columns=COLS)
        
        # Conversion numérique
        for col in NUM_COLS:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "."), errors="coerce").fillna(0)

        # Extraction de l'année pour les archives
        parsed_dates = pd.to_datetime(df["date"], errors="coerce")
        df["annee"] = parsed_dates.dt.year.fillna(0).astype(int)

        return df[df["mois"] == mois_selectionne].reset_index(drop=True)
    except Exception as e:
        st.error(f"Erreur de lecture : {e}")
        return pd.DataFrame(columns=COLS)

# --- SUPPRESSION ---
def delete_item(item_id):
    sheet = get_sheet()
    if sheet is None: return False
    try:
        data = sheet.get_all_values()
        target = str(item_id).strip()
        for i, row in enumerate(data):
            if i > 0 and str(row[0]).strip() == target:
                sheet.delete_rows(i + 1)
                return True
        return False
    except:
        return False

# --- PDF HELPERS ---
def _safe(text):
    return str(text).encode("latin-1", "replace").decode("latin-1")

def build_receipt_pdf(row):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, _safe(SCHOOL_NAME), ln=True, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "RECU DE PAIEMENT", ln=True, align="C")
    pdf.ln(5)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, _safe(f"Eleve : {row['nom']}"), ln=True)
    pdf.cell(0, 10, _safe(f"Classe : {row['classe']}"), ln=True)
    pdf.cell(0, 10, _safe(f"Motif : {row['designation']}"), ln=True)
    montant = row["entree"] if row["entree"] > 0 else row["sortie"]
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 15, _safe(f"MONTANT : {montant:,.0f} FCFA"), ln=True)
    return bytes(pdf.output())

# --- INTERFACE ---
def main():
    if "auth" not in st.session_state:
        st.session_state.auth = False
    
    if not st.session_state.auth:
        st.title(SCHOOL_NAME)
        pwd = st.text_input("Mot de passe", type="password")
        if st.button("Connexion"):
            if pwd == st.secrets.get("MON_MOT_DE_PASSE"):
                st.session_state.auth = True
                st.rerun()
        return

    st.title(f"📂 {SCHOOL_NAME}")
    
    current_year = date.today().year
    tabs = st.tabs(MONTHS)
    
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df_all = load_data(mois)
            df = df_all[df_all["annee"] == current_year].reset_index(drop=True)
            
            # --- Résumé financier ---
            t_e = df["entree"].sum() if not df.empty else 0
            t_s = df["sortie"].sum() if not df.empty else 0
            c1, c2, c3 = st.columns(3)
            c1.metric("Entrées", f"{t_e:,.0f} FCFA")
            c2.metric("Sorties", f"{t_s:,.0f} FCFA")
            c3.metric("Solde", f"{t_e - t_s:,.0f} FCFA")

            # --- Formulaire ---
            with st.expander("➕ Nouvelle opération"):
                with st.form(f"f_{mois}", clear_on_submit=True):
                    d = st.date_input("Date", value=date.today())
                    nom = st.text_input("Nom de l'élève")
                    cl = st.text_input("Classe")
                    des = st.text_input("Désignation")
                    ent = st.number_input("Entrée", min_value=0.0)
                    sor = st.number_input("Sortie", min_value=0.0)
                    if st.form_submit_button("Enregistrer"):
                        sheet = get_sheet()
                        if sheet:
                            sheet.append_row([str(int(time.time())), mois, d.isoformat(), des, nom, cl, str(ent), str(sor)])
                            st.success("Enregistré !")
                            time.sleep(1)
                            st.rerun()

            # --- Tableau ---
            if not df.empty:
                st.dataframe(df[["date", "nom", "classe", "designation", "entree", "sortie"]], use_container_width=True)
                
                # --- Actions ---
                target_id = st.selectbox("Choisir une ligne", df["id"].tolist(), key=f"s_{mois}")
                col_a, col_b = st.columns(2)
                if col_a.button("🗑️ Supprimer", key=f"d_{mois}"):
                    if delete_item(target_id):
                        st.success("Supprimé !")
                        st.rerun()
                
                if col_b.button("📄 Reçu PDF", key=f"p_{mois}"):
                    row = df[df["id"] == target_id].iloc[0]
                    pdf_bytes = build_receipt_pdf(row)
                    st.download_button("⬇️ Télécharger", data=pdf_bytes, file_name=f"recu_{target_id}.pdf", mime="application/pdf")

            # --- Archives ---
            st.divider()
            with st.expander("🗂️ Archives des années précédentes"):
                archive_years = sorted([y for y in df_all["annee"].unique() if y > 0 and y != current_year], reverse=True)
                if archive_years:
                    sel_y = st.selectbox("Année", archive_years, key=f"ay_{mois}")
                    df_arch = df_all[df_all["annee"] == sel_y]
                    st.dataframe(df_arch[["date", "nom", "designation", "entree", "sortie"]], use_container_width=True)
                else:
                    st.info("Aucune archive.")

if __name__ == "__main__":
    main()