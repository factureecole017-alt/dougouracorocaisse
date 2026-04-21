import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import os
import re
from pathlib import Path
import unicodedata
from datetime import date
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# --- CONFIGURATION INITIALE (Inchangée) ---
LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = ["Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février", "Mars", "Avril", "Mai"]
# 2. Fonction de connexion ROBUSTE
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# 1. Définition des droits d'accès
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_sheet_client():
    try:
        # CRUCIAL : On crée une COPIE pour ne pas modifier st.secrets directement
        creds_dict = dict(st.secrets["gcp_service_account"])
        
        # On nettoie la clé privée (gestion des sauts de ligne)
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

        # Connexion à Google avec la copie propre
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ Erreur de configuration : {e}")
        st.stop()

def init_db():
    try:
        client = get_sheet_client()
        # Assure-toi que le nom du fichier est EXACTEMENT celui-là dans ton Drive
        sheet = client.open("Base_Donnees_Caisse").sheet1
        return sheet
    except Exception as e:
        st.error(f"❌ Impossible d'ouvrir le Google Sheet : {e}")
        st.info("💡 Rappel : Partage ton fichier Excel avec l'adresse email de ton compte de service.")
        st.stop()

# Lancement de la base de données
sheet = init_db()

# 3. Initialisation de la base de données
def init_db():
    try:
        client = get_sheet_client()
        # Remplace bien par le nom EXACT de ton fichier Google Sheets
        sheet = client.open("Base_Donnees_Caisse").sheet1
        return sheet
    except Exception as e:
        st.error(f"Impossible d'ouvrir le Google Sheet : {e}")
        st.info("Vérifie que tu as bien partagé le fichier avec l'adresse email du compte de service.")
        st.stop()

# Appeler l'initialisation au début de ton code principal
sheet = init_db()
def init_db():
    """Vérifie si les titres de colonnes existent, sinon les crée"""
    sheet = get_sheet_client()
    headers = ["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"]
    first_row = sheet.row_values(1)
    if not first_row:
        sheet.append_row(headers)

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = get_sheet_client()
    # On génère un ID simple basé sur le nombre de lignes
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
    # On cherche la ligne qui a cet ID et on la supprime (index + 2 car index 0 et ligne 1 est le header)
    for index, row in enumerate(data):
        if int(row['id']) == row_id:
            sheet.delete_rows(index + 2)
            break

def load_mouvements(mois=None):
    sheet = get_sheet_client()
    records = sheet.get_all_records()
    df = pd.DataFrame(records)
    
    if df.empty:
        return df

    # Filtrage par mois si demandé
    if mois:
        df = df[df['mois'] == mois]

    if df.empty:
        return df

    # Conversion et calculs (identiques à ton code original)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["entree"] = df["entree"].astype(float)
    df["sortie"] = df["sortie"].astype(float)
    df["solde"] = df["entree"] - df["sortie"]
    df["solde_cumule"] = df["solde"].cumsum()
    return df

# --- FONCTIONS PDF & UTILITAIRES (Inchangées - Ton Travail) ---
def clean_pdf_text(value):
    return str(value).encode("latin-1", "replace").decode("latin-1")

def money(value):
    return f"{float(value):,.2f}".replace(",", " ")

def safe_filename_part(value):
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    return cleaned or "Eleve"

def receipt_file_name(row):
    return f"Recu_{safe_filename_part(row.nom)}.pdf"

def pdf_to_bytes(pdf):
    output = pdf.output()
    return bytes(output) if not isinstance(output, str) else output.encode("latin-1")

