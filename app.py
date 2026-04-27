import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import os
import re
from pathlib import Path
import unicodedata
from datetime import datetime, date
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# --- CONFIGURATION INITIALE ---
LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
# Liste complète des mois
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août"]

# --- CACHE DES DONNÉES POUR ÉVITER LES ERREURS ---
def get_sheet_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    # Utilise le secret configuré dans Streamlit
    creds = Credentials.from_service_account_info(st.secrets["GCP_JSON"], scopes=scope)
    client = gspread.authorize(creds)
    # Assure-toi que ce nom correspond exactement à ton fichier Google Sheets
    return client.open("Base_Donnees_Caisse").sheet1

def load_mouvements(mois=None):
    try:
        sheet = get_sheet_client()
        records = sheet.get_all_records()
        df = pd.DataFrame(records)
        
        if df.empty:
            return df

        # Conversion forcée en numérique pour éviter les sommes à "0"
        df['entree'] = pd.to_numeric(df['entree'], errors='coerce').fillna(0)
        df['sortie'] = pd.to_numeric(df['sortie'], errors='coerce').fillna(0)
        
        # Gestion des dates pour l'extraction de l'année (utile pour les archives)
        df['date_dt'] = pd.to_datetime(df['date'], errors='coerce')
        df['annee'] = df['date_dt'].dt.year.fillna(0).astype(int)

        # Calcul du solde par ligne pour le tableau
        df['solde'] = df['entree'] - df['sortie']
        df['solde_cumule'] = df['solde'].cumsum()

        if mois:
            df = df[df['mois'] == mois]
        
        return df
    except Exception as e:
        st.error(f"Erreur de lecture : {e}")
        return pd.DataFrame()

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = get_sheet_client()
    new_id = len(sheet.get_all_values())
    sheet.append_row([
        new_id, 
        mois, 
        movement_date.isoformat(), 
        designation.strip(), 
        nom.strip(), 
        classe.strip(), 
        float(entree or 0), 
        float(sortie or 0)
    ])

def delete_mouvement(row_id):
    sheet = get_sheet_client()
    data = sheet.get_all_records()
    for index, row in enumerate(data):
        if str(row['id']) == str(row_id):
            sheet.delete_rows(index + 2) # +2 car header + index 0
            return True
    return False

# --- FONCTIONS PDF (CORRIGÉES) ---
def clean_pdf_text(value):
    return str(value).encode("latin-1", "replace").decode("latin-1")

def money(value):
    return f"{float(value or 0):,.0f} FCFA".replace(",", " ")

def pdf_to_bytes(pdf):
    # CORRECTION CRITIQUE ICI pour éviter l'erreur TypeError
    try:
        return bytes(pdf.output())
    except:
        return pdf.output(dest='S').encode('latin-1')

def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.add_page()
    # Logo et Header
    if LOGO_PATH.exists():
        pdf.image(str(LOGO_PATH), x=87, y=10, w=36)
        pdf.ln(40)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, clean_pdf_text(SCHOOL_NAME), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, clean_pdf_text("Reçu de Paiement"), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    
    amount = row.entree if float(row.entree) > 0 else row.sortie
    fields = [("ID Reçu", row.id), ("Date", row.date), ("Nom", row.nom), ("Classe", row.classe), ("Motif", row.designation), ("Montant", money(amount))]
    
    for label, val in fields:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(40, 10, f"{label}: ", 0)
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 10, clean_pdf_text(val), 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    pdf.ln(20)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 10, f"Fait à Bamako, le {date.today()}", align="R")
    return pdf_to_bytes(pdf)

