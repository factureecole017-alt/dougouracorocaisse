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

# Mots-clés indiquant des lignes de résumé Excel à ignorer (Fix 2)
SUMMARY_KEYWORDS = re.compile(r"\b(TOTAL|TOTAUX|SOLDE|REPORT|REPORTS)\b", re.IGNORECASE)

# Date par défaut pour les lignes sans date (Fix 5 - Récupération Mars 2022)
DEFAULT_DATE_YEAR = 2022
DEFAULT_DATE_MONTH = 3
DEFAULT_DATE_DAY = 1
DEFAULT_MONTH_NAME = "Mars"

# Toute date strictement antérieure est considérée aberrante (ex : 1900, 202)
# et déclenche la correction intelligente par contexte (Fix 1).
DATE_MIN_VALID = pd.Timestamp(year=2020, month=1, day=1)

# Totaux attendus issus de la ligne rouge de l'Excel client (Fix 2).
EXPECTED_ENTREES = 66_197_700
EXPECTED_SORTIES = 66_096_315
EXPECTED_SOLDE = EXPECTED_ENTREES - EXPECTED_SORTIES  # 101 385


# --- CHARGEMENT SÉCURISÉ DES SECRETS ---
REQUIRED_GCP_KEYS = {
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "token_uri",
}


def _coerce_to_dict(raw):
    """Transforme une valeur de secret (dict, AttrDict, ou string JSON) en dict Python pur."""
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
    """Charge les identifiants Google de manière blindée.
    Garde la logique json.loads(st.secrets["GCP_JSON"]) pour éviter l'erreur 'keys'.
    Accepte trois sources :
      - st.secrets["GCP_JSON"]            (string JSON — priorité demandée)
      - st.secrets["gcp_service_account"] (table TOML / dict)
      - variable d'environnement GCP_JSON (string JSON)
    """
    candidates = []

    # 1) GCP_JSON dans les secrets Streamlit — PRIORITÉ (logique demandée)
    try:
        if "GCP_JSON" in st.secrets:
            candidates.append(("GCP_JSON", st.secrets["GCP_JSON"]))
    except Exception:
        pass

    # 2) gcp_service_account (table TOML) — fallback
    try:
        if "gcp_service_account" in st.secrets:
            candidates.append(("gcp_service_account", st.secrets["gcp_service_account"]))
    except Exception:
        pass

    # 3) Variable d'environnement GCP_JSON — fallback final
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
    """Convertit n'importe quelle valeur (string avec espaces, virgule, FCFA, etc.) en float.
    Fix 2 : nettoie tout texte (FCFA, espaces insécables, etc.)."""
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
    # supprime espaces normaux, espaces insécables, FCFA, et tout caractère non numérique sauf , . -
    s = s.replace("\xa0", "").replace(" ", "")
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


def _intelligent_date_repair(df):
    """Fix 1 : Correction intelligente des dates.

    Une date est dite « aberrante » si :
      - elle est vide, ou
      - elle ne se parse pas, ou
      - elle est antérieure à 2020 (ex : '1900', '202' tapés à la place de '2022').

    Pour chaque ligne aberrante, on regarde la dernière date VALIDE au-dessus
    et la première date VALIDE en-dessous (dans l'ordre du Sheet) :
      - si les deux voisines partagent le même (année, mois) → on attribue
        le 01 de ce mois (ex : encadrée par deux opérations de Septembre 2022
        → '01/09/2022').
      - sinon, on prend le 01 du mois de la voisine la plus proche au-dessus
        (continuité chronologique avec la dernière opération connue).
      - si aucune voisine n'est valide → fallback Mars 2022 (Fix 5).

    Retourne la série pandas des dates réparées + le nombre de réparations.
    """
    raw_dates = df["date"].astype(str).str.strip()

    parsed = pd.to_datetime(raw_dates, errors="coerce", dayfirst=True)
    mask_unparsed = parsed.isna() & raw_dates.ne("")
    if mask_unparsed.any():
        parsed_iso = pd.to_datetime(raw_dates[mask_unparsed], errors="coerce")
        parsed.loc[mask_unparsed] = parsed_iso

    is_valid = parsed.notna() & (parsed >= DATE_MIN_VALID)

    fallback_default = pd.Timestamp(
        DEFAULT_DATE_YEAR, DEFAULT_DATE_MONTH, DEFAULT_DATE_DAY
    )

    repaired = parsed.where(is_valid)
    nb_repaired = 0
    aberrant_records = []   # pour le panneau Diagnostic

    parsed_list = parsed.tolist()
    valid_list = is_valid.tolist()
    raw_list = raw_dates.tolist()
    n = len(parsed_list)

    for pos in range(n):
        if valid_list[pos]:
            continue

        prev_valid = None
        for j in range(pos - 1, -1, -1):
            if valid_list[j]:
                prev_valid = parsed_list[j]
                break

        next_valid = None
        for j in range(pos + 1, n):
            if valid_list[j]:
                next_valid = parsed_list[j]
                break

        if prev_valid is not None and next_valid is not None:
            if (prev_valid.year == next_valid.year
                    and prev_valid.month == next_valid.month):
                chosen = pd.Timestamp(prev_valid.year, prev_valid.month, 1)
            else:
                chosen = pd.Timestamp(prev_valid.year, prev_valid.month, 1)
        elif prev_valid is not None:
            chosen = pd.Timestamp(prev_valid.year, prev_valid.month, 1)
        elif next_valid is not None:
            chosen = pd.Timestamp(next_valid.year, next_valid.month, 1)
        else:
            chosen = fallback_default

        repaired.iloc[pos] = chosen
        nb_repaired += 1

        aberrant_records.append({
            "pos": pos,
            "date_originale": raw_list[pos] if raw_list[pos] else "(vide)",
            "date_corrigee": chosen.strftime("%d/%m/%Y"),
        })

    return repaired, nb_repaired, aberrant_records


