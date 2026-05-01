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

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Caisse Scolaire", layout="wide")

st.markdown(
    """
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
    """,
    unsafe_allow_html=True,
)

SCHOOL_NAME   = "Complexe Scolaire Dougouracoro Sema"
LOGO_PATH     = "logo.png"
DIRECTOR_PHONE = "+223 75172000"

MONTHS = [
    "Septembre", "Octobre", "Novembre", "Décembre",
    "Janvier", "Février", "Mars", "Avril", "Mai",
    "Juin", "Juillet", "Août",
]
MONTH_INDEX = {
    "Septembre": 9, "Octobre": 10, "Novembre": 11, "Décembre": 12,
    "Janvier": 1,  "Février": 2,  "Mars": 3,      "Avril": 4,
    "Mai": 5,      "Juin": 6,     "Juillet": 7,   "Août": 8,
}
COLS     = ["id", "mois", "date", "designation", "nom", "classe", "entree", "sortie"]
NUM_COLS = ["entree", "sortie"]

# Lignes de résumé Excel à ignorer dans les totaux
SUMMARY_RE = re.compile(r"\b(TOTAL|TOTAUX|SOLDE|REPORT|REPORTS)\b", re.IGNORECASE)

# Correction intelligente des dates : toute date antérieure à cette borne
# est considérée aberrante et sera replacée par contexte.
DATE_MIN_VALID = pd.Timestamp(year=2020, month=1, day=1)

# Fallback pour les lignes sans aucune date ni contexte (Mars 2022)
_FALLBACK = pd.Timestamp(2022, 3, 1)

# ─────────────────────────────────────────────────────────────────────────────
# CONNEXION GOOGLE SHEETS
# ─────────────────────────────────────────────────────────────────────────────
_REQUIRED_GCP_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "token_uri",
}


def _coerce_to_dict(raw):
    if raw is None:
        return None
    if hasattr(raw, "to_dict"):
        try:
            return dict(raw.to_dict())
        except Exception:
            pass
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        cleaned = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\t", "\\t")
        return json.loads(cleaned, strict=False)


def _load_gcp_credentials():
    """Priorité : st.secrets["GCP_JSON"] (json.loads) → gcp_service_account → env GCP_JSON."""
    candidates = []
    try:
        if "GCP_JSON" in st.secrets:
            candidates.append(st.secrets["GCP_JSON"])
    except Exception:
        pass
    try:
        if "gcp_service_account" in st.secrets:
            candidates.append(st.secrets["gcp_service_account"])
    except Exception:
        pass
    env_val = os.environ.get("GCP_JSON")
    if env_val:
        candidates.append(env_val)

    if not candidates:
        raise RuntimeError("Aucun secret Google Cloud trouvé. Définissez GCP_JSON.")

    last_err = None
    for raw in candidates:
        try:
            d = _coerce_to_dict(raw)
            if not isinstance(d, dict):
                raise RuntimeError(f"Type inattendu : {type(raw).__name__}")
            missing = _REQUIRED_GCP_KEYS - set(d.keys())
            if missing:
                raise RuntimeError(f"Clés manquantes : {sorted(missing)}")
            pk = d.get("private_key", "")
            if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
                d["private_key"] = pk.replace("\\n", "\n")
            return d
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Impossible de lire les identifiants Google : {last_err}")


