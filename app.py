import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re
from datetime import date
import time
from fpdf import FPDF

# --- CONFIGURATION ---
st.set_page_config(page_title="Caisse scolaire", layout="wide")

hide_st_style = """
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
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

SCHOOL_NAME = "Complexe Scolaire Dougouracoro Sema"
LOGO_PATH = "logo.png"
DIRECTOR_PHONE = "+223 75172000"
MONTHS = [
    "Septembre", "Octobre", "Novembre", "Décembre",
    "Janvier", "Février", "Mars", "Avril", "Mai",
    "Juin", "Juillet", "Août",
]
COLS = ["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"]
NUM_COLS = ["entree", "sortie"]
MONTH_INDEX = {
    "Septembre": 9, "Octobre": 10, "Novembre": 11, "Décembre": 12,
    "Janvier": 1, "Février": 2, "Mars": 3, "Avril": 4, "Mai": 5,
    "Juin": 6, "Juillet": 7, "Août": 8,
}


# --- CHARGEMENT SÉCURISÉ DES SECRETS ---
REQUIRED_GCP_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "token_uri",
}


def _coerce_to_dict(raw):
    """Transforme une valeur de secret (dict, AttrDict, ou string JSON) en dict Python pur."""
    if raw is None:
        return None
    # st.secrets renvoie souvent un AttrDict (pour les tables TOML)
    if hasattr(raw, "to_dict"):
        try:
            return dict(raw.to_dict())
        except Exception:
            pass
    if isinstance(raw, dict):
        return dict(raw)
    # Sinon on suppose une string JSON
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # Tente de réparer les retours à la ligne bruts dans la private_key
        cleaned = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\t", "\\t")
        return json.loads(cleaned, strict=False)


def _load_gcp_credentials():
    """Charge les identifiants Google de manière blindée.
    Accepte deux formats :
      - st.secrets["GCP_JSON"]  (string JSON ou env var)
      - st.secrets["gcp_service_account"]  (table TOML / dict)
    """
    candidates = []

    # 1) gcp_service_account (table TOML ou JSON) — priorité
    try:
        if "gcp_service_account" in st.secrets:
            candidates.append(("gcp_service_account", st.secrets["gcp_service_account"]))
    except Exception:
        pass

    # 2) GCP_JSON (string JSON) — fallback
    try:
        if "GCP_JSON" in st.secrets:
            candidates.append(("GCP_JSON", st.secrets["GCP_JSON"]))
    except Exception:
        pass
    env_json = os.environ.get("GCP_JSON")
    if env_json:
        candidates.append(("env GCP_JSON", env_json))

    if not candidates:
        raise RuntimeError(
            "Aucun secret Google Cloud trouvé. Définissez GCP_JSON ou gcp_service_account."
        )

    last_err = None
    for source, raw in candidates:
        try:
            creds_dict = _coerce_to_dict(raw)
            if not isinstance(creds_dict, dict):
                raise RuntimeError(
                    f"Le secret {source} n'est pas un objet exploitable "
                    f"(type reçu : {type(raw).__name__})."
                )

            # Vérifie les clés essentielles avant d'utiliser .keys()
            missing = REQUIRED_GCP_KEYS - set(creds_dict.keys())
            if missing:
                raise RuntimeError(
                    f"Le secret {source} est incomplet. Clés manquantes : {sorted(missing)}"
                )

            # Répare la private_key si les retours à la ligne sont échappés
            pk = creds_dict.get("private_key", "")
            if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
                creds_dict["private_key"] = pk.replace("\\n", "\n")

            return creds_dict
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Impossible de lire les identifiants Google : {last_err}")


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


# --- CONVERSION NUMÉRIQUE BLINDÉE ---
def _to_number(val):
    """Convertit n'importe quelle valeur (string avec espaces, virgule, FCFA, etc.) en float."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        try:
            return float(val)
        except Exception:
            return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    # supprime espaces normaux, espaces insécables et tout caractère non numérique sauf , . -
    s = s.replace("\xa0", "").replace(" ", "")
    s = re.sub(r"[^\d,.\-]", "", s)
    s = s.replace(",", ".")
    # si plusieurs points, ne garde que le dernier comme séparateur décimal
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(s)
    except Exception:
        return 0.0