def _normalize_df(df):
    """Force les types corrects sur toutes les colonnes et applique
    la correction intelligente des dates (Fix 1) ainsi que la
    récupération des lignes Mars 2022 sans date (Fix 5).
    """
    for c in COLS:
        if c not in df.columns:
            df[c] = ""

    # Fix 2 : Nettoyage numérique blindé (FCFA, espaces, virgules)
    for col in NUM_COLS:
        cleaned = df[col].map(_to_number)
        df[col] = pd.to_numeric(cleaned, errors="coerce").fillna(0).astype(float)

    for col in ["id", "mois", "date", "designation", "nom", "classe"]:
        df[col] = df[col].astype(str).fillna("").str.strip()

    # Fix 1 + Fix 5 : Correction intelligente des dates aberrantes / manquantes
    repaired, nb_repaired, aberrant_records = _intelligent_date_repair(df)
    df.attrs["nb_dates_repaired"] = int(nb_repaired)
    df.attrs["aberrant_records"] = aberrant_records   # pour Diagnostic

    df["date_triable"] = repaired
    df["annee"] = repaired.dt.year.astype(int)
    df["date_affichage"] = repaired.dt.strftime("%d/%m/%Y")

    # Aligne la colonne « mois » sur la date réparée si elle est vide
    # ou si elle ne correspond pas à un mois connu.
    mois_vide = (
        df["mois"].str.strip().eq("")
        | df["mois"].str.strip().str.lower().eq("nan")
        | (~df["mois"].isin(MONTHS))
    )
    if mois_vide.any():
        month_num_to_name = {v: k for k, v in MONTH_INDEX.items()}
        df.loc[mois_vide, "mois"] = (
            repaired[mois_vide].dt.month.map(month_num_to_name)
        )

    return df


# --- FILTRAGE DES LIGNES DE RÉSUMÉ EXCEL (Fix 2) ---
def _is_summary_row(designation, nom):
    """Détecte les lignes contenant TOTAL / TOTAUX / SOLDE / REPORT
    dans la colonne Désignation ou Nom (lignes Excel à ignorer pour les totaux)."""
    for val in (designation, nom):
        if val is None:
            continue
        s = str(val).strip()
        if not s:
            continue
        if SUMMARY_KEYWORDS.search(s):
            return True
    return False


def _apply_strict_filter(df):
    """Filtre les lignes (Fix 2) :
      - ignore toute ligne 'TOTAL', 'TOTAUX', 'SOLDE', 'REPORT' dans Désignation/Nom
      - garde les lignes ayant au moins un nom OU un montant
      - **déduplique** les lignes strictement identiques (même date, même nom,
        même classe, même désignation, mêmes montants) — un doublon dans
        l'Excel d'origine ferait gonfler les totaux artificiellement.
    """
    if df is None or df.empty:
        return df

    # Mémorise le nombre de lignes brutes pour le diagnostic
    nb_raw = len(df)

    # Fix 2 : éliminer les lignes de résumé Excel (TOTAL, SOLDE, REPORT)
    is_summary = df.apply(
        lambda r: _is_summary_row(r.get("designation", ""), r.get("nom", "")),
        axis=1,
    )
    nb_summary = int(is_summary.sum())
    df = df[~is_summary]

    # Garde les lignes ayant un nom ou un montant
    nom_ok = df["nom"].astype(str).str.strip().ne("")
    montant_ok = (df["entree"].fillna(0) > 0) | (df["sortie"].fillna(0) > 0)
    keep_mask = nom_ok | montant_ok
    nb_empty = int((~keep_mask).sum())
    df = df[keep_mask]

    # Déduplication exacte (Fix 2)
    dedup_key = (
        df["date_affichage"].astype(str) + "|"
        + df["nom"].astype(str).str.strip().str.lower() + "|"
        + df["classe"].astype(str).str.strip().str.lower() + "|"
        + df["designation"].astype(str).str.strip().str.lower() + "|"
        + df["entree"].astype(float).map(lambda x: f"{x:.2f}") + "|"
        + df["sortie"].astype(float).map(lambda x: f"{x:.2f}")
    )
    dup_mask = dedup_key.duplicated(keep="first")
    nb_duplicates = int(dup_mask.sum())
    # Garde les lignes en double pour le panneau Diagnostic (avec colonne clé)
    df_duplicates = df.loc[dup_mask].copy() if nb_duplicates > 0 else pd.DataFrame()
    df = df.loc[~dup_mask].reset_index(drop=True)

    # Diagnostic exposé via df.attrs (consulté par le tableau de bord et Diagnostic)
    df.attrs["nb_raw_rows"] = nb_raw
    df.attrs["nb_summary_rows"] = nb_summary
    df.attrs["nb_empty_rows"] = nb_empty
    df.attrs["nb_duplicates"] = nb_duplicates
    df.attrs["duplicate_rows"] = df_duplicates   # pour Diagnostic

    return df


