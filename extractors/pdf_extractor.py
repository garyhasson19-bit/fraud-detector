"""
Extracteur PDF de relevés bancaires — version robuste multi-banques.
Stratégie : essaie 4 méthodes dans l'ordre, prend la meilleure.
Compatible : BNP, CA, SG, CIC, LCL, Banque Populaire, Caisse d'Épargne, CaixaBank, Boursorama, etc.
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
PDF_MAGIC_BYTES = b'%PDF'


def _validate_pdf(data: bytes):
    if len(data) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"Fichier trop volumineux (max {MAX_FILE_SIZE_MB} Mo).")
    if len(data) < 4 or data[:4] != PDF_MAGIC_BYTES:
        raise ValueError("Le fichier ne semble pas être un PDF valide.")


# ── Nettoyage des montants ────────────────────────────────────────────────────

def clean_amount(raw) -> float | None:
    """
    Convertit n'importe quelle chaîne de montant en float.
    Gère : 1 234,56 / 1.234,56 / 1,234.56 / -800 / 800,00 / 1 234.56 etc.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ('-', '—', 'N/A', ''):
        return None

    # Supprimer espaces insécables, symboles monétaires, lettres parasite
    s = s.replace('\xa0', '').replace(' ', '').replace('€', '').replace('EUR', '')
    s = s.replace(' ', '').replace(' ', '')

    negative = s.startswith('-') or s.startswith('(') or s.endswith('-')
    s = s.replace('(', '').replace(')', '').lstrip('-').rstrip('-').strip()

    # Déterminer le séparateur décimal (virgule ou point)
    # Règle : si le dernier séparateur est une virgule ou un point suivi de 2 chiffres → décimal
    # Sinon → séparateur de milliers
    comma_pos = s.rfind(',')
    dot_pos   = s.rfind('.')

    if comma_pos == -1 and dot_pos == -1:
        # Pas de séparateur → entier
        try:
            val = float(s)
        except ValueError:
            return None
    elif comma_pos > dot_pos:
        # Format européen : 1.234,56 ou 1234,56
        s = s.replace('.', '').replace(',', '.')
        try:
            val = float(s)
        except ValueError:
            return None
    else:
        # Format anglais : 1,234.56 ou 1234.56
        s = s.replace(',', '')
        try:
            val = float(s)
        except ValueError:
            return None

    return -val if negative else val


def parse_date_safe(raw) -> datetime | None:
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw or raw.lower() in ('none', '-', ''):
        return None
    # Normalise les séparateurs
    raw = re.sub(r'[\.\-]', '/', raw)
    try:
        return date_parser.parse(raw, dayfirst=True)
    except Exception:
        pass
    # Essaie quelques formats communs
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y/%m/%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            pass
    return None


# ── Détection de colonnes ────────────────────────────────────────────────────

DATE_KEYWORDS   = ['date', 'jour', 'date op', 'date val', 'date ope', 'date opé']
DESC_KEYWORDS   = ['libellé', 'libelle', 'motif', 'opération', 'operation', 'détail',
                   'detail', 'description', 'label', 'nature', 'désignation', 'designation']
DEBIT_KEYWORDS  = ['débit', 'debit', 'sortie', 'retrait', 'dépense', 'depense', 'db', 'dbt']
CREDIT_KEYWORDS = ['crédit', 'credit', 'entrée', 'entree', 'versement', 'recette', 'cr', 'crt']
AMOUNT_KEYWORDS = ['montant', 'amount', 'valeur', 'solde mouvement', 'mvt']


def _find_col(header, keywords):
    for i, h in enumerate(header):
        h_low = str(h).lower().strip()
        if any(k in h_low for k in keywords):
            return i
    return None


# ── Méthode 1 : extraction par tableaux ─────────────────────────────────────