@st.cache_resource(show_spinner=False)
def get_sheet():
    try:
        creds_dict = _load_gcp_credentials()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open("Caisse Scolaire").get_worksheet(0)
    except Exception as e:
        st.error(f"Erreur de connexion Google Sheets : {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSION NUMÉRIQUE (FCFA, espaces insécables, virgules…)
# ─────────────────────────────────────────────────────────────────────────────
def _to_number(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("\xa0", "").replace(" ", "")
    s = re.sub(r"(?i)fcfa", "", s)
    s = re.sub(r"[^\d,.\-]", "", s)
    s = s.replace(",", ".")
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(s)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CORRECTION INTELLIGENTE DES DATES ABERRANTES
# ─────────────────────────────────────────────────────────────────────────────
def _repair_dates(df):
    """Pour chaque ligne dont la date est manquante ou antérieure à 2020,
    regarde les voisines valides (au-dessus et en-dessous) et attribue
    le 1er du mois partagé par ces voisines. Fallback : 01/03/2022."""
    raw = df["date"].astype(str).str.strip()
    parsed = pd.to_datetime(raw, errors="coerce", dayfirst=True)

    # Deuxième tentative pour les formats ISO/US non reconnus en mode dayfirst
    missing = parsed.isna() & raw.ne("")
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(raw[missing], errors="coerce")

    valid = parsed.notna() & (parsed >= DATE_MIN_VALID)
    result = parsed.where(valid, other=pd.NaT)

    parsed_list = parsed.tolist()
    valid_list  = valid.tolist()
    n = len(parsed_list)

    for pos in range(n):
        if valid_list[pos]:
            continue
        prev_v = next((parsed_list[j] for j in range(pos - 1, -1, -1) if valid_list[j]), None)
        next_v = next((parsed_list[j] for j in range(pos + 1, n)      if valid_list[j]), None)

        if prev_v is not None:
            chosen = pd.Timestamp(prev_v.year, prev_v.month, 1)
        elif next_v is not None:
            chosen = pd.Timestamp(next_v.year, next_v.month, 1)
        else:
            chosen = _FALLBACK

        result.iloc[pos] = chosen

    return result


# ─────────────────────────────────────────────────────────────────────────────
# NETTOYAGE & NORMALISATION DU DATAFRAME
# ─────────────────────────────────────────────────────────────────────────────
def _is_summary(designation, nom):
    for v in (designation, nom):
        if v and SUMMARY_RE.search(str(v).strip()):
            return True
    return False


def _normalize(df):
    """Nettoie et enrichit le DataFrame :
    - conversion numérique des colonnes entrée/sortie
    - correction intelligente des dates (aberrantes ou manquantes)
    - déduction du mois depuis la date si la colonne mois est vide
    - déduplication des lignes strictement identiques
    - suppression des lignes de résumé (TOTAL, SOLDE, REPORT…)
    - suppression des lignes sans nom ET sans montant
    - tri du plus récent au plus ancien
    """
    for c in COLS:
        if c not in df.columns:
            df[c] = ""

    for col in NUM_COLS:
        df[col] = pd.to_numeric(df[col].map(_to_number), errors="coerce").fillna(0.0)

    for col in ["id", "mois", "date", "designation", "nom", "classe"]:
        df[col] = df[col].astype(str).fillna("").str.strip()

    # Dates réparées
    repaired = _repair_dates(df)
    df["date_triable"]  = repaired
    df["annee"]         = repaired.dt.year.astype(int)
    df["date_affichage"] = repaired.dt.strftime("%d/%m/%Y")

    # Mois : déduit depuis la date si absent ou non reconnu
    month_num_to_name = {v: k for k, v in MONTH_INDEX.items()}
    mois_absent = df["mois"].eq("") | df["mois"].str.lower().eq("nan") | (~df["mois"].isin(MONTHS))
    if mois_absent.any():
        df.loc[mois_absent, "mois"] = repaired[mois_absent].dt.month.map(month_num_to_name)

    # Suppression des lignes de résumé Excel (TOTAL / SOLDE / REPORT…)
    is_summ = df.apply(lambda r: _is_summary(r["designation"], r["nom"]), axis=1)
    df = df[~is_summ]

    # Suppression des lignes sans nom ET sans montant
    nom_ok     = df["nom"].str.strip().ne("")
    montant_ok = (df["entree"] > 0) | (df["sortie"] > 0)
    df = df[nom_ok | montant_ok]

    # Déduplication exacte (même date, nom, classe, désignation, montants)
    dedup_key = (
        df["date_affichage"].astype(str) + "|"
        + df["nom"].str.strip().str.lower() + "|"
        + df["classe"].str.strip().str.lower() + "|"
        + df["designation"].str.strip().str.lower() + "|"
        + df["entree"].map(lambda x: f"{x:.2f}") + "|"
        + df["sortie"].map(lambda x: f"{x:.2f}")
    )
    df = df.loc[~dedup_key.duplicated(keep="first")]

    # Tri chronologique inversé (du plus récent au plus ancien)
    df = df.sort_values("date_triable", ascending=False, kind="mergesort")

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# CHARGEMENT COMPLET DEPUIS GOOGLE SHEETS (cache)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Chargement des données…")
def load_all_data():
    """Lit TOUTES les lignes via worksheet.get_all_values() (plus fiable
    que get_all_records pour les feuilles avec en-têtes dupliqués ou manquants).
    Le résultat est mis en cache via st.cache_data pour de bonnes performances
    sur 5 900+ enregistrements.
    """
    sheet = get_sheet()
    if sheet is None:
        return pd.DataFrame(columns=COLS + ["annee", "date_triable", "date_affichage"])

    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return pd.DataFrame(columns=COLS + ["annee", "date_triable", "date_affichage"])

    if not data or len(data) <= 1:
        return pd.DataFrame(columns=COLS + ["annee", "date_triable", "date_affichage"])

    header_raw = [str(c).strip().lower() for c in data[0]]
    has_header = any(h in {c.lower() for c in COLS} for h in header_raw)
    body = data[1:] if has_header else data

    if has_header:
        col_idx = {c: None for c in COLS}
        for col in COLS:
            for i, h in enumerate(header_raw):
                if h == col:
                    col_idx[col] = i
                    break
        rows = []
        for r in body:
            r = list(r)
            rows.append([
                r[col_idx[c]] if col_idx[c] is not None and col_idx[c] < len(r) else ""
                for c in COLS
            ])
    else:
        rows = [
            (list(r) + [""] * len(COLS))[: len(COLS)]
            for r in body
        ]

    df = pd.DataFrame(rows, columns=COLS)
    if df.empty:
        return pd.DataFrame(columns=COLS + ["annee", "date_triable", "date_affichage"])

    return _normalize(df)


def _invalidate_cache():
    try:
        load_all_data.clear()
    except Exception:
        pass
    try:
        st.cache_data.clear()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# ÉCRITURE / SUPPRESSION / MISE À JOUR DANS GOOGLE SHEETS
# ─────────────────────────────────────────────────────────────────────────────
def save_entry(mois, d, nom, classe, designation, entree, sortie):
    sheet = get_sheet()
    if sheet is None:
        st.error("Connexion au Google Sheet indisponible.")
        return False
    try:
        new_id  = str(int(time.time() * 1000))
        date_str = d.isoformat() if hasattr(d, "isoformat") else str(d)
        sheet.append_row([
            new_id, str(mois or ""), date_str,
            str(designation or ""), str(nom or ""), str(classe or ""),
            str(float(entree or 0)), str(float(sortie or 0)),
        ])
        _invalidate_cache()
        return True
    except Exception as e:
        st.error(f"Erreur enregistrement : {e}")
        return False


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
        if row and str(row[0]).strip() == target:
            sheet.delete_rows(i + 1)
            _invalidate_cache()
            return True
    return False


def update_item(item_id, updates):
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
        if row and str(row[0]).strip() == target:
            sheet_row = i + 1
            row = (list(row) + [""] * len(COLS))[: len(COLS)]
            for col, val in updates.items():
                if col in COLS:
                    row[COLS.index(col)] = str(val)
            try:
                sheet.update(
                    f"A{sheet_row}:{chr(ord('A') + len(COLS) - 1)}{sheet_row}",
                    [row],
                )
                _invalidate_cache()
                return True
            except Exception as e:
                st.error(f"Erreur mise à jour : {e}")
                return False
    return False


def cleanup_empty_rows():
    """Supprime du Sheet les lignes sans nom, sans désignation et sans montant."""
    sheet = get_sheet()
    if sheet is None:
        return 0
    try:
        data = sheet.get_all_values()
    except Exception:
        return 0
    to_delete = []
    for i, row in enumerate(data):
        if i == 0:
            continue
        row = (list(row) + [""] * len(COLS))[: len(COLS)]
        nom = str(row[4]).strip()
        des = str(row[3]).strip()
        ent = _to_number(row[6])
        sor = _to_number(row[7])
        if not nom and not des and ent == 0 and sor == 0:
            to_delete.append(i + 1)
    deleted = 0
    for sheet_row in sorted(to_delete, reverse=True):
        try:
            sheet.delete_rows(sheet_row)
            deleted += 1
        except Exception:
            pass
    if deleted:
        _invalidate_cache()
    return deleted


def delete_year(annee):
    """Supprime toutes les lignes d'une année donnée."""
    sheet = get_sheet()
    if sheet is None:
        return 0
    try:
        data = sheet.get_all_values()
    except Exception:
        return 0
    to_delete = []
    for i, row in enumerate(data):
        if i == 0:
            continue
        row = (list(row) + [""] * len(COLS))[: len(COLS)]
        date_str = str(row[2]).strip()
        try:
            p = pd.to_datetime(date_str, errors="coerce", dayfirst=True)
            if pd.isna(p):
                p = pd.to_datetime(date_str, errors="coerce")
            y = int(p.year) if pd.notna(p) and p >= DATE_MIN_VALID else 2022
        except Exception:
            y = 2022
        if y == int(annee):
            to_delete.append(i + 1)
    deleted = 0
    for sheet_row in sorted(to_delete, reverse=True):
        try:
            sheet.delete_rows(sheet_row)
            deleted += 1
        except Exception:
            pass
    if deleted:
        _invalidate_cache()
    return deleted


# ─────────────────────────────────────────────────────────────────────────────
# GÉNÉRATION PDF
# ─────────────────────────────────────────────────────────────────────────────
def _safe(text):
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _fmt(n):
    return f"{float(n):,.0f} FCFA".replace(",", " ")


def build_receipt_pdf(row):
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    m = 10
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.6)
    pdf.rect(m, m, 210 - 2 * m, 297 - 2 * m)

    if os.path.exists(LOGO_PATH):
        try:
            pdf.image(LOGO_PATH, x=m + 5, y=m + 5, w=25)
        except Exception:
            pass

    pdf.set_xy(m, m + 8)
    pdf.set_font("Arial", "B", 18)
    pdf.cell(0, 10, _safe(SCHOOL_NAME), ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 5, _safe(f"Tel. Directeur : {DIRECTOR_PHONE}"), ln=True, align="C")
    pdf.ln(4)
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "RECU DE PAIEMENT", ln=True, align="C")
    y0 = pdf.get_y()
    pdf.set_line_width(0.4)
    pdf.line(m + 40, y0, 210 - m - 40, y0)
    pdf.ln(8)

    def row_detail(label, value):
        y = pdf.get_y()
        pdf.set_xy(m + 10, y)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(40, 9, _safe(label))
        pdf.set_xy(m + 55, y)
        pdf.set_font("Arial", "", 12)
        pdf.cell(0, 9, _safe(value), ln=True)

    row_detail("Recu N :", str(row.get("id", "")))
    row_detail("Date :", row.get("date_affichage", row.get("date", "")) or "Date non spécifiée")
    row_detail("Mois :", row.get("mois", ""))
    row_detail("Eleve :", row.get("nom", ""))
    row_detail("Classe :", row.get("classe", ""))
    row_detail("Motif :", row.get("designation", ""))

    pdf.ln(3)
    y1 = pdf.get_y()
    pdf.set_draw_color(150, 150, 150)
    pdf.line(m + 5, y1, 210 - m - 5, y1)
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(6)

    ent = float(row.get("entree", 0) or 0)
    sor = float(row.get("sortie", 0) or 0)
    montant = ent if ent > 0 else sor
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 14, _safe(f"MONTANT : {montant:,.0f} FCFA".replace(",", " ")), ln=True, align="C")

    sy = 297 - m - 35
    sw = 75
    sx = 210 - m - sw - 5
    pdf.set_xy(sx, sy)
    pdf.set_font("Arial", "", 11)
    pdf.cell(sw, 6, "_______________________________", ln=True, align="C")
    pdf.set_xy(sx, sy + 6)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(sw, 6, "Signature du Directeur", ln=True, align="C")

    out = pdf.output(dest="S")
    return out.encode("latin-1", "replace") if isinstance(out, str) else bytes(out)