def _normalize_df(df):
    """Force types corrects sur toutes les colonnes.
    Gère les dates manquantes : par défaut le 1er du mois (année courante)
    et affiche 'Date non spécifiée' dans les tableaux.
    """
    for c in COLS:
        if c not in df.columns:
            df[c] = ""

    # 1) Pré-nettoyage texte (espaces, virgules, FCFA, etc.) via _to_number
    # 2) Filet de sécurité final : pd.to_numeric(errors='coerce').fillna(0)
    for col in NUM_COLS:
        cleaned = df[col].map(_to_number)
        df[col] = pd.to_numeric(cleaned, errors="coerce").fillna(0).astype(float)

    for col in ["id", "mois", "date", "designation", "nom", "classe"]:
        df[col] = df[col].astype(str).fillna("").str.strip()

    # Tentative de parsing de la date
    parsed = pd.to_datetime(df["date"], errors="coerce")

    current_year = date.today().year

    # Année : si la date est invalide/vide, on rattache à l'année courante
    # (pour que les opérations apparaissent dans la vue active du mois).
    df["annee"] = parsed.dt.year.where(parsed.notna(), current_year).astype(int)

    # Date affichée : "Date non spécifiée" si non parsable
    df["date_affichage"] = df["date"]
    df.loc[parsed.isna(), "date_affichage"] = "Date non spécifiée"

    # Date triable (pour reçus PDF et tris) : 1er du mois si manquante
    def _fallback_date(row):
        m_idx = MONTH_INDEX.get(row["mois"], 1)
        return pd.Timestamp(year=current_year, month=m_idx, day=1)

    fallback = df.apply(_fallback_date, axis=1)
    df["date_triable"] = parsed.where(parsed.notna(), fallback)
    return df


# --- CHARGEMENT BLINDÉ DE TOUTES LES DONNÉES ---
def load_all_data():
    """Lit absolument TOUTES les lignes du Google Sheet."""
    sheet = get_sheet()
    if sheet is None:
        return pd.DataFrame(columns=COLS + ["annee"])

    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return pd.DataFrame(columns=COLS + ["annee"])

    if not data or len(data) <= 1:
        return pd.DataFrame(columns=COLS + ["annee"])

    rows = []
    for r in data[1:]:
        r = list(r) + [""] * (len(COLS) - len(r))
        rows.append(r[: len(COLS)])

    df = pd.DataFrame(rows, columns=COLS)
    df = _normalize_df(df)
    return df


def load_data(mois_selectionne):
    df = load_all_data()
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


# --- MODIFICATION PAR ID UNIQUE ---
def update_item(item_id, updates):
    """Met à jour les champs d'une ligne identifiée par son id (1ère colonne).
    `updates` est un dict {nom_colonne: valeur}."""
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
            sheet_row = i + 1
            row = list(row) + [""] * (len(COLS) - len(row))
            new_row = row[: len(COLS)]
            for col, val in updates.items():
                if col in COLS:
                    new_row[COLS.index(col)] = str(val)
            try:
                sheet.update(
                    f"A{sheet_row}:{chr(ord('A') + len(COLS) - 1)}{sheet_row}",
                    [new_row],
                )
                return True
            except Exception as e:
                st.error(f"Erreur mise à jour : {e}")
                return False
    return False


# --- NETTOYAGE DES LIGNES VIDES OU TESTS ---
def cleanup_empty_rows():
    """Supprime les lignes considérées comme vides ou tests :
       - aucun nom ET aucune désignation ET montants à 0
    Retourne le nombre de lignes supprimées."""
    sheet = get_sheet()
    if sheet is None:
        return 0
    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return 0

    if not data or len(data) <= 1:
        return 0

    indices_to_delete = []
    for i, row in enumerate(data):
        if i == 0:
            continue
        row = list(row) + [""] * (len(COLS) - len(row))
        nom = str(row[4]).strip() if len(row) > 4 else ""
        des = str(row[3]).strip() if len(row) > 3 else ""
        ent = _to_number(row[6] if len(row) > 6 else 0)
        sor = _to_number(row[7] if len(row) > 7 else 0)
        if not nom and not des and ent == 0 and sor == 0:
            indices_to_delete.append(i + 1)  # +1 car gspread est 1-indexé

    # Supprime de la fin vers le début pour garder les indices valides
    deleted = 0
    for sheet_row in sorted(indices_to_delete, reverse=True):
        try:
            sheet.delete_rows(sheet_row)
            deleted += 1
        except Exception:
            pass
    return deleted