def _extract_tables(pdf_path: str) -> list[dict]:
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # Essaie différentes stratégies de détection de tableaux
            for strategy in [
                {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                {"vertical_strategy": "text",  "horizontal_strategy": "text"},
                {"vertical_strategy": "lines", "horizontal_strategy": "text"},
            ]:
                try:
                    tables = page.extract_tables(strategy)
                except Exception:
                    tables = page.extract_tables()

                for table in (tables or []):
                    if not table or len(table) < 2:
                        continue
                    header = [str(c or '').lower().strip() for c in table[0]]

                    col_date   = _find_col(header, DATE_KEYWORDS)
                    col_desc   = _find_col(header, DESC_KEYWORDS)
                    col_debit  = _find_col(header, DEBIT_KEYWORDS)
                    col_credit = _find_col(header, CREDIT_KEYWORDS)
                    col_amount = _find_col(header, AMOUNT_KEYWORDS)

                    # Il faut au moins une date OU une colonne montant
                    if col_date is None and col_amount is None and col_debit is None:
                        continue

                    for data_row in table[1:]:
                        if not data_row or all(not c for c in data_row):
                            continue

                        safe = lambda i: str(data_row[i]).strip() if i is not None and i < len(data_row) and data_row[i] else ''

                        raw_date = safe(col_date) if col_date is not None else ''
                        dt = parse_date_safe(raw_date)
                        if dt is None:
                            # Cherche une date dans n'importe quelle cellule
                            for cell in data_row:
                                dt = parse_date_safe(str(cell or ''))
                                if dt:
                                    break
                        if dt is None:
                            continue

                        desc = safe(col_desc) if col_desc is not None else ''
                        if not desc:
                            # Prend la plus longue cellule non-numérique
                            candidates = [str(c or '').strip() for c in data_row
                                          if c and not re.match(r'^[\d\s,.\-€+()]+$', str(c).strip())]
                            desc = max(candidates, key=len) if candidates else f"Transaction p.{page_num}"

                        debit  = clean_amount(safe(col_debit))  if col_debit  is not None else None
                        credit = clean_amount(safe(col_credit)) if col_credit is not None else None

                        if debit is None and credit is None and col_amount is not None:
                            val = clean_amount(safe(col_amount))
                            if val is not None:
                                if val < 0:
                                    debit = abs(val)
                                else:
                                    credit = val

                        # Si toujours rien → scanne toutes les cellules pour un montant
                        if debit is None and credit is None:
                            numeric_vals = []
                            for cell in data_row:
                                v = clean_amount(str(cell or ''))
                                if v is not None and abs(v) > 0:
                                    numeric_vals.append(v)
                            if len(numeric_vals) == 1:
                                v = numeric_vals[0]
                                if v < 0:
                                    debit = abs(v)
                                else:
                                    credit = v
                            elif len(numeric_vals) >= 2:
                                # Heuristique : dernier montant positif = crédit, avant-dernier = débit
                                positives = [v for v in numeric_vals if v > 0]
                                if len(positives) >= 2:
                                    debit, credit = positives[0], positives[1]
                                elif positives:
                                    credit = positives[-1]

                        if debit is None and credit is None:
                            continue

                        amount = -(debit or 0) + (credit or 0)
                        if amount == 0:
                            continue

                        rows.append({
                            'date': dt, 'description': desc[:200],
                            'debit': debit, 'credit': credit, 'amount': amount,
                            'page': page_num,
                            'raw_line': ' | '.join(str(c or '') for c in data_row)[:300],
                        })

                if rows:
                    break  # Une stratégie a marché pour cette page

    return rows


# ── Méthode 2 : extraction par texte brut ────────────────────────────────────

# Regex date : 01/01/2024, 01-01-24, 1 jan 2024, etc.
RE_DATE = re.compile(
    r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})'
    r'|\b(\d{1,2}\s+(?:jan|fév|feb|mar|avr|apr|mai|may|jun|juin|jul|juil|aug|aoû|sep|oct|nov|déc|dec)[a-zé]*\.?\s*\d{2,4})',
    re.IGNORECASE
)

# Montant : 1 234,56 / -1234.56 / (1 234,56) / 1234
RE_AMOUNT = re.compile(
    r'(?<!\w)'                      # pas précédé d'une lettre
    r'([+-]?\s*(?:\d{1,3}(?:[\s\xa0]\d{3})*|\d+)(?:[.,]\d{1,2})?)'
    r'(?:\s*€)?'
    r'(?!\w)',                      # pas suivi d'une lettre
)


def _extract_text(pdf_path: str) -> list[dict]:
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ''
            lines = text.split('\n')

            for line in lines:
                line = line.strip()
                if len(line) < 8:
                    continue

                # Cherche une date
                dm = RE_DATE.search(line)
                if not dm:
                    continue
                raw_date = dm.group(1) or dm.group(2)
                dt = parse_date_safe(raw_date)
                if dt is None:
                    continue

                # Trouve tous les montants
                amounts_raw = RE_AMOUNT.findall(line)
                amounts = [clean_amount(a) for a in amounts_raw]
                amounts = [a for a in amounts if a is not None and abs(a) >= 1]
                if not amounts:
                    continue

                # Description = ligne sans la date et sans les montants
                desc = RE_DATE.sub('', line)
                desc = RE_AMOUNT.sub('', desc)
                desc = re.sub(r'[€\s]{2,}', ' ', desc).strip()
                if not desc:
                    desc = f"Transaction p.{page_num}"

                # Détermine débit/crédit
                debit = credit = None
                if len(amounts) == 1:
                    if amounts[0] < 0:
                        debit = abs(amounts[0])
                    else:
                        credit = amounts[0]
                elif len(amounts) >= 2:
                    # Souvent : [débit, solde] ou [crédit, solde]
                    debit  = amounts[0] if amounts[0] > 0 else None
                    credit = amounts[1] if len(amounts) > 1 and amounts[1] > 0 and amounts[1] != amounts[0] else None

                if debit is None and credit is None:
                    continue

                amount = -(debit or 0) + (credit or 0)
                if amount == 0:
                    continue

                rows.append({
                    'date': dt, 'description': desc[:200],
                    'debit': debit, 'credit': credit, 'amount': amount,
                    'page': page_num, 'raw_line': line[:300],
                })

    return rows