def build_monthly_pdf(df, mois, annee):
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    m = 10
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.6)
    pdf.rect(m, m, 210 - 2 * m, 297 - 2 * m)

    if os.path.exists(LOGO_PATH):
        try:
            pdf.image(LOGO_PATH, x=m + 5, y=m + 5, w=22)
        except Exception:
            pass

    pdf.set_xy(m, m + 8)
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 9, _safe(SCHOOL_NAME), ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 5, _safe(f"Tel. Directeur : {DIRECTOR_PHONE}"), ln=True, align="C")
    pdf.ln(3)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, _safe(f"RAPPORT — {mois} {annee}"), ln=True, align="C")
    y0 = pdf.get_y()
    pdf.set_line_width(0.4)
    pdf.line(m + 30, y0, 210 - m - 30, y0)
    pdf.ln(6)

    headers = [("Date", 25), ("Eleve", 45), ("Classe", 22), ("Motif", 50), ("Entree", 22), ("Sortie", 22)]
    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(230, 230, 230)
    for h, w in headers:
        pdf.cell(w, 8, _safe(h), border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Arial", "", 9)
    total_e = total_s = 0.0
    for _, r in df.iterrows():
        pdf.cell(25, 7, _safe(r.get("date_affichage", "") or ""), border=1)
        pdf.cell(45, 7, _safe(str(r.get("nom", ""))[:25]), border=1)
        pdf.cell(22, 7, _safe(str(r.get("classe", ""))[:12]), border=1)
        pdf.cell(50, 7, _safe(str(r.get("designation", ""))[:30]), border=1)
        ent = float(r.get("entree", 0) or 0)
        sor = float(r.get("sortie", 0) or 0)
        pdf.cell(22, 7, _safe(f"{ent:,.0f}".replace(",", " ")), border=1, align="R")
        pdf.cell(22, 7, _safe(f"{sor:,.0f}".replace(",", " ")), border=1, align="R")
        pdf.ln()
        total_e += ent
        total_s += sor

    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(245, 245, 245)
    pdf.cell(142, 8, "TOTAUX", border=1, align="R", fill=True)
    pdf.cell(22, 8, _safe(f"{total_e:,.0f}".replace(",", " ")), border=1, align="R", fill=True)
    pdf.cell(22, 8, _safe(f"{total_s:,.0f}".replace(",", " ")), border=1, align="R", fill=True)
    pdf.ln(12)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 9, _safe(f"SOLDE : {total_e - total_s:,.0f} FCFA".replace(",", " ")), ln=True, align="C")
    pdf.ln(15)
    pdf.set_x(210 - m - 80)
    pdf.set_font("Arial", "", 11)
    pdf.cell(75, 6, "_______________________________", ln=True, align="C")
    pdf.set_x(210 - m - 80)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(75, 6, "Signature du Directeur", ln=True, align="C")

    out = pdf.output(dest="S")
    return out.encode("latin-1", "replace") if isinstance(out, str) else bytes(out)


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSANTS D'INTERFACE
# ─────────────────────────────────────────────────────────────────────────────
def fmt_fcfa(n):
    return f"{float(n):,.0f} FCFA".replace(",", " ")


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
            pass
        if not expected:
            expected = os.environ.get("MON_MOT_DE_PASSE")
        if pwd and expected and pwd == expected:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")