# --- CHARGEMENT BLINDÉ DE TOUTES LES DONNÉES (Fix 4 : get_all_values) ---
@st.cache_data(show_spinner="Chargement des données…")
def load_all_data():
    """Fix 4 - Synchronisation Intégrale :
    Lit ABSOLUMENT TOUTES les lignes du Google Sheet via worksheet.get_all_values().
    Cette méthode est plus fiable que get_all_records() car elle ignore les en-têtes
    dupliqués / manquants et capture vraiment chaque ligne du fichier."""
    sheet = get_sheet()
    if sheet is None:
        return pd.DataFrame(columns=COLS + ["annee"])

    try:
        # Fix 4 : LECTURE INTÉGRALE via get_all_values()
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return pd.DataFrame(columns=COLS + ["annee"])

    if not data or len(data) <= 1:
        return pd.DataFrame(columns=COLS + ["annee"])

    # Détecte si la première ligne est un en-tête (contient des libellés textuels)
    header_raw = [str(c).strip().lower() for c in data[0]]
    expected_headers = {h.lower() for h in COLS}
    has_header = any(h in expected_headers for h in header_raw)

    body = data[1:] if has_header else data

    # On essaie d'abord de mapper par nom de colonne (insensible à la casse / espaces)
    if has_header:
        # Pour chaque ligne, on extrait les colonnes selon l'index trouvé dans l'en-tête
        col_index_map = {}
        for col_name in COLS:
            for i, h in enumerate(header_raw):
                if h == col_name:
                    col_index_map[col_name] = i
                    break

        rows = []
        for r in body:
            r = list(r)
            row_dict = {}
            for col_name in COLS:
                if col_name in col_index_map:
                    idx = col_index_map[col_name]
                    row_dict[col_name] = r[idx] if idx < len(r) else ""
                else:
                    row_dict[col_name] = ""
            rows.append([row_dict[c] for c in COLS])
    else:
        # Fallback : on suppose l'ordre standard des colonnes
        rows = []
        for r in body:
            r = list(r) + [""] * (len(COLS) - len(r))
            rows.append(r[: len(COLS)])

    df = pd.DataFrame(rows, columns=COLS)

    if df is None or df.empty:
        return pd.DataFrame(columns=COLS + ["annee"])

    nb_sheet_rows = len(df)
    df = _normalize_df(df)
    # Sauvegarde des attrs avant _apply_strict_filter (qui reset_index les perd)
    nb_dates_repaired = int(df.attrs.get("nb_dates_repaired", 0))
    aberrant_records = df.attrs.get("aberrant_records", [])
    # Ajoute les colonnes nom/classe/designation aux enregistrements aberrants
    for rec in aberrant_records:
        pos = rec["pos"]
        if pos < len(df):
            rec["nom"] = str(df.iloc[pos].get("nom", ""))
            rec["classe"] = str(df.iloc[pos].get("classe", ""))
            rec["designation"] = str(df.iloc[pos].get("designation", ""))
            rec["entree"] = float(df.iloc[pos].get("entree", 0) or 0)
            rec["sortie"] = float(df.iloc[pos].get("sortie", 0) or 0)
            rec["id"] = str(df.iloc[pos].get("id", ""))
        else:
            rec.setdefault("nom", "")
            rec.setdefault("classe", "")
            rec.setdefault("designation", "")
            rec.setdefault("entree", 0.0)
            rec.setdefault("sortie", 0.0)
            rec.setdefault("id", "")

    df = _apply_strict_filter(df)

    # Tri chronologique inversé : le plus récent en premier (Fix 3)
    if "date_triable" in df.columns and not df.empty:
        df = df.sort_values(
            "date_triable", ascending=False, kind="mergesort"
        ).reset_index(drop=True)

    # Diagnostic global (consulté par le tableau de bord et le panneau Diagnostic)
    df.attrs["nb_sheet_rows"] = nb_sheet_rows
    df.attrs["nb_dates_repaired"] = nb_dates_repaired
    df.attrs["aberrant_records"] = aberrant_records
    return df


def load_data(mois_selectionne):
    df = load_all_data()
    return df[df["mois"] == mois_selectionne].reset_index(drop=True)


# --- INVALIDATION DU CACHE ---
def _invalidate_cache():
    """Vide TOUT le cache des données pour forcer un rechargement frais
    depuis le Google Sheet au prochain appel de load_all_data()."""
    try:
        load_all_data.clear()
    except Exception:
        pass
    try:
        st.cache_data.clear()
    except Exception:
        pass


# --- ENREGISTREMENT D'UNE NOUVELLE OPÉRATION (Fix 1) ---
def save_entry(mois, d, nom, classe, designation, entree, sortie):
    """Fix 1 : ajoute une nouvelle ligne à la fin du Google Sheet
    et invalide le cache pour que la donnée soit visible immédiatement."""
    sheet = get_sheet()
    if sheet is None:
        st.error("Connexion au Google Sheet indisponible.")
        return False
    try:
        new_id = str(int(time.time() * 1000))
        date_iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
        sheet.append_row([
            new_id,
            str(mois or ""),
            date_iso,
            str(designation or ""),
            str(nom or ""),
            str(classe or ""),
            str(float(entree or 0)),
            str(float(sortie or 0)),
        ])
        _invalidate_cache()
        return True
    except Exception as e:
        st.error(f"Erreur enregistrement : {e}")
        return False


# --- SUPPRESSION PAR ID UNIQUE (Fix 5) ---
def delete_item(item_id):
    """Fix 5 : suppression d'une ligne par son ID unique.
    Fonctionne dans tous les onglets (mois courant ET archives)."""
    sheet = get_sheet()
    if sheet is None:
        return False
    try:
        data = sheet.get_all_values()
    except Exception as e:
        st.error(f"Erreur lecture Sheet : {e}")
        return False

    target = str(item_id).strip()
    if not target:
        return False
    for i, row in enumerate(data):
        if i == 0:
            continue
        if len(row) > 0 and str(row[0]).strip() == target:
            sheet.delete_rows(i + 1)
            _invalidate_cache()
            return True
    return False


# --- MODIFICATION PAR ID UNIQUE ---
def update_item(item_id, updates):
    """Met à jour les champs d'une ligne identifiée par son id (1ère colonne)."""
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
                _invalidate_cache()
                return True
            except Exception as e:
                st.error(f"Erreur mise à jour : {e}")
                return False
    return False