def add_pdf_header(pdf, title):
    if LOGO_PATH.exists():
        logo_width = 36
        pdf.image(str(LOGO_PATH), x=(pdf.w - logo_width) / 2, y=10, w=logo_width)
        pdf.set_y(48)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, clean_pdf_text(SCHOOL_NAME), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, clean_pdf_text(SCHOOL_PHONE), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, clean_pdf_text(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(5)

def add_direction_signature(pdf, compact=False):
    if compact:
        pdf.ln(6)
        if pdf.get_y() > pdf.h - 42: pdf.add_page()
    elif pdf.get_y() > pdf.h - 55: pdf.add_page()
    if not compact: pdf.set_y(pdf.h - 48)
    pdf.set_x(pdf.w - 100)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(85, 8, "Direction", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_x(pdf.w - 100)
    pdf.cell(85, 18, "", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(pdf.w - 100)
    pdf.cell(85, 8, "Signature: ______________________________", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")

def truncate_pdf_text(value, max_length):
    text = clean_pdf_text(value)
    return text if len(text) <= max_length else text[:max_length-3] + "..."

def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    add_pdf_header(pdf, "Reçu de caisse")
    amount = row.entree if float(row.entree) > 0 else row.sortie
    pdf.set_font("Helvetica", "", 12)
    fields = [("N° reçu", row.id), ("Mois", mois), ("Date", row.date), ("Désignation", row.designation), ("Nom", row.nom), ("Classe", row.classe), ("Montant", money(amount))]
    for label, value in fields:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(45, 8, clean_pdf_text(f"{label}:"), border=0)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, truncate_pdf_text(value, 85), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    add_direction_signature(pdf, compact=True)
    return pdf_to_bytes(pdf)

def generate_monthly_summary_pdf(df, mois):
    pdf = FPDF(orientation="L")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    add_pdf_header(pdf, f"Résumé mensuel - {mois}")
    total_entrees, total_sorties = df["entree"].sum(), df["sortie"].sum()
    pdf.set_font("Helvetica", "B", 10)
    widths = [24, 68, 42, 25, 30, 30, 30]
    headers = ["Date", "Désignation", "Nom", "Classe", "Entrée", "Sortie", "Solde"]
    for header, width in zip(headers, widths):
        pdf.cell(width, 8, clean_pdf_text(header), border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for row in df.itertuples(index=False):
        values = [clean_pdf_text(row.date), truncate_pdf_text(row.designation, 36), truncate_pdf_text(row.nom, 24), truncate_pdf_text(row.classe, 12), money(row.entree), money(row.sortie), money(row.solde)]
        for value, width, alignment in zip(values, widths, ["L", "L", "L", "L", "R", "R", "R"]):
            pdf.cell(width, 8, value, border=1, align=alignment)
        pdf.ln()
    pdf.ln(6); pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, clean_pdf_text(f"Total entrées: {money(total_entrees)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, clean_pdf_text(f"Total sorties: {money(total_sorties)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, clean_pdf_text(f"Solde final: {money(total_entrees - total_sorties)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    add_direction_signature(pdf)
    return pdf_to_bytes(pdf)

def format_table(df):
    display_df = df.rename(columns={"id": "ID", "mois": "Mois", "date": "Date", "designation": "Désignation", "nom": "Nom", "classe": "Classe", "entree": "Entrée", "sortie": "Sortie", "solde": "Solde", "solde_cumule": "Solde cumulé"})
    return display_df[["ID", "Date", "Désignation", "Nom", "Classe", "Entrée", "Sortie", "Solde", "Solde cumulé"]]

# --- AUTHENTIFICATION ---
def check_password():
    if st.session_state.get("authenticated"): return True
    st.title("Connexion")
    with st.form("login-form"):
        password = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se connecter")
    if submitted:
        if password == st.secrets.get("MON_MOT_DE_PASSE"):
            st.session_state["authenticated"] = True
            st.rerun()
        else: st.error("Mot de passe incorrect.")
    return False

# --- INTERFACE (Inchangée) ---
def show_month(mois):
    st.subheader(mois)
    df = load_mouvements(mois)
    st.download_button("Imprimer le résumé", data=generate_monthly_summary_pdf(df, mois) if not df.empty else b"", file_name=f"resume_{mois.lower()}.pdf", mime="application/pdf", disabled=df.empty, key=f"summary-{mois}")
    
    with st.form(f"form-{mois}", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            m_date = st.date_input("Date", value=date.today(), key=f"d-{mois}")
            nom = st.text_input("Nom", key=f"n-{mois}")
        with col2:
            desig = st.text_input("Désignation", key=f"de-{mois}")
            classe = st.text_input("Classe", key=f"cl-{mois}")
        with col3:
            entree = st.number_input("Entrée", min_value=0.0, step=100.0, format="%.2f", key=f"e-{mois}")
            sortie = st.number_input("Sortie", min_value=0.0, step=100.0, format="%.2f", key=f"s-{mois}")
        if st.form_submit_button("Ajouter"):
            if not desig.strip() or not nom.strip() or not classe.strip(): st.error("Champs obligatoires !")
            elif entree == 0 and sortie == 0: st.error("Saisissez un montant !")
            else:
                add_mouvement(mois, m_date, desig, nom, classe, entree, sortie)
                st.success("Ajouté !")
                st.rerun()

    if not df.empty:
        col1, col2, col3 = st.columns(3)
        col1.metric("Entrées", money(df["entree"].sum()))
        col2.metric("Sorties", money(df["sortie"].sum()))
        col3.metric("Solde", money(df["entree"].sum() - df["sortie"].sum()))
        st.dataframe(format_table(df), hide_index=True)
        
        st.divider()
        st.subheader("Supprimer / Reçus")
        options = {f"ID {r.id} | {r.nom}": int(r.id) for r in df.itertuples(index=False)}
        sel = st.selectbox("Sélectionner une ligne", list(options.keys()), key=f"sel-{mois}")
        col_del, col_rec = st.columns(2)
        if col_del.button("Supprimer", key=f"btn-del-{mois}", type="primary"):
            delete_mouvement(options[sel])
            st.rerun()
        for r in df.itertuples(index=False):
            if int(r.id) == options[sel]:
                col_rec.download_button("Télécharger Reçu", data=generate_receipt_pdf(r, mois), file_name=receipt_file_name(r), mime="application/pdf", key=f"rec-{r.id}")

def main():
    st.set_page_config(page_title="Caisse scolaire", layout="wide")
    if not check_password(): return
    init_db()
    st.title("Gestion de caisse scolaire")
    
    # Résumé Sidebar
    all_df = load_mouvements()
    if not all_df.empty:
        st.sidebar.metric("Solde Global", money(all_df["entree"].sum() - all_df["sortie"].sum()))
    if st.sidebar.button("Déconnexion"):
        st.session_state["authenticated"] = False
        st.rerun()

    tabs = st.tabs(MONTHS)
    for tab, mois in zip(tabs, MONTHS):
        with tab: show_month(mois)

if __name__ == "__main__":
    main()