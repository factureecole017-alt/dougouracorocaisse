import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import date
from fpdf import FPDF
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]

# --- CONNEXION (SANS ERREUR 429) ---
def get_sheet():
    try:
        creds_dict = json.loads(st.secrets["GCP_JSON"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        # Petite pause pour éviter de saturer Google
        time.sleep(0.5) 
        return client.open("Caisse Scolaire").get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None

# --- CHARGEMENT ROBUSTE ---
def load_data(mois_cible=None):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    # On lit TOUT sans se soucier des noms de colonnes (get_all_values)
    all_values = sheet.get_all_values()
    if len(all_values) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On force nos propres colonnes sur les données (on ignore la ligne 1 d'Excel)
    df = pd.DataFrame(all_values[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Conversion forcée en nombres pour les SOMMES
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    if mois_cible:
        df = df[df["mois"] == mois_cible]
    return df

# --- ACTIONS ---
def add_row(mois, d, des, nom, cl, e, s):
    sheet = get_sheet()
    if not sheet: return
    # L'ID est basé sur l'heure pour être unique et éviter les erreurs de doublons
    new_id = int(time.time())
    sheet.append_row([str(new_id), mois, d.isoformat(), des, nom, cl, str(e), str(s)])

def delete_row(row_id):
    sheet = get_sheet()
    if not sheet: return
    data = sheet.get_all_values()
    for i, row in enumerate(data):
        if i == 0: continue
        if len(row) > 0 and str(row[0]) == str(row_id):
            sheet.delete_rows(i + 1)
            break

# --- INTERFACE ---
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
            
            # Sommes
            e_total = df["entree"].sum()
            s_total = df["sortie"].sum()
            st.subheader(f"Total {mois}: {e_total - s_total} FCFA")

            # Formulaire
            with st.expander("Ajouter une ligne"):
                with st.form(f"f_{mois}"):
                    d = st.date_input("Date", value=date.today())
                    nom = st.text_input("Nom")
                    cl = st.text_input("Classe")
                    des = st.text_input("Désignation")
                    ent = st.number_input("Entrée", min_value=0.0)
                    sor = st.number_input("Sortie", min_value=0.0)
                    if st.form_submit_button("Valider"):
                        add_row(mois, d, des, nom, cl, ent, sor)
                        st.rerun()

            if not df.empty:
                st.dataframe(df[["id", "nom", "designation", "entree", "sortie"]], hide_index=True)
                
                # Suppression & Impression
                c1, c2 = st.columns(2)
                with c1:
                    target = st.selectbox("Ligne à supprimer", df["id"].tolist(), key=f"d_{mois}")
                    if st.button("Supprimer", key=f"bd_{mois}"):
                        delete_row(target)
                        st.rerun()
                with c2:
                    target_p = st.selectbox("Ligne pour PDF", df["id"].tolist(), key=f"p_{mois}")
                    if st.button("Imprimer Reçu", key=f"bp_{mois}"):
                        row = df[df["id"] == target_p].iloc[0]
                        pdf = FPDF()
                        pdf.add_page()
                        pdf.set_font("Arial", "B", 16)
                        pdf.cell(0, 10, f"RECU: {row['nom']}", ln=True)
                        pdf.set_font("Arial", "", 12)
                        pdf.cell(0, 10, f"Montant: {row['entree'] + row['sortie']} FCFA", ln=True)
                        st.download_button("Télécharger", pdf.output(dest='S'), f"recu_{row['nom']}.pdf")
            else:
                st.write("Vide.")

if __name__ == "__main__":
    main()