def render_dashboard(df, title, subtitle):
    st.markdown(f"### {title}")
    st.caption(subtitle)
    t_e = float(df["entree"].sum()) if not df.empty else 0.0
    t_s = float(df["sortie"].sum()) if not df.empty else 0.0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entrées", fmt_fcfa(t_e))
    c2.metric("Sorties", fmt_fcfa(t_s))
    c3.metric("Solde",   fmt_fcfa(t_e - t_s))
    c4.metric("Opérations", f"{len(df)}")
    st.divider()


def render_new_entry_form(default_mois):
    with st.expander("Ajouter une opération", expanded=False):
        with st.form("form_new_entry", clear_on_submit=True):
            fc1, fc2 = st.columns(2)
            with fc1:
                f_date   = st.date_input("Date", value=date.today())
                f_mois   = st.selectbox(
                    "Mois", MONTHS,
                    index=MONTHS.index(default_mois) if default_mois in MONTHS else 0,
                )
                f_nom    = st.text_input("Nom de l'élève")
                f_classe = st.text_input("Classe")
            with fc2:
                f_des    = st.text_input("Désignation")
                f_entree = st.number_input("Entrée (FCFA)", min_value=0.0, step=500.0)
                f_sortie = st.number_input("Sortie (FCFA)", min_value=0.0, step=500.0)
            submitted = st.form_submit_button("Enregistrer", type="primary")
            if submitted:
                if not f_nom.strip() and f_entree == 0 and f_sortie == 0:
                    st.warning("Veuillez renseigner au moins un nom ou un montant.")
                elif save_entry(f_mois, f_date, f_nom, f_classe, f_des, f_entree, f_sortie):
                    st.success(f"Opération enregistrée pour {f_mois} {f_date.year}.")
                    time.sleep(0.5)
                    st.rerun()