# --- NETTOYAGE FORCÉ DES LIGNES INCOMPLÈTES ---
def cleanup_incomplete_rows():
    """Nettoyage forcé : supprime toutes les lignes incomplètes
       - aucun nom, OU
       - aucun montant (entree=0 ET sortie=0), OU
       - ligne complètement vide
    Retourne le nombre de lignes supprimées."""
    sheet = get_sheet()
    if sheet is None:
        return 0
    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return 0

    if not data or len(data) <= 1:
        return 0

    indices_to_delete = []
    for i, row in enumerate(data):
        if i == 0:
            continue
        row = list(row) + [""] * (len(COLS) - len(row))
        nom = str(row[4]).strip() if len(row) > 4 else ""
        des = str(row[3]).strip() if len(row) > 3 else ""
        ent = _to_number(row[6] if len(row) > 6 else 0)
        sor = _to_number(row[7] if len(row) > 7 else 0)
        # Incomplet si : pas de nom OU pas de montant OU ligne vide
        is_empty = not nom and not des and ent == 0 and sor == 0
        no_name = not nom
        no_amount = ent == 0 and sor == 0
        if is_empty or no_name or no_amount:
            indices_to_delete.append(i + 1)

    deleted = 0
    for sheet_row in sorted(indices_to_delete, reverse=True):
        try:
            sheet.delete_rows(sheet_row)
            deleted += 1
        except Exception:
            pass
    return deleted


# --- GÉNÉRATION PDF ---
def _safe(text):
    return str(text).encode("latin-1", "replace").decode("latin-1")


def build_receipt_pdf(row):
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    margin = 10
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.6)
    pdf.rect(margin, margin, 210 - 2 * margin, 297 - 2 * margin)

    if os.path.exists(LOGO_PATH):
        try:
            pdf.image(LOGO_PATH, x=margin + 5, y=margin + 5, w=25)
        except Exception:
            pass

    pdf.set_xy(margin, margin + 8)
    pdf.set_font("Arial", "B", 18)
    pdf.cell(0, 10, _safe(SCHOOL_NAME), ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 5, _safe(f"Tel. Directeur : {DIRECTOR_PHONE}"), ln=True, align="C")
    pdf.ln(4)

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "RECU DE PAIEMENT", ln=True, align="C")
    y = pdf.get_y()
    pdf.set_line_width(0.4)
    pdf.line(margin + 40, y, 210 - margin - 40, y)
    pdf.ln(8)

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
    details("Date :", row.get("date_affichage", row.get("date", "")) or "Date non spécifiée")
    details("Mois :", row.get("mois", ""))
    details("Eleve :", row["nom"])
    details("Classe :", row["classe"])
    details("Motif :", row["designation"])

    pdf.ln(3)
    y = pdf.get_y()
    pdf.set_draw_color(150, 150, 150)
    pdf.line(margin + 5, y, 210 - margin - 5, y)
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(6)

    montant = row["entree"] if float(row["entree"]) > 0 else float(row["sortie"])
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 14, _safe(f"MONTANT : {montant:,.0f} FCFA".replace(",", " ")), ln=True, align="C")
    pdf.ln(4)

    sig_w = 75
    sig_x = 210 - margin - sig_w - 5
    sig_y = 297 - margin - 35
    pdf.set_xy(sig_x, sig_y)
    pdf.set_font("Arial", "", 11)
    pdf.cell(sig_w, 6, "_______________________________", ln=True, align="C")
    pdf.set_xy(sig_x, sig_y + 6)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(sig_w, 6, "Signature du Directeur", ln=True, align="C")

    output = pdf.output(dest="S")
    if isinstance(output, str):
        return output.encode("latin-1", "replace")
    return bytes(output)


