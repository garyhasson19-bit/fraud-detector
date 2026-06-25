"""
Extracteur PDF de relevés bancaires — version sécurisée avec garde-fous.
Stratégie : tableaux en priorité, texte en fallback, avec validation stricte.
"""

import pdfplumber
import pandas as pd
import re
import tempfile
import os
from datetime import datetime
from dateutil import parser as date_parser


# ── Sécurité ──────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_MB = 50
PDF_MAGIC_BYTES  = b'%PDF'

# ── Garde-fous sur les données extraites ──────────────────────────────────────
DATE_MIN = datetime(2000, 1, 1)   # Aucun relevé avant 2000
DATE_MAX = datetime(2030, 12, 31) # Aucun relevé après 2030
MONTANT_MAX_TRANSACTION = 500_000  # Au-delà = probablement un solde cumulé, pas une transaction
MONTANT_MIN_TRANSACTION = 0.50     # En dessous = bruit (frais centimes ignorés sauf card testing)


def _validate_pdf(data: bytes):
    if len(data) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"Fichier trop volumineux (max {MAX_FILE_SIZE_MB} Mo).")
    if len(data) < 4 or data[:4] != PDF_MAGIC_BYTES:
        raise ValueError("Le fichier ne semble pas être un PDF valide.")


# ── Nettoyage des montants ────────────────────────────────────────────────────

def clean_amount(raw) -> float | None:
    """
    Convertit n'importe quel string de montant en float.
    Gère : 1 234,56 / 1.234,56 / 1,234.56 / -800 / (1500,00) etc.
    Retourne None si la valeur est hors des garde-fous.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ('-', '—', 'N/A', '', 'none', 'None'):
        return None

    # Supprimer caractères parasites
    s = s.replace('\xa0', '').replace(' ', '').replace(' ', '')
    s = s.replace('€', '').replace('EUR', '').replace(' ', '')

    negative = s.startswith('-') or (s.startswith('(') and s.endswith(')'))
    s = s.strip('()').lstrip('+-').strip()

    if not s:
        return None

    # Déterminer le séparateur décimal
    comma_pos = s.rfind(',')
    dot_pos   = s.rfind('.')

    try:
        if comma_pos == -1 and dot_pos == -1:
            val = float(s)
        elif comma_pos > dot_pos:
            # Format européen : 1.234,56
            val = float(s.replace('.', '').replace(',', '.'))
        else:
            # Format anglais : 1,234.56
            val = float(s.replace(',', ''))
    except ValueError:
        return None

    # Garde-fous sur le montant
    if val < 0:
        val = abs(val)
        negative = True
    if val < MONTANT_MIN_TRANSACTION or val > MONTANT_MAX_TRANSACTION:
        return None

    return -val if negative else val


def parse_date_safe(raw) -> datetime | None:
    """Parse une date en acceptant seulement les dates entre 2000 et 2030."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ('none', '-', '', 'date'):
        return None

    # Doit contenir au moins un chiffre
    if not any(c.isdigit() for c in s):
        return None

    # Normalise les séparateurs
    s = re.sub(r'[\.\-]', '/', s)

    try:
        dt = date_parser.parse(s, dayfirst=True, fuzzy=False)
        if DATE_MIN <= dt <= DATE_MAX:
            return dt
        return None
    except Exception:
        pass

    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s.replace('-', '/').replace('.', '/'), fmt)
            if DATE_MIN <= dt <= DATE_MAX:
                return dt
        except Exception:
            pass
    return None


# ── Détection de colonnes ────────────────────────────────────────────────────

DATE_KW   = ['date', 'jour', 'date op', 'date val', 'date ope', 'date opé', 'date opération']
DESC_KW   = ['libellé', 'libelle', 'motif', 'opération', 'operation', 'détail',
             'detail', 'description', 'label', 'nature', 'désignation']
DEBIT_KW  = ['débit', 'debit', 'sortie', 'retrait', 'dépense', 'depense', 'déb', 'deb']
CREDIT_KW = ['crédit', 'credit', 'entrée', 'entree', 'versement', 'recette', 'cré', 'cred']
AMOUNT_KW = ['montant', 'amount', 'valeur']
# Colonnes à IGNORER (contiennent des soldes cumulatifs, pas des transactions)
SOLDE_KW  = ['solde', 'balance', 'cumul', 'total', 'running']