# --- INTERFACE UTILISATEUR ---
def main():
    st.set_page_config(page_title="Gestion Caisse Dougouracoro", layout="wide")
    
    # Masquer le style Streamlit pour faire pro
    hide_st_style = """
                <style>
                #MainMenu {visibility: hidden;}
                footer {visibility: hidden;}
                header {visibility: hidden;}
                </style>
                """
    st.markdown(hide_st_style, unsafe_allow_html=True)

    # Mot de passe
    if "auth" not in st.session_state:
        st.session_state.auth = False
    
    if not st.session_state.auth:
        pwd = st.text_input("Entrez le mot de passe Direction", type="password")
        if pwd == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state.auth = True
            st.rerun()
        return

    st.title(f"📂 {SCHOOL_NAME}")

    # --- 1. TABLEAU DE BORD GLOBAL (Nouveauté) ---
    df_global = load_mouvements()
    if not df_global.empty:
        t_entrees = df_global['entree'].sum()
        t_sorties = df_global['sortie'].sum()
        solde_actuel = t_entrees - t_sorties
        
        st.info("### 📊 SITUATION GÉNÉRALE DE LA CAISSE")
        c1, c2, c3 = st.columns(3)
        c1.metric("TOTAL ENTRÉES", money(t_entrees))
        c2.metric("TOTAL SORTIES", money(t_sorties))
        c3.metric("SOLDE GLOBAL", money(solde_actuel))
        st.divider()

    # --- 2. GESTION PAR MOIS ---
    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            st.subheader(f"Mouvement de {mois}")
            
            # Formulaire d'ajout
            with st.expander(f"➕ Ajouter une opération en {mois}"):
                with st.form(f"form_{mois}"):
                    col1, col2 = st.columns(2)
                    m_date = col1.date_input("Date", value=date.today())
                    m_nom = col1.text_input("Nom de l'élève")
                    m_classe = col1.text_input("Classe")
                    m_desig = col2.text_input("Désignation / Motif")
                    m_entree = col2.number_input("Entrée (Somme reçue)", min_value=0, step=500)
                    m_sortie = col2.number_input("Sortie (Dépense)", min_value=0, step=500)
                    
                    if st.form_submit_button("Enregistrer l'opération"):
                        add_mouvement(mois, m_date, m_desig, m_nom, m_classe, m_entree, m_sortie)
                        st.success("Enregistré dans le Cloud !")
                        st.rerun()

            # Affichage des données du mois
            df_mois = load_mouvements(mois)
            
            if not df_mois.empty:
                # Séparer année actuelle et archives
                annee_actuelle = date.today().year
                df_current = df_mois[df_mois['annee'] == annee_actuelle]
                df_archives = df_mois[df_mois['annee'] < annee_actuelle]

                st.write(f"**Opérations en cours ({annee_actuelle})**")
                st.dataframe(df_current[['id', 'date', 'nom', 'classe', 'designation', 'entree', 'sortie']], hide_index=True)

                # --- 3. SYSTÈME D'ARCHIVES ---
                if not df_archives.empty:
                    with st.expander("📁 Consulter les Archives (Années précédentes)"):
                        annees_dispo = sorted(df_archives['annee'].unique(), reverse=True)
                        sel_year = st.selectbox("Choisir l'année à consulter", annees_dispo, key=f"yr_{mois}")
                        
                        df_view = df_archives[df_archives['annee'] == sel_year]
                        st.write(f"Données de l'année {sel_year} :")
                        st.table(df_view[['id', 'date', 'nom', 'designation', 'entree', 'sortie']])
                        
                        # Suppression dans les archives
                        st.warning("Zone de suppression")
                        id_del = st.selectbox("ID à supprimer", df_view['id'].tolist(), key=f"del_sel_{mois}_{sel_year}")
                        if st.button("Supprimer cette ligne définitivement", key=f"btn_del_{mois}_{sel_year}"):
                            if delete_mouvement(id_del):
                                st.success("Supprimé !")
                                st.rerun()
                
                # --- 4. REÇUS ---
                st.divider()
                st.subheader("🖨️ Impression de Reçu")
                all_names = df_mois['nom'].unique()
                sel_nom = st.selectbox("Chercher un élève", all_names, key=f"print_nom_{mois}")
                
                # On prend la dernière opération de cet élève
                row_eleve = df_mois[df_mois['nom'] == sel_nom].iloc[-1]
                st.download_button(
                    label=f"Télécharger le reçu de {sel_nom}",
                    data=generate_receipt_pdf(row_eleve, mois),
                    file_name=f"Recu_{sel_nom}_{mois}.pdf",
                    mime="application/pdf",
                    key=f"dl_{row_eleve.id}"
                )
            else:
                st.info(f"Aucune donnée pour {mois}.")

if __name__ == "__main__":
    main()