def build_annual_report_pdf(df_year, mois, annee):
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    margin = 10
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.6)
    pdf.rect(margin, margin, 210 - 2 * margin, 297 - 2 * margin)

    if os.path.exists(LOGO_PATH):
        try:
            pdf.image(LOGO_PATH, x=margin + 5, y=margin + 5, w=22)
        except Exception:
            pass

    pdf.set_xy(margin, margin + 8)
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 9, _safe(SCHOOL_NAME), ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 5, _safe(f"Tel. Directeur : {DIRECTOR_PHONE}"), ln=True, align="C")
    pdf.ln(3)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, _safe(f"RAPPORT ANNUEL - {mois} {annee}"), ln=True, align="C")
    y = pdf.get_y()
    pdf.set_line_width(0.4)
    pdf.line(margin + 30, y, 210 - margin - 30, y)
    pdf.ln(6)

    headers = [("Date", 25), ("Eleve", 45), ("Classe", 22), ("Motif", 50), ("Entree", 22), ("Sortie", 22)]
    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(230, 230, 230)
    for h, w in headers:
        pdf.cell(w, 8, _safe(h), border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Arial", "", 9)
    total_e = 0.0
    total_s = 0.0
    for _, r in df_year.iterrows():
        pdf.cell(25, 7, _safe(r.get("date_affichage", r.get("date", "")) or "Date non spécifiée"), border=1)
        pdf.cell(45, 7, _safe(r["nom"])[:25], border=1)
        pdf.cell(22, 7, _safe(r["classe"])[:12], border=1)
        pdf.cell(50, 7, _safe(r["designation"])[:30], border=1)
        pdf.cell(22, 7, _safe(f"{float(r['entree']):,.0f}".replace(",", " ")), border=1, align="R")
        pdf.cell(22, 7, _safe(f"{float(r['sortie']):,.0f}".replace(",", " ")), border=1, align="R")
        pdf.ln()
        total_e += float(r["entree"])
        total_s += float(r["sortie"])

    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(245, 245, 245)
    pdf.cell(142, 8, "TOTAUX", border=1, align="R", fill=True)
    pdf.cell(22, 8, _safe(f"{total_e:,.0f}".replace(",", " ")), border=1, align="R", fill=True)
    pdf.cell(22, 8, _safe(f"{total_s:,.0f}".replace(",", " ")), border=1, align="R", fill=True)
    pdf.ln(12)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 9, _safe(f"SOLDE : {total_e - total_s:,.0f} FCFA".replace(",", " ")), ln=True, align="C")

    pdf.ln(15)
    pdf.set_x(210 - margin - 80)
    pdf.set_font("Arial", "", 11)
    pdf.cell(75, 6, "_______________________________", ln=True, align="C")
    pdf.set_x(210 - margin - 80)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(75, 6, "Signature du Directeur", ln=True, align="C")

    output = pdf.output(dest="S")
    if isinstance(output, str):
        return output.encode("latin-1", "replace")
    return bytes(output)


def fmt_fcfa(n):
    return f"{float(n):,.0f} FCFA".replace(",", " ")


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


def render_global_dashboard(df_full):
    """Tableau de bord global : somme TOTALE de TOUT le fichier (tous mois, toutes années)."""
    st.markdown("### Tableau de bord global")
    st.caption("Cumul de toutes les opérations enregistrées dans le Google Sheet")

    if df_full.empty:
        t_e = t_s = 0.0
        nb = 0
    else:
        t_e = float(df_full["entree"].sum())
        t_s = float(df_full["sortie"].sum())
        nb = len(df_full)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Entrées", fmt_fcfa(t_e))
    c2.metric("Total Sorties", fmt_fcfa(t_s))
    c3.metric("Solde Global", fmt_fcfa(t_e - t_s))
    c4.metric("Opérations", f"{nb}")

    st.divider()