def render_rows_with_actions(df, mois, key_prefix):
    """Affiche chaque ligne avec Modifier / Supprimer / Reçu PDF.
    Les lignes sont triées du plus récent au plus ancien.
    Fonctionne pour tous les mois et toutes les années (courante + archives).
    """
    if df is None or df.empty:
        return

    # Tri du plus récent au plus ancien (sécurité sur les sous-DataFrames filtrés)
    if "date_triable" in df.columns:
        df = df.sort_values("date_triable", ascending=False, kind="mergesort").reset_index(drop=True)

    col_w = [1.2, 1.6, 1.0, 2.2, 1.0, 1.0, 1.6]
    header = st.columns(col_w)
    for i, h in enumerate(["Date", "Nom", "Classe", "Désignation", "Entrée", "Sortie", "Actions"]):
        header[i].markdown(f"**{h}**")
    st.markdown("---")

    for idx, (_, row) in enumerate(df.iterrows()):
        rid  = str(row.get("id", "") or "").strip()
        ukey = f"{key_prefix}_{idx}_{rid}"

        c = st.columns(col_w)
        c[0].write(row.get("date_affichage", "") or "Date non spécifiée")
        c[1].write(row.get("nom", ""))
        c[2].write(row.get("classe", ""))
        c[3].write(row.get("designation", ""))
        c[4].write(fmt_fcfa(float(row.get("entree", 0) or 0)))
        c[5].write(fmt_fcfa(float(row.get("sortie", 0) or 0)))

        a1, a2, a3 = c[6].columns(3)
        edit_key = f"edit_open_{ukey}"
        pdf_key  = f"pdf_{ukey}"

        if a1.button("Modifier", key=f"editbtn_{ukey}"):
            st.session_state[edit_key] = True

        if a2.button("Supprimer", key=f"delbtn_{ukey}"):
            if not rid:
                st.error("ID manquant — suppression impossible.")
            elif delete_item(rid):
                st.success(f"« {row.get('nom','')} » supprimé.")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("Suppression impossible (ID introuvable).")

        if a3.button("Reçu PDF", key=f"pdfbtn_{ukey}"):
            st.session_state[pdf_key] = (
                build_receipt_pdf(row),
                f"recu_{rid or idx}_{row.get('nom','')}.pdf",
            )

        if pdf_key in st.session_state:
            pb, pf = st.session_state[pdf_key]
            st.download_button(
                f"Télécharger le reçu de {row.get('nom','')}",
                data=pb, file_name=pf, mime="application/pdf",
                key=f"dl_{ukey}",
            )

        # Formulaire de modification inline
        if st.session_state.get(edit_key):
            with st.form(f"edit_form_{ukey}"):
                st.markdown(f"**Modifier — {row.get('nom','')}**")
                try:
                    d_def = pd.to_datetime(row["date"], dayfirst=True).date()
                except Exception:
                    d_def = date.today()
                e_d   = st.date_input("Date", value=d_def, key=f"ed_{ukey}")
                e_m   = st.selectbox(
                    "Mois", MONTHS,
                    index=MONTHS.index(row["mois"]) if row["mois"] in MONTHS else MONTHS.index(mois),
                    key=f"em_{ukey}",
                )
                e_nom = st.text_input("Nom", value=row.get("nom", ""), key=f"en_{ukey}")
                e_cl  = st.text_input("Classe", value=row.get("classe", ""), key=f"ec_{ukey}")
                e_des = st.text_input("Désignation", value=row.get("designation", ""), key=f"edes_{ukey}")
                e_ent = st.number_input("Entrée (FCFA)", min_value=0.0, step=500.0,
                                        value=float(row.get("entree", 0) or 0), key=f"eent_{ukey}")
                e_sor = st.number_input("Sortie (FCFA)", min_value=0.0, step=500.0,
                                        value=float(row.get("sortie", 0) or 0), key=f"esor_{ukey}")
                cs, cc = st.columns(2)
                save   = cs.form_submit_button("Enregistrer", type="primary")
                cancel = cc.form_submit_button("Annuler")
                if save:
                    ok = update_item(rid, {
                        "date": e_d.isoformat(), "mois": e_m,
                        "nom": e_nom, "classe": e_cl,
                        "designation": e_des,
                        "entree": str(e_ent), "sortie": str(e_sor),
                    })
                    if ok:
                        st.session_state[edit_key] = False
                        st.success("Ligne modifiée.")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("Modification impossible.")
                if cancel:
                    st.session_state[edit_key] = False
                    st.rerun()

        st.markdown(
            "<hr style='margin:4px 0;border:0;border-top:1px solid #eee'>",
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION PRINCIPALE
# ─────────────────────────────────────────────────────────────────────────────
def main():
    if "auth" not in st.session_state:
        st.session_state.auth = False
    if not st.session_state.auth:
        login_screen()
        return

    # En-tête
    col_logo, col_title = st.columns([1, 5])
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=110)
    with col_title:
        st.title(SCHOOL_NAME)
        st.caption("Gestion de la caisse scolaire")

    # Chargement (cache st.cache_data — un seul appel réseau)
    df_full = load_all_data()

    # Tableau de bord global
    render_dashboard(
        df_full,
        "Tableau de bord global",
        "Cumul de toutes les opérations enregistrées dans le Google Sheet",
    )

    # Formulaire de saisie (du plus récent au plus ancien après ajout)
    today_m = date.today().month
    default_mois = next((m for m in MONTHS if MONTH_INDEX.get(m) == today_m), MONTHS[0])
    render_new_entry_form(default_mois)

    # Sélecteur d'année + bouton Actualiser
    available_years = sorted(
        {int(y) for y in df_full["annee"].unique() if int(y) > 0},
        reverse=True,
    )
    if date.today().year not in available_years:
        available_years = [date.today().year] + available_years

    sel_col, act_col = st.columns([3, 1])
    with sel_col:
        sel_year = st.selectbox(
            "Choisir une année à afficher",
            options=available_years,
            index=None,
            placeholder="-- Sélectionnez une année --",
            key="global_year_select",
        )
    with act_col:
        st.write("")
        st.write("")
        if st.button("Actualiser", type="secondary", help="Recharger depuis le Google Sheet"):
            _invalidate_cache()
            # Nettoie les PDFs en session
            for k in [k for k in st.session_state if k.startswith(("pdf_", "dl_", "rep_bytes_", "year_rep_", "month_rep_"))]:
                del st.session_state[k]
            st.toast("Données rechargées.")
            st.rerun()

    if sel_year is None:
        st.info("Sélectionnez une année dans le menu ci-dessus pour afficher les opérations.")
        return

    # Données de l'année sélectionnée
    df_year = df_full[df_full["annee"] == sel_year].reset_index(drop=True)

    render_dashboard(
        df_year,
        f"Tableau de bord — {sel_year}",
        f"Cumul des opérations enregistrées en {sel_year}",
    )

    # Actions sur l'année entière
    ac1, ac2 = st.columns(2)
    with ac1:
        year_key = f"year_rep_{sel_year}"
        if st.button(f"Imprimer toute l'année {sel_year}", use_container_width=True, key=f"year_print_{sel_year}"):
            st.session_state[year_key] = (
                build_monthly_pdf(df_year, "Année complète", sel_year),
                f"rapport_annuel_{sel_year}.pdf",
            )
        if year_key in st.session_state:
            yrb, yrf = st.session_state[year_key]
            st.download_button(
                "Télécharger le rapport annuel",
                data=yrb, file_name=yrf, mime="application/pdf",
                key=f"year_dl_{sel_year}", use_container_width=True,
            )
    with ac2:
        with st.expander(f"Supprimer toute l'année {sel_year}"):
            st.warning("Cette action supprime **toutes** les opérations de cette année. Irréversible.")
            confirmed = st.checkbox(f"Je confirme la suppression de {sel_year}", key=f"del_year_chk_{sel_year}")
            if st.button(f"Supprimer définitivement {sel_year}", key=f"del_year_btn_{sel_year}",
                         type="primary", disabled=not confirmed):
                n = delete_year(sel_year)
                if n > 0:
                    st.success(f"{n} ligne(s) supprimée(s).")
                    st.session_state["global_year_select"] = None
                    time.sleep(0.8)
                    st.rerun()
                else:
                    st.info("Aucune ligne trouvée pour cette année.")

    # Outils d'administration
    with st.expander("Outils d'administration"):
        st.write("Supprime les lignes sans nom, sans désignation et sans montant.")
        if st.button("Nettoyer les lignes vides", key="clean_empty_btn"):
            n = cleanup_empty_rows()
            st.success(f"{n} ligne(s) supprimée(s).") if n > 0 else st.info("Aucune ligne vide trouvée.")
            time.sleep(0.5)
            st.rerun()

    st.divider()

    # Onglets mensuels (Septembre → Août), filtrés sur l'année sélectionnée
    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df_m = df_year[df_year["mois"] == mois].reset_index(drop=True)

            st.markdown(f"**{mois} {sel_year}**")
            t_e = float(df_m["entree"].sum()) if not df_m.empty else 0.0
            t_s = float(df_m["sortie"].sum()) if not df_m.empty else 0.0
            c1, c2, c3 = st.columns(3)
            c1.metric("Entrées", fmt_fcfa(t_e))
            c2.metric("Sorties", fmt_fcfa(t_s))
            c3.metric("Solde",   fmt_fcfa(t_e - t_s))

            if df_m.empty:
                st.info(f"Aucune opération pour {mois} {sel_year}.")
            else:
                render_rows_with_actions(df_m, mois, key_prefix=f"m_{mois}_{sel_year}")

                month_key = f"month_rep_{mois}_{sel_year}"
                if st.button(f"Imprimer {mois} {sel_year}", key=f"month_print_{mois}_{sel_year}"):
                    st.session_state[month_key] = (
                        build_monthly_pdf(df_m, mois, sel_year),
                        f"rapport_{mois}_{sel_year}.pdf",
                    )
                if month_key in st.session_state:
                    mrb, mrf = st.session_state[month_key]
                    st.download_button(
                        "Télécharger le rapport du mois",
                        data=mrb, file_name=mrf, mime="application/pdf",
                        key=f"month_dl_{mois}_{sel_year}",
                    )


if __name__ == "__main__":
    main()
