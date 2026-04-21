from datetime import date
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import os
import re
import json
from pathlib import Path
import unicodedata

from fpdf import FPDF
from fpdf.enums import XPos, YPos
import streamlit as st

# --- CONFIGURATION ---
LOGO_PATH = Path("logo.png")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = [
    "Septembre", "Octobre", "Novembre", "Décembre", 
    "Janvier", "Février", "Mars", "Avril", "Mai",
]

# --- CONNEXION GOOGLE SHEETS ---
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
        st.stop()

def init_db():
    sheet = get_sheet()
    values = sheet.get_all_values()
    if not values:
        headers = ["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"]
        sheet.append_row(headers)

def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    sheet = get_sheet()
    all_values = sheet.get_all_values()
    next_id = len(all_values)
    
    sheet.append_row([
        next_id,
        mois,
        movement_date.isoformat(),
        designation.strip(),
        nom.strip(),
        classe.strip(),
        float(entree or 0),
        float(sortie or 0)
    ])

# --- LA PARTIE CORRIGÉE ---
def delete_mouvement(row_id):
    sheet = get_sheet()
    # On récupère toutes les lignes brute pour éviter l'erreur de conversion
    all_values = sheet.get_all_values()
    for i, row in enumerate(all_values):
        if i == 0: continue # Sauter l'entête
        # On compare en texte pour être sûr de trouver l'ID
        if str(row[0]) == str(row_id):
            sheet.delete_rows(i + 1)
            break

def load_mouvements(mois=None):
    sheet = get_sheet()
    records = sheet.get_all_records()
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"])
    if mois:
        df = df[df["mois"] == mois]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["entree"] = df["entree"].astype(float)
    df["sortie"] = df["sortie"].astype(float)
    df["solde"] = df["entree"] - df["sortie"]
    df["solde_cumule"] = df["solde"].cumsum()
    return df

# --- FONCTIONS PDF (Inchangées) ---
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
    if isinstance(output, str):
        return output.encode("latin-1")
    return bytes(output)

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

def generate_receipt_pdf(row, mois):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    add_pdf_header(pdf, "Reçu de caisse")
    amount = row.entree if float(row.entree) > 0 else row.sortie
    pdf.set_font("Helvetica", "", 12)
    fields = [
        ("N° reçu", row.id), ("Mois", mois), ("Date", row.date),
        ("Désignation", row.designation), ("Nom", row.nom),
        ("Classe", row.classe), ("Montant", money(amount)),
    ]
    for label, value in fields:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(45, 8, clean_pdf_text(f"{label}:"), border=0)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, truncate_pdf_text(value, 85), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    add_direction_signature(pdf, compact=True)
    return pdf_to_bytes(pdf)

def truncate_pdf_text(value, max_length):
    text = clean_pdf_text(value)
    if len(text) <= max_length: return text
    return text[: max_length - 3] + "..."

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
        values = [clean_pdf_text(row.date), truncate_pdf_text(row.designation, 36),
                  truncate_pdf_text(row.nom, 24), truncate_pdf_text(row.classe, 12),
                  money(row.entree), money(row.sortie), money(row.solde)]
        for val, w, al in zip(values, widths, ["L", "L", "L", "L", "R", "R", "R"]):
            pdf.cell(w, 8, val, border=1, align=al)
        pdf.ln()
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, clean_pdf_text(f"Total entrées: {money(total_entrees)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, clean_pdf_text(f"Total sorties: {money(total_sorties)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, clean_pdf_text(f"Solde final: {money(total_entrees - total_sorties)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    add_direction_signature(pdf)
    return pdf_to_bytes(pdf)

def format_table(df):
    display_df = df.rename(columns={"id": "ID", "mois": "Mois", "date": "Date", "designation": "Désignation",
                                    "nom": "Nom", "classe": "Classe", "entree": "Entrée", "sortie": "Sortie",
                                    "solde": "Solde", "solde_cumule": "Solde cumulé"})
    return display_df[["ID", "Date", "Désignation", "Nom", "Classe", "Entrée", "Sortie", "Solde", "Solde cumulé"]]

# --- INTERFACE ---
def check_password():
    if st.session_state.get("authenticated"): return True
    st.title("Connexion")
    with st.form("login-form"):
        password = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se connecter")
    if submitted:
        if password == st.secrets["MON_MOT_DE_PASSE"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else: st.error("Mot de passe incorrect.")
    return False

def show_month(mois):
    st.subheader(mois)
    df = load_mouvements(mois)
    st.download_button("Imprimer le résumé", data=generate_monthly_summary_pdf(df, mois) if not df.empty else b"",
                       file_name=f"resume_{mois.lower()}.pdf", mime="application/pdf", disabled=df.empty, key=f"sum-{mois}")
    with st.form(f"form-{mois}", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        m_date = c1.date_input("Date", value=date.today(), key=f"d-{mois}")
        nom = c1.text_input("Nom", key=f"n-{mois}")
        designation = c2.text_input("Désignation", key=f"des-{mois}")
        classe = c2.text_input("Classe", key=f"cl-{mois}")
        entree = c3.number_input("Entrée", min_value=0.0, step=100.0, key=f"e-{mois}")
        sortie = c3.number_input("Sortie", min_value=0.0, step=100.0, key=f"s-{mois}")
        if st.form_submit_button("Ajouter"):
            if not designation.strip() or not nom.strip() or not classe.strip(): st.error("Champs requis.")
            elif entree == 0 and sortie == 0: st.error("Saisir un montant.")
            else:
                add_mouvement(mois, m_date, designation, nom, classe, entree, sortie)
                st.rerun()
    if df.empty:
        st.info("Aucune donnée.")
        return
    st.dataframe(format_table(df), hide_index=True, width="stretch")
    st.divider()
    c_del, c_rec = st.columns(2)
    with c_del:
        st.subheader("Supprimer")
        options = {f"ID {r.id} - {r.nom}": r.id for r in df.itertuples(index=False)}
        sel = st.selectbox("Ligne", list(options.keys()), key=f"del-sel-{mois}")
        if st.button("Supprimer", key=f"del-btn-{mois}", type="primary"):
            delete_mouvement(options[sel]); st.rerun()
    with c_rec:
        st.subheader("Reçus")
        for row in df.itertuples(index=False):
            st.download_button(f"Reçu ID {row.id} - {row.nom}", data=generate_receipt_pdf(row, mois),
                               file_name=receipt_file_name(row), mime="application/pdf", key=f"rec-{mois}-{row.id}")

def main():
    st.set_page_config(page_title="Caisse scolaire", layout="wide")
    if not check_password(): return
    init_db()
    
    col_logo, col_title = st.columns([1, 8])
    if LOGO_PATH.exists():
        col_logo.image(str(LOGO_PATH), width=80)
    col_title.title("Gestion de caisse scolaire")
    
    if st.sidebar.button("Se déconnecter"):
        st.session_state["authenticated"] = False
        st.rerun()
    
    tabs = st.tabs(MONTHS)
    for tab, mois in zip(tabs, MONTHS):
        with tab: show_month(mois)

if __name__ == "__main__":
    main()