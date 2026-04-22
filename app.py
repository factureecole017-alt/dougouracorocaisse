import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import date
import time
from fpdf import FPDF

# --- CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
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
        st.error(f"Erreur Google : {e}")
        return None

# --- CHARGEMENT SANS ÉCHEC ---
def load_data(mois_selectionne):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    data = sheet.get_all_values()
    # Si le fichier est vide ou n'a que les titres
    if len(data) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On force les colonnes pour éviter le 'KeyError' de tes photos
    df = pd.DataFrame(data[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # Nettoyage des chiffres pour éviter le 'ValueError'
    for col in ["entree", "sortie"]:
        df[col] = df[col].str.replace(',', '.').str.replace(' ', '')
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    return df[df["mois"] == mois_selectionne]

# --- SUPPRESSION ---
def delete_item(item_id):
    sheet = get_sheet()
    if not sheet: return
    # On récupère tout pour être sûr de trouver la bonne ligne
    data = sheet.get_all_values()
    for i, row in enumerate(data):
        if i == 0: continue # On saute les titres
        if len(row) > 0 and str(row[0]) == str(item_id):
            sheet.delete_rows(i + 1)
            return True
    return False

# --- INTERFACE ---
def main():
    if "auth" not in st.session_state: st.session_state.auth = False
    if not st.session_state.auth:
        pwd = st.text_input("Mot de passe", type="password")
        if pwd == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state.auth = True
            st.rerun()
        return

    st.title("💰 Gestion de Caisse")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_data(mois)
            
            # Sommes automatiques
            t_entree = df["entree"].sum()
            t_sortie = df["sortie"].sum()
            st.metric(f"Solde {mois}", f"{t_entree - t_sortie} FCFA", delta=f"In: {t_entree}")

            # Formulaire
            with st.expander("➕ Nouvelle Opération"):
                with st.form(f"form_{mois}"):
                    c1, c2 = st.columns(2)
                    d = c1.date_input("Date", value=date.today())
                    nom = c2.text_input("Nom de l'élève")
                    cl = c1.text_input("Classe")
                    des = c2.text_input("Désignation")
                    ent = c1.number_input("Entrée", min_value=0.0)
                    sor = c2.number_input("Sortie", min_value=0.0)
                    
                    if st.form_submit_button("Enregistrer"):
                        sheet = get_sheet()
                        # ID unique pour ne plus jamais se tromper
                        new_id = str(int(time.time()))
                        sheet.append_row([new_id, mois, d.isoformat(), des, nom, cl, str(ent), str(sor)])
                        st.success("C'est enregistré !")
                        time.sleep(1)
                        st.rerun()

            # Tableau et Actions (Supprimer/Imprimer)
            if not df.empty:
                st.dataframe(df[["id", "date", "nom", "designation", "entree", "sortie"]], hide_index=True)
                
                st.divider()
                # On utilise un ID unique pour que le code sache exactement quoi supprimer/imprimer
                selection = st.selectbox("Sélectionner une ligne (par ID)", df["id"].tolist(), key=f"sel_{mois}")
                
                c_del, c_pdf = st.columns(2)
                
                if c_del.button("🗑️ Supprimer cette ligne", key=f"btn_del_{mois}", type="primary"):
                    if delete_item(selection):
                        st.success("Supprimé !")
                        time.sleep(1)
                        st.rerun()
                
                if c_pdf.button("📄 Imprimer le reçu", key=f"btn_pdf_{mois}"):
                    row = df[df["id"] == selection].iloc[0]
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Arial", "B", 16)
                    pdf.cell(0, 10, SCHOOL_NAME, ln=True, align='C')
                    pdf.ln(10)
                    pdf.set_font("Arial", "", 12)
                    pdf.cell(0, 10, f"RECU POUR : {row['nom']}", ln=True)
                    pdf.cell(0, 10, f"CLASSE : {row['classe']}", ln=True)
                    pdf.cell(0, 10, f"MOTIF : {row['designation']}", ln=True)
                    m = row['entree'] if row['entree'] > 0 else row['sortie']
                    pdf.set_font("Arial", "B", 14)
                    pdf.cell(0, 15, f"MONTANT : {m} FCFA", ln=True)
                    
                    # Génération sécurisée du PDF
                    pdf_output = pdf.output(dest='S').encode('latin-1', 'replace')
                    st.download_button("⬇️ Télécharger le Reçu", pdf_output, f"recu_{row['nom']}.pdf")
            else:
                st.info("Rien à afficher pour le moment.")

if __name__ == "__main__":
    main()