def main():
    if "auth" not in st.session_state:
        st.session_state.auth = False
    if not st.session_state.auth:
        login_screen()
        return

    # --- Header avec logo ---
    col_logo, col_title = st.columns([1, 5])
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=110)
    with col_title:
        st.title(SCHOOL_NAME)
        st.caption("Gestion de la caisse scolaire")

    # --- Charge TOUTES les données une seule fois ---
    df_full = load_all_data()

    # --- Tableau de bord global EN HAUT, avant les onglets ---
    render_global_dashboard(df_full)

    # --- Outils d'administration (nettoyage) ---
    with st.expander("⚙️ Outils d'administration"):
        st.write(
            "Le nettoyage supprime définitivement les lignes considérées comme vides "
            "(sans nom, sans désignation et avec des montants à zéro)."
        )
        if st.button("🧹 Nettoyer les lignes vides / tests", type="secondary"):
            n = cleanup_empty_rows()
            if n > 0:
                st.success(f"{n} ligne(s) supprimée(s).")
            else:
                st.info("Aucune ligne vide à supprimer.")
            time.sleep(0.8)
            st.rerun()

        st.markdown("---")
        st.write(
            "**Nettoyage forcé** : supprime aussi toutes les lignes incomplètes "
            "(pas de nom OU pas de montant). À utiliser avec précaution."
        )
        force_confirm = st.checkbox(
            "Je confirme vouloir lancer le nettoyage forcé",
            key="force_cleanup_confirm",
        )
        if st.button(
            "🔥 Nettoyage Forcé (lignes incomplètes)",
            type="primary",
            disabled=not force_confirm,
        ):
            n = cleanup_incomplete_rows()
            if n > 0:
                st.success(f"{n} ligne(s) incomplète(s) supprimée(s).")
            else:
                st.info("Aucune ligne incomplète à supprimer.")
            time.sleep(0.8)
            st.rerun()

    current_year = date.today().year
    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df_all = df_full[df_full["mois"] == mois].reset_index(drop=True)
            df = df_all[df_all["annee"] == current_year].reset_index(drop=True)

            st.markdown(f"**Année en cours : {current_year}**")
            t_e = float(df["entree"].sum()) if not df.empty else 0.0
            t_s = float(df["sortie"].sum()) if not df.empty else 0.0
            c1, c2, c3 = st.columns(3)
            c1.metric("Entrées", fmt_fcfa(t_e))
            c2.metric("Sorties", fmt_fcfa(t_s))
            c3.metric("Solde", fmt_fcfa(t_e - t_s))

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
                st.info(f"Aucune donnée pour {mois} {current_year}.")
            else:
                df_disp = df.copy()
                df_disp["date"] = df_disp["date_affichage"]
                st.dataframe(
                    df_disp[["date", "nom", "classe", "designation", "entree", "sortie"]],
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

                a, b, c = st.columns(3)
                if a.button("🗑️ Supprimer", key=f"del_{mois}", type="primary"):
                    if delete_item(target_id):
                        st.success("Opération supprimée.")
                        time.sleep(0.8)
                        st.rerun()
                    else:
                        st.error("Suppression impossible (ID introuvable).")

                edit_key = f"edit_open_{mois}_{target_id}"
                if b.button("✏️ Modifier", key=f"editbtn_{mois}_{target_id}"):
                    st.session_state[edit_key] = True

                pdf_key = f"pdf_bytes_{mois}_{target_id}"
                if c.button("📄 Préparer le reçu PDF", key=f"pdf_{mois}"):
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

                # --- Formulaire de modification ---
                if st.session_state.get(edit_key):
                    row = df[df["id"] == target_id].iloc[0]
                    with st.form(f"edit_form_{mois}_{target_id}"):
                        st.markdown("**Modifier l'opération**")
                        try:
                            d_default = pd.to_datetime(row["date"]).date()
                        except Exception:
                            d_default = date.today()
                        e_d = st.date_input("Date", value=d_default, key=f"e_d_{target_id}")
                        e_mois = st.selectbox(
                            "Mois", MONTHS,
                            index=MONTHS.index(row["mois"]) if row["mois"] in MONTHS else MONTHS.index(mois),
                            key=f"e_m_{target_id}",
                        )
                        e_nom = st.text_input("Nom de l'élève", value=row["nom"], key=f"e_n_{target_id}")
                        e_cl = st.text_input("Classe", value=row["classe"], key=f"e_c_{target_id}")
                        e_des = st.text_input("Désignation", value=row["designation"], key=f"e_des_{target_id}")
                        e_ent = st.number_input(
                            "Entrée (FCFA)", min_value=0.0, step=500.0,
                            value=float(row["entree"]), key=f"e_ent_{target_id}",
                        )
                        e_sor = st.number_input(
                            "Sortie (FCFA)", min_value=0.0, step=500.0,
                            value=float(row["sortie"]), key=f"e_sor_{target_id}",
                        )
                        col_s, col_c = st.columns(2)
                        save = col_s.form_submit_button("💾 Enregistrer", type="primary")
                        cancel = col_c.form_submit_button("Annuler")
                        if save:
                            ok = update_item(target_id, {
                                "date": e_d.isoformat(),
                                "mois": e_mois,
                                "nom": e_nom,
                                "classe": e_cl,
                                "designation": e_des,
                                "entree": str(e_ent),
                                "sortie": str(e_sor),
                            })
                            if ok:
                                st.session_state[edit_key] = False
                                st.success("Opération modifiée.")
                                time.sleep(0.8)
                                st.rerun()
                            else:
                                st.error("Modification impossible (ID introuvable).")
                        if cancel:
                            st.session_state[edit_key] = False
                            st.rerun()

            # --- Archives des années précédentes ---
            st.divider()
            archive_years = sorted(
                [int(y) for y in df_all["annee"].unique() if int(y) > 0 and int(y) != current_year],
                reverse=True,
            )
            with st.expander("🗂️ Archives des années précédentes"):
                if not archive_years:
                    st.info("Aucune archive disponible pour ce mois.")
                else:
                    sel_year = st.selectbox(
                        "Choisir une année",
                        archive_years,
                        key=f"select_annee_{mois}",
                    )

                    df_archive = df_all[df_all["annee"] == sel_year].copy().reset_index(drop=True)

                    a_e = float(df_archive["entree"].sum())
                    a_s = float(df_archive["sortie"].sum())
                    ac1, ac2, ac3 = st.columns(3)
                    ac1.metric("Entrées", fmt_fcfa(a_e))
                    ac2.metric("Sorties", fmt_fcfa(a_s))
                    ac3.metric("Solde", fmt_fcfa(a_e - a_s))

                    df_arc_disp = df_archive.copy()
                    df_arc_disp["date"] = df_arc_disp["date_affichage"]
                    st.dataframe(
                        df_arc_disp[["date", "nom", "classe", "designation", "entree", "sortie"]],
                        hide_index=True,
                        use_container_width=True,
                        key=f"table_archive_{mois}_{sel_year}",
                    )

                    # --- SUPPRESSION + MODIFICATION dans les archives ---
                    if not df_archive.empty:
                        st.markdown("**Gérer une opération archivée :**")
                        arc_ids = df_archive["id"].tolist()
                        arc_target = st.selectbox(
                            "Choisir l'opération",
                            arc_ids,
                            format_func=lambda x: (
                                f"{df_archive.loc[df_archive['id']==x,'date_affichage'].values[0]} - "
                                f"{df_archive.loc[df_archive['id']==x,'nom'].values[0]} - "
                                f"{df_archive.loc[df_archive['id']==x,'designation'].values[0]}"
                            ),
                            key=f"arc_sel_{mois}_{sel_year}",
                        )

                        arc_a, arc_b, arc_c = st.columns(3)
                        arc_edit_key = f"arc_edit_open_{mois}_{sel_year}_{arc_target}"
                        arc_pdf_key2 = f"arc_pdf2_{mois}_{sel_year}_{arc_target}"

                        if arc_b.button("✏️ Modifier", key=f"arc_editbtn_{mois}_{sel_year}_{arc_target}"):
                            st.session_state[arc_edit_key] = True

                        if arc_c.button("📄 Préparer le reçu", key=f"arc_pdfbtn2_{mois}_{sel_year}_{arc_target}"):
                            arow = df_archive[df_archive["id"] == arc_target].iloc[0]
                            st.session_state[arc_pdf_key2] = (
                                build_receipt_pdf(arow),
                                f"recu_{arow['id']}_{arow['nom']}.pdf",
                            )
                        if arc_pdf_key2 in st.session_state:
                            apb, apf = st.session_state[arc_pdf_key2]
                            st.download_button(
                                "⬇️ Télécharger le reçu",
                                data=apb,
                                file_name=apf,
                                mime="application/pdf",
                                key=f"arc_dl2_{mois}_{sel_year}_{arc_target}",
                            )

                        confirm_key = f"arc_del_confirm_{mois}_{sel_year}"
                        confirm = st.checkbox(
                            "Je confirme vouloir supprimer définitivement cette ligne",
                            key=confirm_key,
                        )
                        if arc_a.button(
                            "🗑️ Supprimer définitivement",
                            key=f"arc_del_btn_{mois}_{sel_year}",
                            type="primary",
                            disabled=not confirm,
                        ):
                            if delete_item(arc_target):
                                st.success("Opération archivée supprimée.")
                                time.sleep(0.8)
                                st.rerun()
                            else:
                                st.error("Suppression impossible (ID introuvable).")

                        # --- Formulaire de modification (archives) ---
                        if st.session_state.get(arc_edit_key):
                            arow = df_archive[df_archive["id"] == arc_target].iloc[0]
                            with st.form(f"arc_edit_form_{mois}_{sel_year}_{arc_target}"):
                                st.markdown("**Modifier l'opération archivée**")
                                try:
                                    ad_default = pd.to_datetime(arow["date"]).date()
                                except Exception:
                                    ad_default = date.today()
                                ae_d = st.date_input("Date", value=ad_default, key=f"ae_d_{arc_target}")
                                ae_mois = st.selectbox(
                                    "Mois", MONTHS,
                                    index=MONTHS.index(arow["mois"]) if arow["mois"] in MONTHS else MONTHS.index(mois),
                                    key=f"ae_m_{arc_target}",
                                )
                                ae_nom = st.text_input("Nom de l'élève", value=arow["nom"], key=f"ae_n_{arc_target}")
                                ae_cl = st.text_input("Classe", value=arow["classe"], key=f"ae_c_{arc_target}")
                                ae_des = st.text_input("Désignation", value=arow["designation"], key=f"ae_des_{arc_target}")
                                ae_ent = st.number_input(
                                    "Entrée (FCFA)", min_value=0.0, step=500.0,
                                    value=float(arow["entree"]), key=f"ae_ent_{arc_target}",
                                )
                                ae_sor = st.number_input(
                                    "Sortie (FCFA)", min_value=0.0, step=500.0,
                                    value=float(arow["sortie"]), key=f"ae_sor_{arc_target}",
                                )
                                cs, cc = st.columns(2)
                                asave = cs.form_submit_button("💾 Enregistrer", type="primary")
                                acancel = cc.form_submit_button("Annuler")
                                if asave:
                                    ok = update_item(arc_target, {
                                        "date": ae_d.isoformat(),
                                        "mois": ae_mois,
                                        "nom": ae_nom,
                                        "classe": ae_cl,
                                        "designation": ae_des,
                                        "entree": str(ae_ent),
                                        "sortie": str(ae_sor),
                                    })
                                    if ok:
                                        st.session_state[arc_edit_key] = False
                                        st.success("Opération modifiée.")
                                        time.sleep(0.8)
                                        st.rerun()
                                    else:
                                        st.error("Modification impossible (ID introuvable).")
                                if acancel:
                                    st.session_state[arc_edit_key] = False
                                    st.rerun()

                    # Rapport annuel du mois
                    rep_key = f"rep_bytes_{mois}_{sel_year}"
                    if st.button("📊 Imprimer le rapport annuel", key=f"rep_{mois}_{sel_year}"):
                        st.session_state[rep_key] = (
                            build_annual_report_pdf(df_archive, mois, sel_year),
                            f"rapport_{mois}_{sel_year}.pdf",
                        )
                    if rep_key in st.session_state:
                        rb, rf = st.session_state[rep_key]
                        st.download_button(
                            "⬇️ Télécharger le rapport",
                            data=rb,
                            file_name=rf,
                            mime="application/pdf",
                            key=f"dlrep_{mois}_{sel_year}",
                        )

                    # Reçu individuel dans les archives
                    if not df_archive.empty:
                        st.markdown("**Reçu individuel :**")
                        noms = sorted(df_archive["nom"].unique().tolist())
                        nom_choisi = st.selectbox(
                            "Choisir un élève",
                            noms,
                            key=f"select_eleve_{sel_year}_{mois}",
                        )
                        df_eleve = df_archive[df_archive["nom"] == nom_choisi].reset_index(drop=True)
                        op_ids = df_eleve["id"].tolist()
                        if op_ids:
                            arc_target = st.selectbox(
                                "Choisir l'opération",
                                op_ids,
                                format_func=lambda x: (
                                    f"{df_eleve.loc[df_eleve['id']==x,'date_affichage'].values[0]} - "
                                    f"{df_eleve.loc[df_eleve['id']==x,'designation'].values[0]}"
                                ),
                                key=f"select_op_{sel_year}_{mois}",
                            )
                            arc_pdf_key = f"arc_pdf_{mois}_{sel_year}_{arc_target}"
                            if st.button("📄 Préparer le reçu", key=f"arc_pdfbtn_{mois}_{sel_year}_{arc_target}"):
                                arow = df_eleve[df_eleve["id"] == arc_target].iloc[0]
                                st.session_state[arc_pdf_key] = (
                                    build_receipt_pdf(arow),
                                    f"recu_{arow['id']}_{arow['nom']}.pdf",
                                )
                            if arc_pdf_key in st.session_state:
                                apb, apf = st.session_state[arc_pdf_key]
                                st.download_button(
                                    "⬇️ Télécharger le reçu",
                                    data=apb,
                                    file_name=apf,
                                    mime="application/pdf",
                                    key=f"arc_dl_{mois}_{sel_year}_{arc_target}",
                                )


if __name__ == "__main__":
    main()
