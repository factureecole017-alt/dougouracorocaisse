from datetime import date
import os
import re
import sqlite3
from pathlib import Path
import unicodedata

from fpdf import FPDF
from fpdf.enums import XPos, YPos
import pandas as pd
import streamlit as st

DB_PATH = Path("caisse_scolaire.db")
LOGO_PATH = Path("logo.png")
ENV_PATH = Path(".env")
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
SCHOOL_PHONE = "Tél: 75172000"
MONTHS = [
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
]


def get_connection():
    return sqlite3.connect(DB_PATH)


def load_env_file():
    if not ENV_PATH.exists():
        return

    for line in ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mouvements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mois TEXT NOT NULL,
                date TEXT NOT NULL,
                designation TEXT NOT NULL,
                nom TEXT NOT NULL,
                classe TEXT NOT NULL,
                entree REAL NOT NULL DEFAULT 0,
                sortie REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO mouvements (mois, date, designation, nom, classe, entree, sortie)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mois,
                movement_date.isoformat(),
                designation.strip(),
                nom.strip(),
                classe.strip(),
                float(entree or 0),
                float(sortie or 0),
            ),
        )
        conn.commit()


def delete_mouvement(row_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM mouvements WHERE id = ?", (row_id,))
        conn.commit()


def load_mouvements(mois=None):
    query = "SELECT id, mois, date, designation, nom, classe, entree, sortie FROM mouvements"
    params = ()
    if mois:
        query += " WHERE mois = ?"
        params = (mois,)
    query += " ORDER BY date ASC, id ASC"

    with get_connection() as conn:
        df = pd.read_sql_query(query, conn, params=params)

    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["entree"] = df["entree"].astype(float)
    df["sortie"] = df["sortie"].astype(float)
    df["solde"] = df["entree"] - df["sortie"]
    df["solde_cumule"] = df["solde"].cumsum()
    return df


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
        if pdf.get_y() > pdf.h - 42:
            pdf.add_page()
    elif pdf.get_y() > pdf.h - 55:
        pdf.add_page()
    if not compact:
        pdf.set_y(pdf.h - 48)
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

    movement_type = "Entrée" if float(row.entree) > 0 else "Sortie"
    amount = row.entree if float(row.entree) > 0 else row.sortie
    balance = float(row.entree) - float(row.sortie)

    pdf.set_font("Helvetica", "", 12)
    fields = [
        ("N° reçu", row.id),
        ("Mois", mois),
        ("Date", row.date),
        ("Désignation", row.designation),
        ("Nom", row.nom),
        ("Classe", row.classe),
        ("Montant", money(amount)),
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
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def generate_monthly_summary_pdf(df, mois):
    pdf = FPDF(orientation="L")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    add_pdf_header(pdf, f"Résumé mensuel - {mois}")

    total_entrees = df["entree"].sum()
    total_sorties = df["sortie"].sum()
    solde_final = total_entrees - total_sorties

    pdf.set_font("Helvetica", "B", 10)
    widths = [24, 68, 42, 25, 30, 30, 30]
    headers = ["Date", "Désignation", "Nom", "Classe", "Entrée", "Sortie", "Solde"]
    for header, width in zip(headers, widths):
        pdf.cell(width, 8, clean_pdf_text(header), border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for row in df.itertuples(index=False):
        values = [
            clean_pdf_text(row.date),
            truncate_pdf_text(row.designation, 36),
            truncate_pdf_text(row.nom, 24),
            truncate_pdf_text(row.classe, 12),
            money(row.entree),
            money(row.sortie),
            money(row.solde),
        ]
        alignments = ["L", "L", "L", "L", "R", "R", "R"]
        for value, width, alignment in zip(values, widths, alignments):
            pdf.cell(width, 8, value, border=1, align=alignment)
        pdf.ln()

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, clean_pdf_text(f"Total entrées: {money(total_entrees)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, clean_pdf_text(f"Total sorties: {money(total_sorties)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, clean_pdf_text(f"Solde final: {money(solde_final)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    add_direction_signature(pdf)
    return pdf_to_bytes(pdf)


def format_table(df):
    display_df = df.rename(
        columns={
            "id": "ID",
            "mois": "Mois",
            "date": "Date",
            "designation": "Désignation",
            "nom": "Nom",
            "classe": "Classe",
            "entree": "Entrée",
            "sortie": "Sortie",
            "solde": "Solde",
            "solde_cumule": "Solde cumulé",
        }
    )
    return display_df[
        [
            "ID",
            "Date",
            "Désignation",
            "Nom",
            "Classe",
            "Entrée",
            "Sortie",
            "Solde",
            "Solde cumulé",
        ]
    ]


def check_password():
    if st.session_state.get("authenticated"):
        return True

    st.title("Connexion")
    st.write("Entrez le mot de passe pour accéder à la caisse scolaire.")

    with st.form("login-form"):
        password = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se connecter")

    if submitted:
        expected_password = os.getenv("MON_MOT_DE_PASSE")
        if not expected_password:
            st.error("Le mot de passe n'est pas configuré.")
        elif password == expected_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")

    return False


def show_logout():
    if st.sidebar.button("Se déconnecter"):
        st.session_state["authenticated"] = False
        st.rerun()


def show_month(mois):
    st.subheader(mois)

    df = load_mouvements(mois)

    st.download_button(
        "Imprimer le résumé du mois",
        data=generate_monthly_summary_pdf(df, mois) if not df.empty else b"",
        file_name=f"resume_{mois.lower()}.pdf",
        mime="application/pdf",
        disabled=df.empty,
        key=f"summary-pdf-{mois}",
    )

    with st.form(f"form-{mois}", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            movement_date = st.date_input("Date", value=date.today(), key=f"date-{mois}")
            nom = st.text_input("Nom", key=f"nom-{mois}")
        with col2:
            designation = st.text_input("Désignation", key=f"designation-{mois}")
            classe = st.text_input("Classe", key=f"classe-{mois}")
        with col3:
            entree = st.number_input("Entrée", min_value=0.0, step=100.0, format="%.2f", key=f"entree-{mois}")
            sortie = st.number_input("Sortie", min_value=0.0, step=100.0, format="%.2f", key=f"sortie-{mois}")

        submitted = st.form_submit_button("Ajouter")

    if submitted:
        if not designation.strip() or not nom.strip() or not classe.strip():
            st.error("Veuillez remplir la désignation, le nom et la classe.")
        elif entree == 0 and sortie == 0:
            st.error("Veuillez saisir une entrée ou une sortie.")
        else:
            add_mouvement(mois, movement_date, designation, nom, classe, entree, sortie)
            st.success("Ligne ajoutée.")
            st.rerun()

    if df.empty:
        st.info("Aucune donnée pour ce mois.")
        return

    total_entrees = df["entree"].sum()
    total_sorties = df["sortie"].sum()
    solde = total_entrees - total_sorties

    col1, col2, col3 = st.columns(3)
    col1.metric("Total entrées", f"{total_entrees:,.2f}".replace(",", " "))
    col2.metric("Total sorties", f"{total_sorties:,.2f}".replace(",", " "))
    col3.metric("Solde", f"{solde:,.2f}".replace(",", " "))

    st.dataframe(
        format_table(df),
        hide_index=True,
        width="stretch",
        column_config={
            "Entrée": st.column_config.NumberColumn(format="%.2f"),
            "Sortie": st.column_config.NumberColumn(format="%.2f"),
            "Solde": st.column_config.NumberColumn(format="%.2f"),
            "Solde cumulé": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    import io
    if not format_table(df).empty:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            format_table(df).to_excel(writer, index=False, sheet_name='Transactions')
        
        st.download_button(
            label="📥 Sauvegarder les données en Excel",
            data=buffer.getvalue(),
            file_name="sauvegarde_caisse_ecole.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    st.divider()
    delete_col, receipt_col = st.columns([1, 1])

    with delete_col:
        st.subheader("Supprimer une ligne")
        st.warning("Sélectionnez la ligne à supprimer, puis cliquez sur le bouton ci-dessous.")
        options = {
            f"ID {row.id} — {row.date} — {row.designation} — {row.nom}": int(row.id)
            for row in df.itertuples(index=False)
        }
        selected_label = st.selectbox("Choisir la ligne à supprimer", list(options.keys()), key=f"delete-select-{mois}")
        if st.button("Supprimer la ligne sélectionnée", key=f"delete-button-{mois}", type="primary"):
            delete_mouvement(options[selected_label])
            st.success("Ligne supprimée.")
            st.rerun()

    with receipt_col:
        st.subheader("Reçus")
        st.write("Générez un reçu PDF pour chaque ligne du tableau.")
        for row in df.itertuples(index=False):
            row_col, button_col = st.columns([2, 1])
            row_col.write(f"ID {row.id} — {row.date} — {row.nom}")
            button_col.download_button(
                "Générer Reçu",
                data=generate_receipt_pdf(row, mois),
                file_name=receipt_file_name(row),
                mime="application/pdf",
                key=f"receipt-pdf-{mois}-{row.id}",
            )


def show_global_summary():
    df = load_mouvements()
    st.sidebar.header("Résumé")
    if df.empty:
        st.sidebar.write("Aucune donnée enregistrée.")
        return

    st.sidebar.metric("Entrées totales", f"{df['entree'].sum():,.2f}".replace(",", " "))
    st.sidebar.metric("Sorties totales", f"{df['sortie'].sum():,.2f}".replace(",", " "))
    st.sidebar.metric("Solde total", f"{df['solde'].sum():,.2f}".replace(",", " "))


def main():
    st.set_page_config(page_title="Caisse scolaire", layout="wide")
    load_env_file()

    if not check_password():
        return

    init_db()

    st.title("Gestion de caisse scolaire")
    st.write("Ajoutez les entrées et sorties de caisse, mois par mois. Les données sont enregistrées dans SQLite.")

    show_logout()
    show_global_summary()

    tabs = st.tabs(MONTHS)
    for tab, mois in zip(tabs, MONTHS):
        with tab:
            show_month(mois)


if __name__ == "__main__":
    main()