# --- NETTOYAGE DES LIGNES VIDES OU TESTS ---
def cleanup_empty_rows():
    """Supprime les lignes vraiment vides : aucun nom, aucune désignation, montants à 0."""
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
            indices_to_delete.append(i + 1)

    deleted = 0
    for sheet_row in sorted(indices_to_delete, reverse=True):
        try:
            sheet.delete_rows(sheet_row)
            deleted += 1
        except Exception:
            pass
    if deleted > 0:
        _invalidate_cache()
    return deleted


# --- SUPPRESSION DE TOUTE UNE ANNÉE ---
def delete_year(annee):
    """Supprime toutes les lignes du Google Sheet appartenant à l'année donnée."""
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
        date_str = str(row[2]).strip() if len(row) > 2 else ""
        try:
            parsed = pd.to_datetime(date_str, errors="coerce", dayfirst=True)
            if pd.isna(parsed):
                parsed = pd.to_datetime(date_str, errors="coerce")
            # Fix 3 : si date manquante, l'année par défaut est 2022
            y = int(parsed.year) if pd.notna(parsed) else DEFAULT_DATE_YEAR
        except Exception:
            y = DEFAULT_DATE_YEAR
        if y == int(annee):
            indices_to_delete.append(i + 1)

    deleted = 0
    for sheet_row in sorted(indices_to_delete, reverse=True):
        try:
            sheet.delete_rows(sheet_row)
            deleted += 1
        except Exception:
            pass
    if deleted > 0:
        _invalidate_cache()
    return deleted


# --- NETTOYAGE FORCÉ DES LIGNES INCOMPLÈTES ---
def cleanup_incomplete_rows():
    """Nettoyage forcé : supprime les lignes incomplètes (pas de nom OU pas de montant)."""
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
    if deleted > 0:
        _invalidate_cache()
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

    details("Recu N :", row.get("id", ""))
    details("Date :", row.get("date_affichage", row.get("date", "")) or "Date non spécifiée")
    details("Mois :", row.get("mois", ""))
    details("Eleve :", row.get("nom", ""))
    details("Classe :", row.get("classe", ""))
    details("Motif :", row.get("designation", ""))

    pdf.ln(3)
    y = pdf.get_y()
    pdf.set_draw_color(150, 150, 150)
    pdf.line(margin + 5, y, 210 - margin - 5, y)
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(6)

    entree_val = float(row.get("entree", 0) or 0)
    sortie_val = float(row.get("sortie", 0) or 0)
    montant = entree_val if entree_val > 0 else sortie_val
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
        pdf.cell(45, 7, _safe(r.get("nom", ""))[:25], border=1)
        pdf.cell(22, 7, _safe(r.get("classe", ""))[:12], border=1)
        pdf.cell(50, 7, _safe(r.get("designation", ""))[:30], border=1)
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