# ── Méthode 3 : extraction par coordonnées (colonnes alignées) ───────────────

def _extract_by_columns(pdf_path: str) -> list[dict]:
    """
    Pour les relevés où texte et montants sont dans des colonnes visuellement alignées.
    Détecte les colonnes débit/crédit par position horizontale.
    """
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            words = page.extract_words(x_tolerance=5, y_tolerance=5)
            if not words:
                continue

            page_width = page.width
            # Heuristique : montants = mots à droite de la page (> 55% de la largeur)
            right_words  = [w for w in words if float(w['x0']) > page_width * 0.55]
            left_words   = [w for w in words if float(w['x0']) <= page_width * 0.55]

            # Groupe par ligne (y0 proche)
            def group_by_y(word_list, tolerance=4):
                if not word_list:
                    return {}
                lines = {}
                for w in sorted(word_list, key=lambda x: float(x['top'])):
                    y = round(float(w['top']) / tolerance) * tolerance
                    lines.setdefault(y, []).append(w)
                return lines

            right_lines = group_by_y(right_words)
            left_lines  = group_by_y(left_words)

            for y, r_words in right_lines.items():
                # Cherche un montant parmi les mots de droite
                amounts_in_line = []
                for w in r_words:
                    v = clean_amount(w['text'])
                    if v is not None and abs(v) >= 1:
                        amounts_in_line.append((v, float(w['x0'])))

                if not amounts_in_line:
                    continue

                # Cherche une date dans les mots de gauche sur la même ligne (±10px)
                dt = None
                desc_words = []
                for ly, l_words in left_lines.items():
                    if abs(ly - y) <= 10:
                        for w in l_words:
                            d = parse_date_safe(w['text'])
                            if d and dt is None:
                                dt = d
                            else:
                                desc_words.append(w['text'])
                if dt is None:
                    continue

                desc = ' '.join(desc_words).strip() or f"Transaction p.{page_num}"

                # Si 2 montants à droite → colonne débit | colonne crédit (positionnement)
                if len(amounts_in_line) >= 2:
                    amounts_sorted = sorted(amounts_in_line, key=lambda x: x[1])
                    debit  = abs(amounts_sorted[0][0]) if amounts_sorted[0][0] != 0 else None
                    credit = abs(amounts_sorted[1][0]) if amounts_sorted[1][0] != 0 else None
                    # Si débit = 0 ou crédit = 0 → ignorer
                    if debit == 0: debit = None
                    if credit == 0: credit = None
                else:
                    v = amounts_in_line[0][0]
                    debit  = abs(v) if v < 0 else None
                    credit = v     if v > 0 else None

                if debit is None and credit is None:
                    continue

                amount = -(debit or 0) + (credit or 0)
                if amount == 0:
                    continue

                rows.append({
                    'date': dt, 'description': desc[:200],
                    'debit': debit, 'credit': credit, 'amount': amount,
                    'page': page_num, 'raw_line': desc[:300],
                })
    return rows


# ── Point d'entrée principal ──────────────────────────────────────────────────

def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    df = df[df['amount'] != 0]
    # Dédoublonnage : même date + même montant + même description → garder 1
    df = df.drop_duplicates(subset=['date', 'amount', 'description'])
    df = df.sort_values('date').reset_index(drop=True)
    df['id'] = df.index + 1
    return df


def extract_pdf(uploaded_file) -> pd.DataFrame:
    """
    Point d'entrée. Essaie 3 méthodes d'extraction et retourne la plus complète.
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
        for name, fn in [
            ('tables',  _extract_tables),
            ('text',    _extract_text),
            ('columns', _extract_by_columns),
        ]:
            try:
                rows = fn(tmp_path)
                df = _rows_to_df(rows)
                results[name] = df
            except Exception as e:
                results[name] = pd.DataFrame()

        # Prend la méthode qui a trouvé le plus de transactions
        best = max(results.values(), key=len)

        # Si 2 méthodes ont trouvé des résultats, fusionne et dédoublonne
        non_empty = [df for df in results.values() if not df.empty]
        if len(non_empty) >= 2:
            merged = pd.concat(non_empty, ignore_index=True)
            merged['date'] = pd.to_datetime(merged['date'], errors='coerce')
            merged = merged.dropna(subset=['date'])
            merged['amount_round'] = merged['amount'].round(2)
            merged['desc_short'] = merged['description'].str[:40]
            merged = merged.drop_duplicates(subset=['date', 'amount_round', 'desc_short'])
            merged = merged.drop(columns=['amount_round', 'desc_short'], errors='ignore')
            merged = merged.sort_values('date').reset_index(drop=True)
            merged['id'] = merged.index + 1
            if len(merged) > len(best):
                return merged

        return best

    finally:
        os.unlink(tmp_path)
