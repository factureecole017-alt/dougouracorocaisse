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
            </style>
            '''
st.markdown(hide_st_style, unsafe_allow_html=True)
SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
LOGO_PATH = "logo.png"
DIRECTOR_PHONE = "+223 75172000"  # <-- Modifier ici le numero du directeur
MONTHS = [
    "Septembre", "Octobre", "Novembre", "Décembre",
    "Janvier", "Février", "Mars", "Avril", "Mai",
]
COLS = ["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"]
NUM_COLS = ["entree", "sortie"]


# --- CHARGEMENT SÉCURISÉ DES SECRETS ---
def _load_gcp_credentials():
    """Charge GCP_JSON de manière blindée (gère les retours à la ligne réels)."""
    raw = None
    try:
        raw = st.secrets["GCP_JSON"]
    except Exception:
        raw = os.environ.get("GCP_JSON")

    if not raw:
        raise RuntimeError("Le secret GCP_JSON est introuvable.")

    if isinstance(raw, dict):
        creds_dict = dict(raw)
    else:
        text = str(raw).strip()
        # strict=False autorise les caractères de contrôle (vrais \n) dans les strings
        try:
            creds_dict = json.loads(text, strict=False)
        except json.JSONDecodeError:
            # Tentative de réparation : échapper les retours à la ligne bruts
            cleaned = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\t", "\\t")
            creds_dict = json.loads(cleaned, strict=False)

    pk = creds_dict.get("private_key", "")
    if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
        creds_dict["private_key"] = pk.replace("\\n", "\n")

    return creds_dict


# --- CONNEXION GOOGLE SHEETS ---
@st.cache_resource(show_spinner=False)
def get_sheet():
    try:
        creds_dict = _load_gcp_credentials()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open("Caisse Scolaire").get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur de connexion Google Sheets : {e}")
        return None


# --- CHARGEMENT BLINDÉ DES DONNÉES ---
def load_data(mois_selectionne):
    sheet = get_sheet()
    if sheet is None:
        return pd.DataFrame(columns=COLS)

    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return pd.DataFrame(columns=COLS)

    if not data or len(data) <= 1:
        return pd.DataFrame(columns=COLS)

    rows = []
    for r in data[1:]:
        # Normalise chaque ligne à exactement len(COLS) colonnes
        r = list(r) + [""] * (len(COLS) - len(r))
        rows.append(r[: len(COLS)])

    # Force les noms de colonnes (ignore complètement l'entête du Sheet)
    df = pd.DataFrame(rows, columns=COLS)

    # S'assure que toutes les colonnes existent (sécurité supplémentaire)
    for c in COLS:
        if c not in df.columns:
            df[c] = ""

    # Conversion numérique blindée
    for col in NUM_COLS:
        df[col] = (
            pd.to_numeric(
                df[col].astype(str).str.replace(",", ".").str.strip(),
                errors="coerce",
            ).fillna(0)
        )

    # Nettoyage texte
    for col in ["id", "mois", "date", "designation", "nom", "classe"]:
        df[col] = df[col].astype(str).fillna("").str.strip()

    return df[df["mois"] == mois_selectionne].reset_index(drop=True)


# --- SUPPRESSION PAR ID UNIQUE ---
def delete_item(item_id):
    sheet = get_sheet()
    if sheet is None:
        return False
    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return False

    target = str(item_id).strip()
    for i, row in enumerate(data):
        if i == 0:
            continue
        if len(row) > 0 and str(row[0]).strip() == target:
            sheet.delete_rows(i + 1)
            return True
    return False


# --- GENERATION PDF ---
def _safe(text):
    """Encode pour latin-1 (FPDF ne gere pas l\'unicode complet)."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def build_receipt_pdf(row):
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    # --- Cadre exterieur ---
    margin = 10
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.6)
    pdf.rect(margin, margin, 210 - 2 * margin, 297 - 2 * margin)

    # --- Logo ---
    if os.path.exists(LOGO_PATH):
        try:
            pdf.image(LOGO_PATH, x=margin + 5, y=margin + 5, w=25)
        except Exception:
            pass

    # --- En-tete ---
    pdf.set_xy(margin, margin + 8)
    pdf.set_font("Arial", "B", 18)
    pdf.cell(0, 10, _safe(SCHOOL_NAME), ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 5, _safe(f"Tel. Directeur : {DIRECTOR_PHONE}"), ln=True, align="C")
    pdf.ln(4)

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "RECU DE PAIEMENT", ln=True, align="C")
    # Ligne sous le titre
    y = pdf.get_y()
    pdf.set_line_width(0.4)
    pdf.line(margin + 40, y, 210 - margin - 40, y)
    pdf.ln(8)

    # --- Details (libelles a gauche, valeurs alignees) ---
    pdf.set_font("Arial", "", 12)
    label_x = margin + 10
    value_x = margin + 55
    line_h = 9

    def details(label, value):
        y0 = pdf.get_y()
        pdf.set_xy(label_x, y0)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(40, line_h, _safe(label))
        pdf.set_xy(value_x, y0)
        pdf.set_font("Arial", "", 12)
        pdf.cell(0, line_h, _safe(value), ln=True)

    details("Recu N° :", row["id"])
    details("Date :", row["date"])
    details("Mois :", row.get("mois", ""))
    details("Eleve :", row["nom"])
    details("Classe :", row["classe"])
    details("Motif :", row["designation"])

    # --- Ligne de separation ---
    pdf.ln(3)
    y = pdf.get_y()
    pdf.set_draw_color(150, 150, 150)
    pdf.line(margin + 5, y, 210 - margin - 5, y)
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(6)

    # --- Montant en gros et gras ---
    montant = row["entree"] if row["entree"] > 0 else row["sortie"]
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 14, _safe(f"MONTANT : {montant:,.0f} FCFA".replace(",", " ")), ln=True, align="C")
    pdf.ln(4)

    # --- Zone de signature en bas a droite ---
    sig_w = 75
    sig_x = 210 - margin - sig_w - 5
    sig_y = 297 - margin - 35
    pdf.set_xy(sig_x, sig_y)
    pdf.set_font("Arial", "", 11)
    pdf.cell(sig_w, 6, "_______________________________", ln=True, align="C")
    pdf.set_xy(sig_x, sig_y + 6)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(sig_w, 6, "Signature du Directeur", ln=True, align="C")

    # --- Sortie en latin-1 ---
    output = pdf.output(dest="S")
    if isinstance(output, str):
        return output.encode("latin-1", "replace")
    return bytes(output)


# --- INTERFACE ---
def login_screen():
    col_logo, col_title = st.columns([1, 4])
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=100)
    with col_title:
        st.title(SCHOOL_NAME)
    st.subheader("Connexion")

    pwd = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter", type="primary"):
        expected = None
        try:
            expected = st.secrets.get("MON_MOT_DE_PASSE")
        except Exception:
            expected = os.environ.get("MON_MOT_DE_PASSE")
        if pwd and expected and pwd == expected:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")


def main():
    if "auth" not in st.session_state:
        st.session_state.auth = False
    if not st.session_state.auth:
        login_screen()
        return

    # Header avec logo en haut à gauche
    col_logo, col_title = st.columns([1, 5])
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=110)
    with col_title:
        st.title(SCHOOL_NAME)
        st.caption("Gestion de la caisse scolaire")

    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = load_data(mois)

            t_e = float(df["entree"].sum()) if not df.empty else 0.0
            t_s = float(df["sortie"].sum()) if not df.empty else 0.0
            c1, c2, c3 = st.columns(3)
            c1.metric("Entrées", f"{t_e:,.0f} FCFA")
            c2.metric("Sorties", f"{t_s:,.0f} FCFA")
            c3.metric("Solde", f"{t_e - t_s:,.0f} FCFA")

            with st.expander("➕ Nouvelle opération"):
                with st.form(f"f_{mois}", clear_on_submit=True):
                    d = st.date_input("Date", value=date.today())
                    nom = st.text_input("Nom de l'élève")
                    cl = st.text_input("Classe")
                    des = st.text_input("Désignation")
                    ent = st.number_input("Entrée (FCFA)", min_value=0.0, step=500.0)
                    sor = st.number_input("Sortie (FCFA)", min_value=0.0, step=500.0)
                    if st.form_submit_button("Enregistrer", type="primary"):
                        sheet = get_sheet()
                        if sheet is not None:
                            new_id = str(int(time.time()))
                            try:
                                sheet.append_row([
                                    new_id, mois, d.isoformat(), des, nom, cl,
                                    str(ent), str(sor),
                                ])
                                st.success("Opération enregistrée.")
                                time.sleep(0.8)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur enregistrement : {e}")

            if df.empty:
                st.info("Aucune donnée pour ce mois.")
                continue

            st.dataframe(
                df[["date", "nom", "classe", "designation", "entree", "sortie"]],
                hide_index=True,
                use_container_width=True,
            )

            st.divider()
            ids = df["id"].tolist()
            target_id = st.selectbox(
                "Choisir une opération",
                ids,
                format_func=lambda x: (
                    f"{df.loc[df['id']==x,'nom'].values[0]} - "
                    f"{df.loc[df['id']==x,'designation'].values[0]}"
                ),
                key=f"sel_{mois}",
            )

            a, b = st.columns(2)
            if a.button("🗑️ Supprimer", key=f"del_{mois}", type="primary"):
                if delete_item(target_id):
                    st.success("Opération supprimée.")
                    time.sleep(0.8)
                    st.rerun()
                else:
                    st.error("Suppression impossible (ID introuvable).")

            pdf_key = f"pdf_bytes_{mois}_{target_id}"
            if b.button("📄 Préparer le reçu PDF", key=f"pdf_{mois}"):
                row = df[df["id"] == target_id].iloc[0]
                st.session_state[pdf_key] = (
                    build_receipt_pdf(row),
                    f"recu_{row['id']}_{row['nom']}.pdf",
                )
            if pdf_key in st.session_state:
                pdf_bytes, fname = st.session_state[pdf_key]
                st.download_button(
                    "⬇️ Télécharger le reçu",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    key=f"dl_{mois}_{target_id}",
                )


if __name__ == "__main__":
    main()