def _find_col(header, keywords, exclude=None):
    for i, h in enumerate(header):
        h_low = str(h).lower().strip()
        if exclude and any(e in h_low for e in exclude):
            continue
        if any(k in h_low for k in keywords):
            return i
    return None


def _is_solde_col(header_cell: str) -> bool:
    return any(k in str(header_cell).lower() for k in SOLDE_KW)


# ── Méthode 1 : tableaux structurés ─────────────────────────────────────────

def _extract_tables(pdf_path: str) -> list[dict]:
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for table in (tables or []):
                if not table or len(table) < 2:
                    continue
                header = [str(c or '').strip() for c in table[0]]
                header_low = [h.lower() for h in header]

                col_date   = _find_col(header_low, DATE_KW)
                col_desc   = _find_col(header_low, DESC_KW)
                col_debit  = _find_col(header_low, DEBIT_KW,  exclude=SOLDE_KW)
                col_credit = _find_col(header_low, CREDIT_KW, exclude=SOLDE_KW)
                col_amount = _find_col(header_low, AMOUNT_KW, exclude=SOLDE_KW)

                # Ignore les colonnes "solde" pour éviter les cumuls
                solde_cols = {i for i, h in enumerate(header_low) if _is_solde_col(h)}

                if col_date is None:
                    continue
                if col_debit is None and col_credit is None and col_amount is None:
                    continue

                for data_row in table[1:]:
                    if not data_row:
                        continue

                    def safe(i):
                        if i is None or i >= len(data_row) or i in solde_cols:
                            return ''
                        return str(data_row[i] or '').strip()

                    # Date
                    dt = parse_date_safe(safe(col_date))
                    if dt is None:
                        continue

                    # Description
                    desc = safe(col_desc)
                    if not desc:
                        desc_parts = [
                            str(data_row[i] or '').strip()
                            for i in range(len(data_row))
                            if i not in {col_date, col_debit, col_credit, col_amount} | solde_cols
                            and str(data_row[i] or '').strip()
                        ]
                        desc = ' '.join(desc_parts)[:100] or f"Transaction p.{page_num}"

                    # Montants
                    debit  = clean_amount(safe(col_debit))  if col_debit  is not None else None
                    credit = clean_amount(safe(col_credit)) if col_credit is not None else None

                    if debit is None and credit is None and col_amount is not None:
                        val = clean_amount(safe(col_amount))
                        if val is not None:
                            if val < 0:
                                debit = abs(val)
                            else:
                                credit = val

                    if debit is None and credit is None:
                        continue

                    # Débit et crédit ne peuvent pas être tous les deux positifs et identiques
                    if debit is not None and credit is not None and debit == credit:
                        credit = None

                    amount = -(debit or 0) + (credit or 0)
                    if amount == 0:
                        continue

                    rows.append({
                        'date': dt, 'description': desc[:200],
                        'debit': debit, 'credit': credit, 'amount': amount,
                        'page': page_num,
                        'raw_line': ' | '.join(str(c or '') for c in data_row)[:200],
                    })
    return rows


# ── Méthode 2 : texte ligne par ligne ───────────────────────────────────────

# Formats de date stricts (évite de parser des numéros de compte comme des dates)
RE_DATE_STRICT = re.compile(
    r'\b(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})\b'           # 01/01/2024
    r'|\b(\d{2}[/\-\.]\d{2}[/\-\.]\d{2})\b'           # 01/01/24
    r'|\b(\d{1,2}\s+(?:jan|fév|feb|mar|avr|apr|mai|may|jun|juin|jul|juil|aug|aoû|sep|oct|nov|déc|dec)[a-zé]*\.?\s*\d{4})\b',
    re.IGNORECASE
)

# Montant : chiffres avec séparateur décimal obligatoire (évite les numéros bruts)
RE_AMOUNT_STRICT = re.compile(
    r'(?<![,\d])([+-]?\s*(?:\d{1,3}(?:[\s\xa0]\d{3})*|\d+)[,\.]\d{2})(?![,\.\d])'
)


