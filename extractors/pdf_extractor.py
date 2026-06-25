import pdfplumber
import pandas as pd
import re
from datetime import datetime
from dateutil import parser as date_parser
import io


AMOUNT_PATTERN = re.compile(
    r'([+-]?\s*[\d\s]+[.,]\d{2})\s*(?:€|EUR)?'
)
DATE_PATTERNS = [
    r'\b(\d{2}[/\-\.]\d{2}[/\-\.]\d{2,4})\b',
    r'\b(\d{1,2}\s+(?:jan|fév|mar|avr|mai|juin|juil|aoû|sep|oct|nov|déc)[a-zé]*\.?\s+\d{2,4})\b',
    r'\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{2,4})\b',
]


def clean_amount(raw: str) -> float:
    """Convert a raw amount string to float."""
    cleaned = raw.replace(' ', '').replace('\xa0', '')
    cleaned = cleaned.replace(',', '.')
    # Handle European format where . is thousands separator
    parts = cleaned.split('.')
    if len(parts) > 2:
        cleaned = ''.join(parts[:-1]) + '.' + parts[-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(raw: str) -> datetime:
    """Try to parse a date string."""
    try:
        return date_parser.parse(raw, dayfirst=True)
    except Exception:
        return None


def extract_transactions_from_text(text: str, page_num: int) -> list[dict]:
    """
    Heuristic extraction: find lines that contain a date and at least one amount.
    Works for most French bank statement layouts.
    """
    transactions = []
    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue

        # Try to find a date in the line
        found_date = None
        for pat in DATE_PATTERNS:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                found_date = parse_date(m.group(1))
                break

        if not found_date:
            continue

        # Find all amounts in the line
        amounts = AMOUNT_PATTERN.findall(line)
        if not amounts:
            continue

        # Remove amounts from description
        description = line
        for pat in DATE_PATTERNS:
            description = re.sub(pat, '', description, flags=re.IGNORECASE)
        description = AMOUNT_PATTERN.sub('', description)
        description = re.sub(r'\s{2,}', ' ', description).strip()
        description = re.sub(r'[€EUR]+', '', description).strip()

        # Determine debit/credit: look for explicit columns or sign
        debit = None
        credit = None
        raw_amounts = [clean_amount(a) for a in amounts if clean_amount(a) is not None]

        if len(raw_amounts) == 1:
            val = raw_amounts[0]
            # Negative = debit, positive = credit (or vice versa depending on bank)
            if val < 0:
                debit = abs(val)
            else:
                credit = val
        elif len(raw_amounts) >= 2:
            # Common pattern: debit in one column, credit in another
            # Heuristic: if two amounts, first is debit second is credit (or vice versa)
            # We keep them both and let the analyzer decide
            debit = raw_amounts[0] if raw_amounts[0] > 0 else None
            credit = raw_amounts[-1] if len(raw_amounts) > 1 else None

        if not description:
            description = f"Transaction page {page_num}"

        transactions.append({
            'date': found_date,
            'description': description[:200],
            'debit': debit,
            'credit': credit,
            'amount': -(debit or 0) + (credit or 0),
            'page': page_num,
            'raw_line': line[:300],
        })

    return transactions


def extract_with_tables(pdf_path: str) -> list[dict]:
    """
    Try table extraction first — most reliable for structured bank statements.
    Falls back to text extraction if tables are not found.
    """
    all_transactions = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            used_table = False

            for table in tables:
                if not table or len(table) < 2:
                    continue
                # Detect header row
                header = [str(c).lower().strip() if c else '' for c in table[0]]
                has_date = any('date' in h or 'jour' in h for h in header)
                has_amount = any(
                    kw in h for h in header
                    for kw in ('mont', 'débit', 'crédit', 'debit', 'credit', 'solde', 'amount')
                )
                if not (has_date or has_amount):
                    continue

                # Map columns
                col_date = next((i for i, h in enumerate(header) if 'date' in h or 'jour' in h), None)
                col_desc = next(
                    (i for i, h in enumerate(header) if any(k in h for k in ('libellé', 'libelle', 'motif', 'detail', 'opération', 'description', 'label'))),
                    None
                )
                col_debit = next((i for i, h in enumerate(header) if 'débit' in h or 'debit' in h or 'sortie' in h), None)
                col_credit = next((i for i, h in enumerate(header) if 'crédit' in h or 'credit' in h or 'entrée' in h), None)
                col_amount = next(
                    (i for i, h in enumerate(header) if 'mont' in h or 'amount' in h or 'valeur' in h),
                    None
                ) if col_debit is None and col_credit is None else None

                for row in table[1:]:
                    if not row:
                        continue
                    safe = lambda i: str(row[i]).strip() if i is not None and i < len(row) and row[i] else ''

                    raw_date = safe(col_date) if col_date is not None else ''
                    if not raw_date or raw_date.lower() in ('', 'none', '-'):
                        continue

                    found_date = parse_date(raw_date)
                    if not found_date:
                        continue

                    description = safe(col_desc) if col_desc is not None else ' '.join(
                        safe(i) for i in range(len(row)) if i != col_date
                    )

                    debit_raw = safe(col_debit) if col_debit is not None else ''
                    credit_raw = safe(col_credit) if col_credit is not None else ''
                    amount_raw = safe(col_amount) if col_amount is not None else ''

                    debit = clean_amount(debit_raw) if debit_raw else None
                    credit = clean_amount(credit_raw) if credit_raw else None

                    if debit is None and credit is None and amount_raw:
                        val = clean_amount(amount_raw)
                        if val is not None:
                            if val < 0:
                                debit = abs(val)
                            else:
                                credit = val

                    if debit is None and credit is None:
                        continue

                    amount = -(debit or 0) + (credit or 0)

                    all_transactions.append({
                        'date': found_date,
                        'description': description[:200],
                        'debit': debit,
                        'credit': credit,
                        'amount': amount,
                        'page': page_num,
                        'raw_line': ' | '.join(safe(i) for i in range(len(row)))[:300],
                    })
                    used_table = True

            # If no tables found on this page, fallback to text extraction
            if not used_table:
                text = page.extract_text() or ''
                fallback = extract_transactions_from_text(text, page_num)
                all_transactions.extend(fallback)

    return all_transactions


def extract_pdf(uploaded_file) -> pd.DataFrame:
    """
    Main entry point. Accepts a Streamlit UploadedFile or a file path string.
    Returns a cleaned DataFrame of transactions.
    """
    if isinstance(uploaded_file, str):
        transactions = extract_with_tables(uploaded_file)
    else:
        # Save to temp buffer for pdfplumber
        import tempfile, os
        suffix = '.pdf'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name
        try:
            transactions = extract_with_tables(tmp_path)
        finally:
            os.unlink(tmp_path)

    if not transactions:
        return pd.DataFrame()

    df = pd.DataFrame(transactions)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['id'] = df.index + 1
    return df
