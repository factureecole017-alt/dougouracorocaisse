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
        st.error(f"Erreur Google : {e}")
        return None

# --- CHARGEMENT DES DONNÉES ---
def load_data(mois_selectionne):
    sheet = get_sheet()
    if not sheet: return pd.DataFrame()
    
    # On récupère TOUTES les lignes
    data = sheet.get_all_values()
    if len(data) <= 1:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On force nos propres noms de colonnes pour éviter le 'KeyError'
    df = pd.DataFrame(data[1:], columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    
    # On nettoie les montants (remplace le texte par 0 pour éviter le 'ValueError')
    df["entree"] = pd.to_numeric(df["entree"], errors='coerce').fillna(0)
    df["sortie"] = pd.to_numeric(df["sortie"], errors='coerce').fillna(0)
    
    # On filtre sur le mois
    return df[df["mois"] == mois_selectionne]

# --- ACTIONS ---
def delete_item(item_id):
    sheet = get_sheet()
    if not sheet: return
    data = sheet.get_all_values()
    for i, row in enumerate(data):
        if i == 0: continue
        if len(row) > 0 and str(row[0]) == str(item_id):
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

    st.title("💰 Gestion de Caisse Scolaire")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_data(mois)
            
            # 1. Résumé financier
            total_e = df["entree"].sum()
            total_s = df["sortie"].sum()
            st.info(f"**Solde {mois} : {total_e - total_s} FCFA** (Entrées: {total_e} | Sorties: {total_s})")

            # 2. Formulaire d'ajout
            with st.expander("➕ Ajouter une opération"):
                with st.form(f"form_{mois}"):
                    c1, c2 = st.columns(2)
                    d = c1.date_input("Date", value=date.today())
                    nom = c2.text_input("Nom de l'élève")
                    cl = c1.text_input("Classe")
                    des = c2.text_input("Désignation (ex: Mensualité)")
                    ent = c1.number_input("Entrée (Somme reçue)", min_value=0.0)
                    sor = c2.number_input("Sortie (Dépense)", min_value=0.0)
                    
                    if st.form_submit_button("Enregistrer dans Google Drive"):
                        sheet = get_sheet()
                        new_id = str(int(time.time())) # ID unique basé sur l'heure
                        sheet.append_row([new_id, mois, d.isoformat(), des, nom, cl, str(ent), str(sor)])
                        st.success("Enregistré !")
                        time.sleep(1)
                        st.rerun()

            # 3. Tableau et Outils
            if not df.empty:
                st.write("### Liste des opérations")
                st.dataframe(df[["id", "date", "nom", "designation", "entree", "sortie"]], hide_index=True)
                
                st.write("---")
                colA, colB = st.columns(2)
                
                with colA:
                    st.subheader("🗑️ Supprimer")
                    id_to_del = st.selectbox("Choisir l'ID", df["id"].tolist(), key=f"del_{mois}")
                    if st.button("Confirmer la suppression", key=f"bdel_{mois}", type="primary"):
                        delete_item(id_to_del)
                        st.rerun()
                
                with colB:
                    st.subheader("📄 Imprimer")
                    id_to_pdf = st.selectbox("Choisir l'élève", df["id"].tolist(), key=f"pdf_{mois}")
                    if st.button("Générer Reçu PDF", key=f"bpdf_{mois}"):
                        row = df[df["id"] == id_to_pdf].iloc[0]
                        pdf = FPDF()
                        pdf.add_page()
                        pdf.set_font("Arial", "B", 16)
                        pdf.cell(0, 10, SCHOOL_NAME, ln=True, align='C')
                        pdf.ln(10)
                        pdf.set_font("Arial", "B", 14)
                        pdf.cell(0, 10, f"RECU DE CAISSE - {mois}", ln=True, align='C')
                        pdf.ln(5)
                        pdf.set_font("Arial", "", 12)
                        pdf.cell(0, 10, f"Date: {row['date']}", ln=True)
                        pdf.cell(0, 10, f"Élève: {row['nom']} ({row['classe']})", ln=True)
                        pdf.cell(0, 10, f"Motif: {row['designation']}", ln=True)
                        montant = row['entree'] if row['entree'] > 0 else row['sortie']
                        pdf.set_font("Arial", "B", 13)
                        pdf.cell(0, 10, f"Montant: {montant} FCFA", ln=True)
                        
                        pdf_data = pdf.output(dest='S').encode('latin-1')
                        st.download_button("⬇️ Télécharger le PDF", pdf_data, f"recu_{row['nom']}.pdf", "application/pdf")
            else:
                st.info("Aucune donnée pour ce mois.")

if __name__ == "__main__":
    main()