def _extract_text(pdf_path: str) -> list[dict]:
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ''
            for line in text.split('\n'):
                line = line.strip()
                if len(line) < 10:
                    continue

                # Date stricte obligatoire
                dm = RE_DATE_STRICT.search(line)
                if not dm:
                    continue
                raw_date = dm.group(1) or dm.group(2) or dm.group(3)
                dt = parse_date_safe(raw_date)
                if dt is None:
                    continue

                # Montants avec décimales obligatoires
                amounts_raw = RE_AMOUNT_STRICT.findall(line)
                amounts = [clean_amount(a) for a in amounts_raw]
                amounts = [a for a in amounts if a is not None]
                if not amounts:
                    continue

                # Description
                desc = RE_DATE_STRICT.sub('', line)
                desc = RE_AMOUNT_STRICT.sub('', desc)
                desc = re.sub(r'[€\s]{2,}', ' ', desc).strip()[:150]
                if not desc:
                    desc = f"Transaction p.{page_num}"

                # Détermination débit/crédit
                # Si 2+ montants : le dernier est souvent le solde → on prend l'avant-dernier
                if len(amounts) >= 2:
                    # Heuristique : ignorer le dernier (souvent solde)
                    val = amounts[-2] if len(amounts) >= 2 else amounts[0]
                else:
                    val = amounts[0]

                debit  = abs(val) if val < 0 else None
                credit = val      if val > 0 else None

                if debit is None and credit is None:
                    continue

                amount = -(debit or 0) + (credit or 0)
                if amount == 0:
                    continue

                rows.append({
                    'date': dt, 'description': desc,
                    'debit': debit, 'credit': credit, 'amount': amount,
                    'page': page_num, 'raw_line': line[:200],
                })
    return rows


# ── Post-traitement & dédoublonnage ──────────────────────────────────────────

def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])

    # Garde-fous supplémentaires
    df = df[df['amount'] != 0]
    df = df[df['amount'].abs() >= MONTANT_MIN_TRANSACTION]
    df = df[df['amount'].abs() <= MONTANT_MAX_TRANSACTION]

    # Dates valides uniquement
    df = df[(df['date'] >= DATE_MIN) & (df['date'] <= DATE_MAX)]

    if df.empty:
        return df

    # Dédoublonnage strict
    df['_amt_r']   = df['amount'].round(2)
    df['_desc_s']  = df['description'].str.lower().str.strip().str[:40]
    df = df.drop_duplicates(subset=['date', '_amt_r', '_desc_s'])
    df = df.drop(columns=['_amt_r', '_desc_s'])

    df = df.sort_values('date').reset_index(drop=True)
    df['id'] = df.index + 1
    return df


# ── Point d'entrée ────────────────────────────────────────────────────────────

def extract_pdf(uploaded_file) -> pd.DataFrame:
    """
    Essaie tableaux puis texte.
    Garde la méthode qui extrait le plus de transactions VALIDES.
    """
    if isinstance(uploaded_file, str):
        with open(uploaded_file, 'rb') as f:
            raw = f.read()
    else:
        raw = uploaded_file.read()

    _validate_pdf(raw)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        results = {}
        for name, fn in [('tables', _extract_tables), ('text', _extract_text)]:
            try:
                df = _rows_to_df(fn(tmp_path))
                results[name] = df
            except Exception as e:
                print(f"[Extractor] {name}: {e}")
                results[name] = pd.DataFrame()

        # Préfère les tableaux si résultats similaires (plus fiables)
        df_tables = results.get('tables', pd.DataFrame())
        df_text   = results.get('text',   pd.DataFrame())

        if df_tables.empty and df_text.empty:
            return pd.DataFrame()

        if df_tables.empty:
            return df_text
        if df_text.empty:
            return df_tables

        # Si tableaux trouvent au moins 70% de ce que le texte trouve → tableaux
        if len(df_tables) >= len(df_text) * 0.70:
            best = df_tables
        else:
            # Fusionne les deux et dédoublonne
            merged = pd.concat([df_tables, df_text], ignore_index=True)
            merged['_r'] = merged['amount'].round(2)
            merged['_d'] = merged['description'].str.lower().str.strip().str[:40]
            merged = merged.drop_duplicates(subset=['date', '_r', '_d'])
            merged = merged.drop(columns=['_r', '_d'])
            merged = merged.sort_values('date').reset_index(drop=True)
            merged['id'] = merged.index + 1
            best = merged

        return best

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
