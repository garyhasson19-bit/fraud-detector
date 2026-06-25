"""
Génère des PDFs de test qui imitent exactement les formats réels
des banques belges : Belfius, KBC, BNP Paribas Fortis, ING Belgique.

Formats basés sur :
- Documentation officielle Belfius (colonnes : Date / Description / Débit / Crédit / Solde)
- Captures d'écran publiques KBC Brussels Mobile
- Format BNP Paribas Fortis bilingue (FR/NL)
- Format ING Belgique (Contrepartie / Libellé / Montant / Solde)
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
import os

OUTPUT_DIR = os.path.dirname(__file__)


def make_belfius_pdf():
    """Format Belfius : 5 colonnes Date | Libellé des opérations | Débit | Crédit | Solde"""
    path = os.path.join(OUTPUT_DIR, "test_belfius.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []

    # En-tête Belfius
    elems.append(Paragraph("<b>Belfius Banque SA</b>", styles['Title']))
    elems.append(Paragraph("Extrait de compte / Rekeninguitreksel", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Titulaire : DUPONT RESTAURANT SPRL", styles['Normal']))
    elems.append(Paragraph("Compte : BE32 0689 0000 0057", styles['Normal']))
    elems.append(Paragraph("Période : 01/01/2024 - 31/01/2024", styles['Normal']))
    elems.append(Spacer(1, 0.5*cm))

    # Tableau — format exact Belfius
    header = ['Date', 'Libellé des opérations', 'Débit', 'Crédit', 'Solde']
    data = [header] + [
        ['01/01/2024', 'Solde initial', '', '', '5.234,18'],
        ['03/01/2024', 'Virement reçu - Recettes CB janvier S1', '', '3.420,00', '8.654,18'],
        ['04/01/2024', 'Paiement facture METRO CASH CARRY ANDERLECHT', '1.150,00', '', '7.504,18'],
        ['05/01/2024', 'Domiciliation PROXIMUS BE - fact. 2024/001', '89,45', '', '7.414,73'],
        ['07/01/2024', 'Virement reçu - Recettes CB janvier S2', '', '2.890,00', '10.304,73'],
        ['08/01/2024', 'Paiement METRO CASH CARRY ANDERLECHT', '980,00', '', '9.324,73'],
        ['10/01/2024', 'LOYER JANVIER 2024 - M. PEETERS IMMO', '2.800,00', '', '6.524,73'],
        ['12/01/2024', 'KBC VERZEKERING - Police 887234', '145,00', '', '6.379,73'],
        ['15/01/2024', 'Virement - SALAIRE AHMED BAKR - janvier 2024', '1.750,00', '', '4.629,73'],
        ['15/01/2024', 'Virement - SALAIRE MARIE DUPONT - janvier 2024', '1.620,00', '', '3.009,73'],
        ['17/01/2024', 'Virement reçu - Recettes CB janvier S3', '', '3.100,00', '6.109,73'],
        ['18/01/2024', 'Facture COLRUYT GROSSISTE Hal - F2024-0118', '620,00', '', '5.489,73'],
        ['20/01/2024', 'Domiciliation ENGIE ELECTRABEL PRO', '312,00', '', '5.177,73'],
        ['22/01/2024', 'Virement reçu - Recettes CB janvier S4', '', '2.750,00', '7.927,73'],
        ['25/01/2024', 'Paiement METRO CASH CARRY ANDERLECHT', '1.050,00', '', '6.877,73'],
        ['28/01/2024', 'BELFIUS ASSURANCE INCENDIE PRO', '210,00', '', '6.667,73'],
        ['31/01/2024', 'Solde final', '', '', '6.667,73'],
    ]

    t = Table(data, colWidths=[2.5*cm, 9*cm, 2.5*cm, 2.5*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003D7C')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('ALIGN', (2, 1), (4, -1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_kbc_pdf():
    """Format KBC : Date valeur | Date comptable | Description | Montant | Solde"""
    path = os.path.join(OUTPUT_DIR, "test_kbc.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []

    elems.append(Paragraph("<b>KBC Bank NV</b>", styles['Title']))
    elems.append(Paragraph("Rekeninguitreksel / Relevé de compte", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Rekeninghouder: LA MAISON BELGE BVBA", styles['Normal']))
    elems.append(Paragraph("Rekening: BE96 7340 0000 0654", styles['Normal']))
    elems.append(Paragraph("Periode: 01/02/2024 - 29/02/2024", styles['Normal']))
    elems.append(Spacer(1, 0.5*cm))

    # KBC a 2 dates : date valeur + date comptable
    header = ['Datum waarde', 'Boekingsdatum', 'Omschrijving', 'Bedrag (EUR)', 'Saldo (EUR)']
    data = [header] + [
        ['01/02/2024', '01/02/2024', 'Beginsaldo', '', '6.667,73'],
        ['02/02/2024', '02/02/2024', 'Overschrijving ontvangen - Opbrengsten CB week 1', '', '3.210,00'],
        ['05/02/2024', '05/02/2024', 'Betaling METRO CASH AND CARRY ANDERLECHT', '-1.200,00', '8.677,73'],
        ['06/02/2024', '06/02/2024', 'Domiciliering PROXIMUS NV rekeningnr 2024-02', '-89,45', '8.588,28'],
        ['09/02/2024', '09/02/2024', 'Overschrijving ontvangen - Opbrengsten CB week 2', '+2.980,00', '11.568,28'],
        ['12/02/2024', '12/02/2024', 'Betaling COLRUYT GROOT MARKT Ninove', '-590,00', '10.978,28'],
        ['14/02/2024', '14/02/2024', 'HUUR FEBRUARI 2024 - IMMO JANSSEN NV', '-2.800,00', '8.178,28'],
        ['15/02/2024', '15/02/2024', 'Loon AHMED BAKR - februari 2024', '-1.750,00', '6.428,28'],
        ['15/02/2024', '15/02/2024', 'Loon MARIE DUPONT - februari 2024', '-1.620,00', '4.808,28'],
        ['17/02/2024', '17/02/2024', 'Overschrijving ontvangen - Opbrengsten CB week 3', '+3.050,00', '7.858,28'],
        ['20/02/2024', '20/02/2024', 'KBC VERZEKERING brandverzekering 887234', '-210,00', '7.648,28'],
        ['22/02/2024', '22/02/2024', 'ENGIE ELECTRABEL PRO - factuur 2024-02', '-380,00', '7.268,28'],
        ['25/02/2024', '25/02/2024', 'Betaling METRO CASH AND CARRY ANDERLECHT', '-1.100,00', '6.168,28'],
        ['29/02/2024', '29/02/2024', 'Overschrijving ontvangen - Opbrengsten CB week 4', '+2.800,00', '8.968,28'],
        ['29/02/2024', '29/02/2024', 'Eindsaldo', '', '8.968,28'],
    ]

    t = Table(data, colWidths=[2.5*cm, 2.8*cm, 7*cm, 2.7*cm, 2.7*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00A0DE')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#EEF7FD')]),
        ('ALIGN', (3, 1), (4, -1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_bnp_fortis_pdf():
    """Format BNP Paribas Fortis : bilingue FR/NL, Communication structurée belge +++"""
    path = os.path.join(OUTPUT_DIR, "test_bnp_fortis.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []

    elems.append(Paragraph("<b>BNP Paribas Fortis SA/NV</b>", styles['Title']))
    elems.append(Paragraph("Extrait de compte / Rekeningafschrift N° 003/2024", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Titulaire / Rekeninghouder : RESTAURANT AUX AROMES SPRL", styles['Normal']))
    elems.append(Paragraph("Compte / Rekening : BE43 0012 3456 7891", styles['Normal']))
    elems.append(Paragraph("Du / Van : 01/03/2024   Au / Tot : 31/03/2024", styles['Normal']))
    elems.append(Spacer(1, 0.5*cm))

    # BNP Fortis : format avec communication structurée +++ xxx/xxxx/xxxxx +++
    header = ['Date', 'Date valeur', 'Description / Omschrijving', 'Débit / Debet', 'Crédit / Credit']
    data = [header] + [
        ['01/03/2024', '01/03/2024', 'Solde reporté / Overgedragen saldo', '', '8.968,28'],
        ['04/03/2024', '04/03/2024', 'Virement reçu EASY BANKING\n+++123/4567/89012+++\nOpbrengsten CB', '', '3.340,00'],
        ['05/03/2024', '06/03/2024', 'Ordre permanent METRO CASH & CARRY\nBE14 3500 0000 1234\n+++234/5678/90123+++', '1.180,00', ''],
        ['07/03/2024', '07/03/2024', 'Domiciliation / Domiciliering PROXIMUS SA\n+++345/6789/01234+++', '89,45', ''],
        ['10/03/2024', '10/03/2024', 'Virement reçu / Ontvangen overschrijving\nRecettes semaine 2', '', '2.760,00'],
        ['12/03/2024', '12/03/2024', 'LOYER MARS 2024 / HUUR MAART 2024\nIMMOBELGIQUE SA  BE65 0017 8765 4321', '2.800,00', ''],
        ['15/03/2024', '15/03/2024', 'Virement salaire / Loonbetaling BAKR AHMED', '1.750,00', ''],
        ['15/03/2024', '15/03/2024', 'Virement salaire / Loonbetaling DUPONT MARIE', '1.620,00', ''],
        ['18/03/2024', '18/03/2024', 'Virement reçu - Recettes semaine 3', '', '3.180,00'],
        ['20/03/2024', '20/03/2024', 'COLRUYT NV HALLE - fact. 2024-03-20\n+++456/7890/12345+++', '605,00', ''],
        ['22/03/2024', '22/03/2024', 'ENGIE SA/NV Pro - Energie elektriciteit\n+++567/8901/23456+++', '398,00', ''],
        ['25/03/2024', '25/03/2024', 'Ordre METRO CASH & CARRY ANDERLECHT\n+++678/9012/34567+++', '990,00', ''],
        ['28/03/2024', '28/03/2024', 'Virement reçu - Recettes semaine 4\nBancontact + Payconiq', '', '2.920,00'],
        ['31/03/2024', '31/03/2024', 'Solde final / Eindsaldo', '', ''],
    ]

    t = Table(data, colWidths=[2*cm, 2.3*cm, 8.5*cm, 2.5*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#009640')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0FAF4')]),
        ('ALIGN', (3, 1), (4, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_ing_belgium_pdf():
    """Format ING Belgique : Date | Contrepartie | Libellé | Montant | Solde"""
    path = os.path.join(OUTPUT_DIR, "test_ing_belgium.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []

    elems.append(Paragraph("<b>ING Belgique SA/NV</b>", styles['Title']))
    elems.append(Paragraph("Aperçu du compte / Rekeningoverzicht", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Titulaire : FRITERIE DU CENTRE SRL", styles['Normal']))
    elems.append(Paragraph("IBAN : BE71 3100 0000 0785", styles['Normal']))
    elems.append(Paragraph("Période : 01/04/2024 — 30/04/2024", styles['Normal']))
    elems.append(Spacer(1, 0.5*cm))

    # ING : format avec colonne contrepartie séparée
    header = ['Date', 'Contrepartie / Tegenpartij', 'Libellé / Mededeling', 'Montant (€)', 'Solde (€)']
    data = [header] + [
        ['01/04/2024', '', 'Solde initial / Beginsaldo', '', '9.215,44'],
        ['02/04/2024', 'RECETTES CARTE BANCONTACT', 'TPE Terminal paiement avril S1', '+3.180,00', '12.395,44'],
        ['04/04/2024', 'METRO CASH AND CARRY BELGIUM', 'Facture 440-2024-0401 alimentation', '-1.090,00', '11.305,44'],
        ['05/04/2024', 'PROXIMUS SA/NV', 'Domiciliation telcom 04/2024', '-89,45', '11.215,99'],
        ['06/04/2024', 'RECETTES CARTE BANCONTACT', 'TPE Terminal paiement avril S2', '+2.840,00', '14.055,99'],
        ['09/04/2024', 'COLRUYT NV', 'Achat marchandises 09/04/2024', '-580,00', '13.475,99'],
        ['12/04/2024', 'IMMOBILIERE BRUXELLES SA', 'Loyer commercial avril 2024', '-2.800,00', '10.675,99'],
        ['15/04/2024', 'AHMED BAKR', 'Salaire avril 2024', '-1.750,00', '8.925,99'],
        ['15/04/2024', 'MARIE DUPONT', 'Salaire avril 2024', '-1.620,00', '7.305,99'],
        ['16/04/2024', 'RECETTES CARTE BANCONTACT', 'TPE Terminal paiement avril S3', '+3.020,00', '10.325,99'],
        ['18/04/2024', 'ENGIE ELECTRABEL SA/NV', 'Energie professionnelle 04/2024', '-360,00', '9.965,99'],
        ['20/04/2024', 'METRO CASH AND CARRY BELGIUM', 'Facture 440-2024-0420 alimentation', '-1.020,00', '8.945,99'],
        ['23/04/2024', 'RECETTES CARTE BANCONTACT', 'TPE Terminal paiement avril S4', '+2.950,00', '11.895,99'],
        ['25/04/2024', 'BELFIUS INSURANCE', 'Prime assurance responsabilite civile', '-195,00', '11.700,99'],
        ['30/04/2024', '', 'Solde final / Eindsaldo', '', '11.700,99'],
    ]

    t = Table(data, colWidths=[2.3*cm, 5*cm, 6*cm, 2.7*cm, 2.7*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FF6200')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FFF5EE')]),
        ('ALIGN', (3, 1), (4, -1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for fn in [make_belfius_pdf, make_kbc_pdf, make_bnp_fortis_pdf, make_ing_belgium_pdf]:
        path = fn()
        print(f"✅ Généré : {path}")