def render_rows_with_actions(df, mois, key_prefix):
    """Fix 6 : Affiche chaque ligne avec ses propres boutons :
    Modifier / Supprimer / Reçu PDF. Fonctionne dans TOUS les onglets
    (mois en cours et archives). Toute suppression cible la vraie ligne
    du Google Sheet via son ID.

    Fix 3 : les lignes sont triées du plus récent au plus ancien.
    """
    if df is None or df.empty:
        return

    # Fix 3 : tri chronologique inversé (du plus récent au plus ancien)
    if "date_triable" in df.columns:
        df = df.sort_values(
            "date_triable", ascending=False, kind="mergesort"
        ).reset_index(drop=True)

    headers = ["Date", "Nom", "Classe", "Désignation", "Entrée", "Sortie", "Actions"]
    col_widths = [1.2, 1.6, 1.0, 2.2, 1.0, 1.0, 1.6]

    h = st.columns(col_widths)
    for i, t in enumerate(headers):
        h[i].markdown(f"**{t}**")
    st.markdown("---")

    for idx, (_, row) in enumerate(df.iterrows()):
        rid = str(row.get("id", "") or "").strip()
        # Compteur idx + ID = clé toujours unique, même si l'ID est vide ou dupliqué
        uniq = f"{key_prefix}_{idx}_{rid}"

        c = st.columns(col_widths)
        c[0].write(row.get("date_affichage", "") or "Date non spécifiée")
        c[1].write(row.get("nom", ""))
        c[2].write(row.get("classe", ""))
        c[3].write(row.get("designation", ""))
        c[4].write(fmt_fcfa(float(row.get("entree", 0) or 0)))
        c[5].write(fmt_fcfa(float(row.get("sortie", 0) or 0)))

        a1, a2, a3 = c[6].columns(3)
        edit_key = f"row_edit_open_{uniq}"
        pdf_key = f"row_pdf_{uniq}"

        if a1.button("Modifier", key=f"row_editbtn_{uniq}", help="Modifier cette opération"):
            st.session_state[edit_key] = True

        if a2.button("Supprimer", key=f"row_delbtn_{uniq}", help="Supprimer définitivement du Google Sheet"):
            if not rid:
                st.error("Cette ligne n'a pas d'ID — suppression impossible.")
            elif delete_item(rid):
                st.success(f"Ligne « {row.get('nom','')} » supprimée du Sheet.")
                time.sleep(0.6)
                st.rerun()
            else:
                st.error("Suppression impossible (ID introuvable dans le Sheet).")

        if a3.button("Reçu PDF", key=f"row_pdfbtn_{uniq}", help="Préparer le reçu PDF"):
            st.session_state[pdf_key] = (
                build_receipt_pdf(row),
                f"recu_{rid or idx}_{row.get('nom','')}.pdf",
            )

        if pdf_key in st.session_state:
            pb, pf = st.session_state[pdf_key]
            st.download_button(
                f"Télécharger le reçu de {row.get('nom','')}",
                data=pb,
                file_name=pf,
                mime="application/pdf",
                key=f"row_dl_{uniq}",
            )

        # --- Formulaire d'édition par ligne ---
        if st.session_state.get(edit_key):
            with st.form(f"row_edit_form_{uniq}"):
                st.markdown(f"**Modifier la ligne de {row.get('nom','')}**")
                try:
                    d_default = pd.to_datetime(row["date"], dayfirst=True).date()
                except Exception:
                    d_default = date.today()
                e_d = st.date_input("Date", value=d_default, key=f"re_d_{uniq}")
                e_mois = st.selectbox(
                    "Mois", MONTHS,
                    index=MONTHS.index(row["mois"]) if row["mois"] in MONTHS else MONTHS.index(mois),
                    key=f"re_m_{uniq}",
                )
                e_nom = st.text_input("Nom", value=row.get("nom", ""), key=f"re_n_{uniq}")
                e_cl = st.text_input("Classe", value=row.get("classe", ""), key=f"re_c_{uniq}")
                e_des = st.text_input("Désignation", value=row.get("designation", ""), key=f"re_des_{uniq}")
                e_ent = st.number_input(
                    "Entrée (FCFA)", min_value=0.0, step=500.0,
                    value=float(row.get("entree", 0) or 0), key=f"re_ent_{uniq}",
                )
                e_sor = st.number_input(
                    "Sortie (FCFA)", min_value=0.0, step=500.0,
                    value=float(row.get("sortie", 0) or 0), key=f"re_sor_{uniq}",
                )
                cs, cc = st.columns(2)
                save = cs.form_submit_button("Enregistrer", type="primary")
                cancel = cc.form_submit_button("Annuler")
                if save:
                    ok = update_item(rid, {
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
                        st.success("Ligne modifiée dans le Sheet.")
                        time.sleep(0.6)
                        st.rerun()
                    else:
                        st.error("Modification impossible (ID introuvable).")
                if cancel:
                    st.session_state[edit_key] = False
                    st.rerun()

        st.markdown("<hr style='margin:4px 0;border:0;border-top:1px solid #eee'>", unsafe_allow_html=True)


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
            expected = None
        if not expected:
            expected = os.environ.get("MON_MOT_DE_PASSE")
        if pwd and expected and pwd == expected:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")


def render_diagnostic_panel(df_full):
    """Panneau Diagnostic — 4 sections :
      1. Nombre de lignes par mois (tous mois/années)
      2. Montants suspects (> 1 000 000 FCFA)
      3. Doublons identifiés (proposés à la suppression)
      4. Erreurs de dates à corriger (dates aberrantes avant 2020)
    """
    with st.expander("Diagnostic des données", expanded=False):

        # ── 1. LIGNES PAR MOIS ───────────────────────────────────────────────
        st.markdown("#### 1. Nombre de lignes par mois")
        if df_full.empty:
            st.info("Aucune donnée chargée.")
        else:
            # Compte par (annee, mois) dans l'ordre scolaire
            grp = (
                df_full.groupby(["annee", "mois"])
                .agg(
                    nb=("id", "count"),
                    entrees=("entree", "sum"),
                    sorties=("sortie", "sum"),
                )
                .reset_index()
            )
            # Trie par année puis par ordre scolaire des mois
            month_order = {m: i for i, m in enumerate(MONTHS)}
            grp["mois_order"] = grp["mois"].map(lambda m: month_order.get(m, 99))
            grp = grp.sort_values(["annee", "mois_order"]).drop(
                columns=["mois_order"]
            )
            grp = grp.rename(
                columns={
                    "annee": "Année",
                    "mois": "Mois",
                    "nb": "Lignes",
                    "entrees": "Entrées (FCFA)",
                    "sorties": "Sorties (FCFA)",
                }
            )
            grp["Entrées (FCFA)"] = grp["Entrées (FCFA)"].map(
                lambda x: f"{x:,.0f}".replace(",", " ")
            )
            grp["Sorties (FCFA)"] = grp["Sorties (FCFA)"].map(
                lambda x: f"{x:,.0f}".replace(",", " ")
            )
            st.dataframe(grp, use_container_width=True, hide_index=True)
            st.caption(
                f"Total : **{len(df_full)}** lignes valides sur "
                f"**{df_full.attrs.get('nb_sheet_rows', '?')}** lignes brutes dans le Sheet."
            )

        st.divider()

        # ── 2. MONTANTS SUSPECTS (> 1 000 000) ──────────────────────────────
        st.markdown("#### 2. Montants supérieurs à 1 000 000 FCFA")
        SEUIL = 1_000_000
        if not df_full.empty:
            mask_gros = (df_full["entree"] > SEUIL) | (df_full["sortie"] > SEUIL)
            df_gros = df_full.loc[mask_gros].copy()
        else:
            df_gros = pd.DataFrame()

        if df_gros.empty:
            st.success("Aucun montant supérieur à 1 000 000 FCFA détecté.")
        else:
            st.warning(
                f"{len(df_gros)} ligne(s) avec un montant > 1 000 000 FCFA — "
                "vérifiez qu'il ne s'agit pas d'une erreur de saisie."
            )
            for idx, (_, row) in enumerate(df_gros.iterrows()):
                rid = str(row.get("id", "") or "").strip()
                uniq = f"diag_gros_{idx}_{rid}"
                c = st.columns([1.2, 1.6, 1.0, 2.0, 1.1, 1.1, 1.2])
                c[0].write(row.get("date_affichage", ""))
                c[1].write(row.get("nom", ""))
                c[2].write(row.get("classe", ""))
                c[3].write(row.get("designation", ""))
                ent = float(row.get("entree", 0) or 0)
                sor = float(row.get("sortie", 0) or 0)
                c[4].write(
                    f"**{fmt_fcfa(ent)}**" if ent > SEUIL else fmt_fcfa(ent)
                )
                c[5].write(
                    f"**{fmt_fcfa(sor)}**" if sor > SEUIL else fmt_fcfa(sor)
                )
                if c[6].button(
                    "Supprimer", key=f"diag_del_gros_{uniq}",
                    help="Supprimer cette ligne du Google Sheet"
                ):
                    if rid and delete_item(rid):
                        st.success(f"Ligne « {row.get('nom','')} » supprimée.")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("Suppression impossible.")
                st.markdown(
                    "<hr style='margin:3px 0;border:0;border-top:1px solid #eee'>",
                    unsafe_allow_html=True,
                )

        st.divider()

        # ── 3. DOUBLONS ──────────────────────────────────────────────────────
        st.markdown("#### 3. Doublons identifiés")
        df_dup = df_full.attrs.get("duplicate_rows", pd.DataFrame())
        if df_dup is None or (isinstance(df_dup, pd.DataFrame) and df_dup.empty):
            st.success("Aucun doublon détecté — toutes les lignes sont uniques.")
        else:
            st.warning(
                f"{len(df_dup)} ligne(s) en double détectée(s) et déjà exclue(s) "
                "des totaux. Vous pouvez les supprimer définitivement du Google Sheet."
            )
            for idx, (_, row) in enumerate(df_dup.iterrows()):
                rid = str(row.get("id", "") or "").strip()
                uniq = f"diag_dup_{idx}_{rid}"
                c = st.columns([1.2, 1.6, 1.0, 2.0, 1.1, 1.1, 1.2])
                c[0].write(row.get("date_affichage", ""))
                c[1].write(row.get("nom", ""))
                c[2].write(row.get("classe", ""))
                c[3].write(row.get("designation", ""))
                c[4].write(fmt_fcfa(float(row.get("entree", 0) or 0)))
                c[5].write(fmt_fcfa(float(row.get("sortie", 0) or 0)))
                if c[6].button(
                    "Supprimer", key=f"diag_del_dup_{uniq}",
                    help="Supprimer ce doublon du Google Sheet"
                ):
                    if rid and delete_item(rid):
                        st.success(f"Doublon « {row.get('nom','')} » supprimé.")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("Suppression impossible (ID introuvable).")
                st.markdown(
                    "<hr style='margin:3px 0;border:0;border-top:1px solid #eee'>",
                    unsafe_allow_html=True,
                )

        st.divider()

        # ── 4. ERREURS DE DATES ──────────────────────────────────────────────
        st.markdown("#### 4. Erreurs à corriger — Dates aberrantes")
        aberrant = df_full.attrs.get("aberrant_records", [])
        if not aberrant:
            st.success("Aucune date aberrante détectée — toutes les dates sont valides.")
        else:
            st.warning(
                f"{len(aberrant)} ligne(s) avec une date invalide ou antérieure à 2020. "
                "La correction automatique a été appliquée — vérifiez la colonne "
                "« Date corrigée » et corrigez directement dans le Google Sheet si besoin."
            )
            diag_cols = st.columns([0.8, 1.2, 1.5, 1.0, 1.8, 1.0, 1.0])
            for lbl in ["Ligne", "Date originale", "Date corrigée", "Nom", "Désignation", "Entrée", "Sortie"]:
                diag_cols[["Ligne", "Date originale", "Date corrigée", "Nom", "Désignation", "Entrée", "Sortie"].index(lbl)].markdown(f"**{lbl}**")
            st.markdown("---")
            for rec in aberrant:
                row_c = st.columns([0.8, 1.2, 1.5, 1.0, 1.8, 1.0, 1.0])
                row_c[0].write(str(rec.get("pos", "") + 2))   # +2 : 1 pour l'en-tête, 1 pour 0-index
                row_c[1].write(f"**:red[{rec.get('date_originale', '')}]**")
                row_c[2].write(rec.get("date_corrigee", ""))
                row_c[3].write(rec.get("nom", ""))
                row_c[4].write(rec.get("designation", ""))
                row_c[5].write(fmt_fcfa(rec.get("entree", 0)))
                row_c[6].write(fmt_fcfa(rec.get("sortie", 0)))
                st.markdown(
                    "<hr style='margin:3px 0;border:0;border-top:1px solid #eee'>",
                    unsafe_allow_html=True,
                )


def render_global_dashboard(df_full, annee=None):
    """Tableau de bord global : cumul des opérations.
    Si `annee` est fourni, n'affiche que les totaux de cette année.
    Si `annee` est None, affiche en plus la comparaison avec les
    totaux Excel attendus (Fix 2 : ligne rouge du fichier client).
    """
    if annee is not None:
        st.markdown(f"### Tableau de bord — Année {annee}")
        st.caption(f"Cumul des opérations enregistrées en {annee}")
    else:
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
    c3.metric("Solde", fmt_fcfa(t_e - t_s))
    c4.metric("Opérations", f"{nb}")

    # Fix 2 : comparaison avec les totaux Excel attendus (uniquement
    # pour le tableau de bord global toutes années confondues).
    if annee is None and not df_full.empty:
        diff_e = t_e - EXPECTED_ENTREES
        diff_s = t_s - EXPECTED_SORTIES
        diff_solde = (t_e - t_s) - EXPECTED_SOLDE

        if abs(diff_e) < 1 and abs(diff_s) < 1:
            st.success(
                f"Totaux conformes à l'Excel : "
                f"Entrées {fmt_fcfa(EXPECTED_ENTREES)} · "
                f"Sorties {fmt_fcfa(EXPECTED_SORTIES)} · "
                f"Solde {fmt_fcfa(EXPECTED_SOLDE)}."
            )
        else:
            with st.expander(
                "Comparaison avec la ligne rouge de l'Excel",
                expanded=True,
            ):
                e1, e2, e3 = st.columns(3)
                e1.metric(
                    "Entrées attendues",
                    fmt_fcfa(EXPECTED_ENTREES),
                    delta=f"{diff_e:+,.0f} FCFA".replace(",", " "),
                    delta_color="inverse",
                )
                e2.metric(
                    "Sorties attendues",
                    fmt_fcfa(EXPECTED_SORTIES),
                    delta=f"{diff_s:+,.0f} FCFA".replace(",", " "),
                    delta_color="inverse",
                )
                e3.metric(
                    "Solde attendu",
                    fmt_fcfa(EXPECTED_SOLDE),
                    delta=f"{diff_solde:+,.0f} FCFA".replace(",", " "),
                    delta_color="inverse",
                )
                nb_raw = df_full.attrs.get("nb_sheet_rows", "?")
                nb_summary = df_full.attrs.get("nb_summary_rows", "?")
                nb_empty = df_full.attrs.get("nb_empty_rows", "?")
                nb_dup = df_full.attrs.get("nb_duplicates", "?")
                nb_repaired = df_full.attrs.get("nb_dates_repaired", "?")
                st.caption(
                    f"Lignes lues dans le Sheet : **{nb_raw}** · "
                    f"lignes de résumé ignorées : **{nb_summary}** · "
                    f"lignes vides ignorées : **{nb_empty}** · "
                    f"doublons écartés : **{nb_dup}** · "
                    f"dates aberrantes corrigées : **{nb_repaired}**."
                )
                if abs(diff_e) >= 1 or abs(diff_s) >= 1:
                    st.info(
                        "Si l'écart persiste, utilisez « Outils d'administration "
                        "→ Nettoyer les lignes vides » pour purger d'éventuelles "
                        "lignes fantômes restantes dans le Google Sheet."
                    )

    st.divider()


def render_new_entry_form(default_mois):
    """Fix 1 : Formulaire d'enregistrement d'une nouvelle opération.
    Affiché en haut de la page dans un st.expander."""
    with st.expander("Ajouter une opération", expanded=False):
        with st.form("form_new_entry", clear_on_submit=True):
            fc1, fc2 = st.columns(2)
            with fc1:
                f_date = st.date_input("Date", value=date.today(), key="ne_date")
                f_mois = st.selectbox(
                    "Mois",
                    MONTHS,
                    index=MONTHS.index(default_mois) if default_mois in MONTHS else 0,
                    key="ne_mois",
                )
                f_nom = st.text_input("Nom de l'élève", key="ne_nom")
                f_classe = st.text_input("Classe", key="ne_classe")
            with fc2:
                f_des = st.text_input("Désignation", key="ne_des")
                f_entree = st.number_input(
                    "Entrée (FCFA)", min_value=0.0, step=500.0, key="ne_entree"
                )
                f_sortie = st.number_input(
                    "Sortie (FCFA)", min_value=0.0, step=500.0, key="ne_sortie"
                )
            submitted = st.form_submit_button("Enregistrer", type="primary")
            if submitted:
                if not f_nom.strip() and f_entree == 0 and f_sortie == 0:
                    st.warning("Veuillez renseigner au moins un nom ou un montant.")
                elif save_entry(
                    f_mois, f_date, f_nom, f_classe, f_des, f_entree, f_sortie
                ):
                    st.success(
                        f"Opération enregistrée pour {f_mois} {f_date.year}. "
                        "L'application va se rafraîchir."
                    )
                    time.sleep(0.6)
                    st.rerun()


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

    # --- Charge TOUTES les données depuis le cache (Sheet appelé seulement si invalidé) ---
    df_full = load_all_data()

    current_year = date.today().year

    # ============================================================
    # FORMULAIRE D'ENREGISTREMENT (Fix 1) — TOUJOURS VISIBLE EN HAUT
    # ============================================================
    today_m = date.today().month
    default_mois = next(
        (m for m in MONTHS if MONTH_INDEX.get(m) == today_m),
        MONTHS[0],
    )
    render_new_entry_form(default_mois)

    # ============================================================
    # TABLEAU DE BORD GLOBAL (toutes années confondues)
    # ============================================================
    render_global_dashboard(df_full)
    annees_count = (
        len({int(y) for y in df_full["annee"].unique() if int(y) > 0})
        if not df_full.empty else 0
    )
    nb_sheet_rows = df_full.attrs.get("nb_sheet_rows", len(df_full))
    st.caption(
        f"**Lecture intégrale du Google Sheet** : "
        f"{nb_sheet_rows} ligne(s) brute(s) lue(s) · "
        f"{len(df_full)} opération(s) valide(s) après nettoyage · "
        f"{annees_count} année(s) couverte(s) · "
        f"objectif Excel : 5934 lignes."
    )

    # ============================================================
    # PANNEAU DIAGNOSTIC
    # ============================================================
    render_diagnostic_panel(df_full)

    # ============================================================
    # SÉLECTEUR D'ANNÉE
    # ============================================================
    available_years = sorted(
        {int(y) for y in df_full["annee"].unique() if int(y) > 0},
        reverse=True,
    )
    if current_year not in available_years:
        available_years = [current_year] + available_years

    sel_col1, sel_col2 = st.columns([3, 1])
    with sel_col1:
        sel_year = st.selectbox(
            "Choisir une année à afficher",
            options=available_years,
            index=None,
            placeholder="-- Sélectionnez une année --",
            key="global_year_select",
        )
    with sel_col2:
        st.write("")
        st.write("")
        if st.button("Actualiser", type="secondary", help="Recharger depuis le Google Sheet"):
            _invalidate_cache()
            for k in list(st.session_state.keys()):
                if (
                    k.startswith("row_pdf_")
                    or k.startswith("pdf_bytes_")
                    or k.startswith("arc_pdf")
                    or k.startswith("rep_bytes_")
                    or k.startswith("cur_rep_bytes_")
                    or k.startswith("month_rep_bytes_")
                    or k.startswith("year_rep_bytes_")
                ):
                    del st.session_state[k]
            st.toast("Données rechargées depuis le Google Sheet.")
            st.rerun()

    if sel_year is None:
        st.info(
            "Sélectionnez une année dans le menu ci-dessus pour afficher les opérations."
        )
        return

    # ============================================================
    # DONNÉES FILTRÉES SUR L'ANNÉE SÉLECTIONNÉE
    # ============================================================
    df_year = df_full[df_full["annee"] == sel_year].reset_index(drop=True)

    # Tableau de bord global de l'année sélectionnée
    render_global_dashboard(df_year, annee=sel_year)

    # --- Actions sur l'année entière (impression + suppression) ---
    act_c1, act_c2 = st.columns(2)

    with act_c1:
        year_rep_key = f"year_rep_bytes_{sel_year}"
        if st.button(
            f"Imprimer toute l'année {sel_year}",
            key=f"year_rep_btn_{sel_year}",
            use_container_width=True,
        ):
            st.session_state[year_rep_key] = (
                build_annual_report_pdf(df_year, "Année complète", sel_year),
                f"rapport_annuel_{sel_year}.pdf",
            )
        if year_rep_key in st.session_state:
            yrb, yrf = st.session_state[year_rep_key]
            st.download_button(
                "Télécharger le rapport annuel",
                data=yrb,
                file_name=yrf,
                mime="application/pdf",
                key=f"year_dlrep_{sel_year}",
                use_container_width=True,
            )

    with act_c2:
        with st.expander(f"Supprimer toute l'année {sel_year}"):
            st.warning(
                f"Cette action supprime **toutes les opérations de {sel_year}** "
                "(tous mois confondus) du Google Sheet. Action irréversible."
            )
            confirm_year = st.checkbox(
                f"Je confirme la suppression complète de l'année {sel_year}",
                key=f"del_year_confirm_{sel_year}",
            )
            if st.button(
                f"Supprimer définitivement {sel_year}",
                key=f"del_year_btn_{sel_year}",
                type="primary",
                disabled=not confirm_year,
            ):
                n = delete_year(sel_year)
                if n > 0:
                    st.success(f"{n} ligne(s) de {sel_year} supprimée(s).")
                    st.session_state["global_year_select"] = None
                    time.sleep(0.8)
                    st.rerun()
                else:
                    st.info(f"Aucune ligne {sel_year} trouvée.")

    # --- Outils d'administration ---
    with st.expander("Outils d'administration"):
        st.write("Nettoyage des lignes vides ou test (sans nom, sans désignation, montants à 0).")
        if st.button("Nettoyer les lignes vides", key="clean_empty_btn"):
            n = cleanup_empty_rows()
            if n > 0:
                st.success(f"{n} ligne(s) supprimée(s).")
            else:
                st.info("Aucune ligne vide à supprimer.")
            time.sleep(0.6)
            st.rerun()

        st.markdown("---")
        st.write("**Nettoyage forcé** : supprime aussi les lignes incomplètes (pas de nom OU pas de montant).")
        force_confirm = st.checkbox(
            "Je confirme le nettoyage forcé",
            key="force_cleanup_confirm",
        )
        if st.button(
            "Nettoyage forcé",
            type="primary",
            disabled=not force_confirm,
            key="force_clean_btn",
        ):
            n = cleanup_incomplete_rows()
            if n > 0:
                st.success(f"{n} ligne(s) incomplète(s) supprimée(s).")
            else:
                st.info("Aucune ligne incomplète à supprimer.")
            time.sleep(0.6)
            st.rerun()

    # ============================================================
    # ONGLETS MENSUELS (Septembre -> Août) FILTRÉS SUR L'ANNÉE
    # ============================================================
    tabs = st.tabs(MONTHS)
    for i, mois in enumerate(MONTHS):
        with tabs[i]:
            df = df_year[df_year["mois"] == mois].reset_index(drop=True)

            st.markdown(f"**{mois} {sel_year}**")
            t_e = float(df["entree"].sum()) if not df.empty else 0.0
            t_s = float(df["sortie"].sum()) if not df.empty else 0.0
            c1, c2, c3 = st.columns(3)
            c1.metric("Entrées", fmt_fcfa(t_e))
            c2.metric("Sorties", fmt_fcfa(t_s))
            c3.metric("Solde", fmt_fcfa(t_e - t_s))

            # Fix 5 : Tableau des opérations + boutons par ligne
            # Fonctionne pour TOUS les mois et TOUTES les années (courante et archives)
            if df.empty:
                st.info(f"Aucune opération pour {mois} {sel_year}.")
            else:
                render_rows_with_actions(
                    df, mois,
                    key_prefix=f"m_{mois}_{sel_year}",
                )

                # Fix 5 : Bouton "Imprimer le mois" (fonctionne partout)
                month_rep_key = f"month_rep_bytes_{mois}_{sel_year}"
                if st.button(
                    f"Imprimer le mois ({mois} {sel_year})",
                    key=f"month_rep_btn_{mois}_{sel_year}",
                ):
                    st.session_state[month_rep_key] = (
                        build_annual_report_pdf(df, mois, sel_year),
                        f"rapport_{mois}_{sel_year}.pdf",
                    )
                if month_rep_key in st.session_state:
                    mrb, mrf = st.session_state[month_rep_key]
                    st.download_button(
                        "Télécharger le rapport du mois",
                        data=mrb,
                        file_name=mrf,
                        mime="application/pdf",
                        key=f"month_dlrep_{mois}_{sel_year}",
                    )


if __name__ == "__main__":
    main()
