"""
PDFs de test pour les banques belges restantes :
CBC, Crelan, Argenta, Beobank, AXA Bank, Hello bank!, Deutsche Bank BE, Triodos BE

Sources formats :
- CBC = division wallonne de KBC, format identique mais entièrement en français
- Crelan = banque coopérative, format simple 5 colonnes
- Argenta = banque d'épargne, format NL + FR
- Beobank = ex-Citibank Belgium, format international
- AXA Bank = format classique FR/NL
- Hello bank! = filiale BNP Paribas Fortis, format numérique simplifié
- Deutsche Bank BE = format international avec IBAN explicite
- Triodos = banque éthique, format minimaliste
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
import os

OUTPUT_DIR = os.path.dirname(__file__)


def make_cbc_pdf():
    """CBC Banque (division wallonne de KBC) — format identique KBC mais 100% français"""
    path = os.path.join(OUTPUT_DIR, "test_cbc.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>CBC Banque SA</b>", styles['Title']))
    elems.append(Paragraph("Extrait de compte mensuel", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Titulaire : BRASSERIE DU SUD SRL", styles['Normal']))
    elems.append(Paragraph("Compte : BE45 7310 0000 0321", styles['Normal']))
    elems.append(Paragraph("Période : 01/05/2024 - 31/05/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    header = ['Date valeur', 'Date comptable', 'Libellé de l\'opération', 'Débit (EUR)', 'Crédit (EUR)']
    data = [header] + [
        ['02/05/2024', '02/05/2024', 'Recettes CB semaine 1 - Terminal Bancontact', '', '3.250,00'],
        ['06/05/2024', '06/05/2024', 'Paiement METRO CASH & CARRY ANDERLECHT', '1.120,00', ''],
        ['07/05/2024', '07/05/2024', 'Domiciliation PROXIMUS SA - fact. 05/2024', '89,45', ''],
        ['10/05/2024', '10/05/2024', 'Recettes CB semaine 2 - Terminal Bancontact', '', '2.980,00'],
        ['13/05/2024', '13/05/2024', 'Loyer mai 2024 - SCI DUBOIS ET FILS', '2.800,00', ''],
        ['15/05/2024', '15/05/2024', 'Salaire JEAN-PIERRE MARTIN - mai 2024', '1.750,00', ''],
        ['15/05/2024', '15/05/2024', 'Salaire NATHALIE SIMON - mai 2024', '1.620,00', ''],
        ['17/05/2024', '17/05/2024', 'Recettes CB semaine 3 - Terminal Bancontact', '', '3.100,00'],
        ['20/05/2024', '20/05/2024', 'COLRUYT CENTRE DISTRIBUTION Hal', '605,00', ''],
        ['22/05/2024', '22/05/2024', 'ENGIE ELECTRABEL PRO - contrat 2024', '340,00', ''],
        ['24/05/2024', '24/05/2024', 'Recettes CB semaine 4 - Terminal Bancontact', '', '2.870,00'],
        ['27/05/2024', '27/05/2024', 'Paiement METRO CASH & CARRY ANDERLECHT', '1.080,00', ''],
        ['31/05/2024', '31/05/2024', 'CBC ASSURANCES - prime mensuelle 05/2024', '195,00', ''],
    ]
    t = Table(data, colWidths=[2.5*cm, 2.8*cm, 7.5*cm, 2.5*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#004B87')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#EEF2F7')]),
        ('ALIGN', (3,1),(4,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_crelan_pdf():
    """Crelan — banque coopérative belge, format simple, populaire PME agricoles et HORECA"""
    path = os.path.join(OUTPUT_DIR, "test_crelan.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>Crelan SA</b>", styles['Title']))
    elems.append(Paragraph("Extrait de compte / Rekeninguitreksel", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Client : CHARCUTERIE ARTISANALE DUPONT", styles['Normal']))
    elems.append(Paragraph("Compte : BE56 1030 0000 0456", styles['Normal']))
    elems.append(Paragraph("Du 01/06/2024 au 30/06/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    header = ['Date', 'Description de l\'opération / Omschrijving', 'Débit', 'Crédit', 'Solde']
    data = [header] + [
        ['03/06/2024', 'Recettes semaine 1 Bancontact / Payconiq', '', '2.940,00', '11.940,00'],
        ['05/06/2024', 'SODIPRO SA - fournisseur charcuterie F2024-06-01', '780,00', '', '11.160,00'],
        ['07/06/2024', 'PROXIMUS SA domiciliation mensuelle', '89,45', '', '11.070,55'],
        ['10/06/2024', 'Recettes semaine 2 Bancontact / Payconiq', '', '3.020,00', '14.090,55'],
        ['12/06/2024', 'Loyer local commercial juin 2024', '1.800,00', '', '12.290,55'],
        ['14/06/2024', 'METRO CASH AND CARRY approvisionnement', '1.050,00', '', '11.240,55'],
        ['15/06/2024', 'Salaire Pierre DUBOIS juin 2024', '1.750,00', '', '9.490,55'],
        ['15/06/2024', 'Salaire Isabelle MARTIN juin 2024', '1.620,00', '', '7.870,55'],
        ['17/06/2024', 'Recettes semaine 3 Bancontact / Payconiq', '', '2.860,00', '10.730,55'],
        ['19/06/2024', 'CRELAN ASSURANCES prime annuelle proratisée', '210,00', '', '10.520,55'],
        ['24/06/2024', 'Recettes semaine 4 Bancontact / Payconiq', '', '3.110,00', '13.630,55'],
        ['26/06/2024', 'BELGACOM TELENET domiciliation', '55,00', '', '13.575,55'],
        ['28/06/2024', 'SODIPRO SA - fournisseur charcuterie F2024-06-02', '820,00', '', '12.755,55'],
    ]
    t = Table(data, colWidths=[2.2*cm, 9*cm, 2.2*cm, 2.2*cm, 2.7*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#6CB33F')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#F4FAF0')]),
        ('ALIGN', (2,1),(4,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_argenta_pdf():
    """Argenta — banque d'épargne belge, populaire en Flandre, bilingue NL/FR"""
    path = os.path.join(OUTPUT_DIR, "test_argenta.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>Argenta Spaarbank NV</b>", styles['Title']))
    elems.append(Paragraph("Rekeningafschrift / Extrait de compte", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Rekeninghouder / Titulaire : BAKKERIJ DE GOUDEN KORST BVBA", styles['Normal']))
    elems.append(Paragraph("IBAN : BE78 9793 0000 0123", styles['Normal']))
    elems.append(Paragraph("Periode / Période : 01/07/2024 - 31/07/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    # Argenta utilise NL en premier : Datum | Omschrijving | Debet | Credit | Saldo
    header = ['Datum', 'Omschrijving / Description', 'Debet (EUR)', 'Credit (EUR)', 'Saldo (EUR)']
    data = [header] + [
        ['02/07/2024', 'Bancontact ontvangst week 1 / Recettes CB semaine 1', '', '2.780,00', '14.780,00'],
        ['04/07/2024', 'METRO CASH AND CARRY betaling / paiement', '1.090,00', '', '13.690,00'],
        ['05/07/2024', 'PROXIMUS maandelijkse domiciliering', '89,45', '', '13.600,55'],
        ['08/07/2024', 'Bancontact ontvangst week 2', '', '3.050,00', '16.650,55'],
        ['10/07/2024', 'HUUR JULI 2024 / LOYER JUILLET 2024', '1.900,00', '', '14.750,55'],
        ['15/07/2024', 'Loon / Salaire THOMAS VAN DEN BERG', '1.750,00', '', '13.000,55'],
        ['15/07/2024', 'Loon / Salaire ELISE PEETERS', '1.620,00', '', '11.380,55'],
        ['16/07/2024', 'Bancontact ontvangst week 3', '', '2.920,00', '14.300,55'],
        ['19/07/2024', 'COLRUYT NIJVERHEIDSSTRAAT aankoop', '560,00', '', '13.740,55'],
        ['22/07/2024', 'ENGIE GAS EN ELEKTRICITEIT PRO', '295,00', '', '13.445,55'],
        ['23/07/2024', 'Bancontact ontvangst week 4', '', '3.180,00', '16.625,55'],
        ['26/07/2024', 'METRO CASH AND CARRY betaling', '980,00', '', '15.645,55'],
        ['31/07/2024', 'ARGENTA VERZEKERING brandpolis 07/2024', '145,00', '', '15.500,55'],
    ]
    t = Table(data, colWidths=[2.3*cm, 8.5*cm, 2.5*cm, 2.5*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#E30613')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#FFF5F5')]),
        ('ALIGN', (2,1),(4,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_beobank_pdf():
    """Beobank (ex-Citibank Belgium) — format international en anglais ET français"""
    path = os.path.join(OUTPUT_DIR, "test_beobank.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>Beobank NV/SA</b>", styles['Title']))
    elems.append(Paragraph("Account Statement / Relevé de compte", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Account holder / Titulaire : PIZZA NAPOLI SRL", styles['Normal']))
    elems.append(Paragraph("Account / Compte : BE12 5230 0000 0789", styles['Normal']))
    elems.append(Paragraph("Period : 01/08/2024 to 31/08/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    # Beobank : format international, montant avec signe dans une colonne unique
    header = ['Value date', 'Transaction date', 'Description', 'Amount (EUR)', 'Balance (EUR)']
    data = [header] + [
        ['01/08/2024', '01/08/2024', 'Card payments received / Recettes CB', '+3.120,00', '15.120,00'],
        ['05/08/2024', '05/08/2024', 'Payment to METRO CASH CARRY', '-1.150,00', '13.970,00'],
        ['06/08/2024', '06/08/2024', 'Standing order PROXIMUS SA', '-89,45', '13.880,55'],
        ['09/08/2024', '09/08/2024', 'Card payments received / Recettes CB', '+2.890,00', '16.770,55'],
        ['12/08/2024', '12/08/2024', 'Commercial rent August 2024 / Loyer août', '-2.800,00', '13.970,55'],
        ['15/08/2024', '15/08/2024', 'Salary ROBERTO ESPOSITO August 2024', '-1.750,00', '12.220,55'],
        ['15/08/2024', '15/08/2024', 'Salary LUCIA FERRANTE August 2024', '-1.620,00', '10.600,55'],
        ['16/08/2024', '16/08/2024', 'Card payments received / Recettes CB', '+3.210,00', '13.810,55'],
        ['20/08/2024', '20/08/2024', 'SODEXO MEAL VOUCHERS professional', '-280,00', '13.530,55'],
        ['21/08/2024', '21/08/2024', 'ENGIE ELECTRABEL pro August 2024', '-360,00', '13.170,55'],
        ['23/08/2024', '23/08/2024', 'Card payments received / Recettes CB', '+2.950,00', '16.120,55'],
        ['27/08/2024', '27/08/2024', 'Payment COLRUYT NV purchase', '-570,00', '15.550,55'],
        ['30/08/2024', '30/08/2024', 'BEOBANK insurance premium / prime assurance', '-195,00', '15.355,55'],
    ]
    t = Table(data, colWidths=[2.5*cm, 2.8*cm, 7.5*cm, 2.7*cm, 2.7*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#1A1A1A')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('ALIGN', (3,1),(4,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_axa_bank_pdf():
    """AXA Bank Belgium — format FR/NL, colonne montant unique avec signe"""
    path = os.path.join(OUTPUT_DIR, "test_axa_bank.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>AXA Bank Belgium SA/NV</b>", styles['Title']))
    elems.append(Paragraph("Extrait de compte professionnel", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Titulaire : LE PETIT GASTRO SPRL", styles['Normal']))
    elems.append(Paragraph("Compte : BE89 7543 0000 0654", styles['Normal']))
    elems.append(Paragraph("Période : 01/09/2024 — 30/09/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    header = ['Date', 'Détail de l\'opération', 'Montant EUR', 'Solde EUR']
    data = [header] + [
        ['03/09/2024', 'Recettes Bancontact semaine 1', '+3.080,00', '18.080,00'],
        ['05/09/2024', 'Facture METRO CASH CARRY - alim. F09-001', '-1.130,00', '16.950,00'],
        ['06/09/2024', 'Prélèvement PROXIMUS SA télécommunications', '-89,45', '16.860,55'],
        ['09/09/2024', 'Recettes Bancontact semaine 2', '+2.910,00', '19.770,55'],
        ['11/09/2024', 'Virement loyer commercial septembre 2024', '-2.800,00', '16.970,55'],
        ['15/09/2024', 'Salaire Karim HASSAN septembre 2024', '-1.750,00', '15.220,55'],
        ['15/09/2024', 'Salaire Sophie LAMBERT septembre 2024', '-1.620,00', '13.600,55'],
        ['16/09/2024', 'Recettes Bancontact semaine 3', '+3.050,00', '16.650,55'],
        ['19/09/2024', 'COLRUYT DISTRIBUTION marchandises', '-595,00', '16.055,55'],
        ['23/09/2024', 'Recettes Bancontact semaine 4', '+2.970,00', '19.025,55'],
        ['24/09/2024', 'ENGIE pro - énergie septembre 2024', '-325,00', '18.700,55'],
        ['26/09/2024', 'METRO CASH CARRY - alim. F09-002', '-1.060,00', '17.640,55'],
        ['30/09/2024', 'AXA ASSURANCES prime mensuelle', '-175,00', '17.465,55'],
    ]
    t = Table(data, colWidths=[2.3*cm, 10*cm, 2.8*cm, 3*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#CC0000')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#FFF0F0')]),
        ('ALIGN', (2,1),(3,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_hello_bank_pdf():
    """Hello bank! (filiale 100% numérique de BNP Paribas Fortis) — format simplifié"""
    path = os.path.join(OUTPUT_DIR, "test_hello_bank.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>Hello bank! — BNP Paribas Fortis SA/NV</b>", styles['Title']))
    elems.append(Paragraph("Relevé de transactions / Transactieoverzicht", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Titulaire : FOOD TRUCK BRUXELLES SRL", styles['Normal']))
    elems.append(Paragraph("IBAN : BE53 0634 0000 0321", styles['Normal']))
    elems.append(Paragraph("Du 01/10/2024 au 31/10/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    # Hello bank format : très simplifié, montant avec signe, pas de colonne solde séparée
    header = ['Date', 'Bénéficiaire / Donneur d\'ordre', 'Communication', 'Montant', 'Solde après']
    data = [header] + [
        ['01/10/2024', 'Recettes journée CB', 'Payconiq + Bancontact', '+1.850,00', '16.850,00'],
        ['04/10/2024', 'METRO CASH AND CARRY', 'Approvisionnement semaine 1', '-950,00', '15.900,00'],
        ['05/10/2024', 'PROXIMUS SA', 'Abonnement pro octobre', '-89,45', '15.810,55'],
        ['07/10/2024', 'Recettes journée CB', 'Payconiq + Bancontact', '+2.100,00', '17.910,55'],
        ['10/10/2024', 'SCI INVESTIMMO', 'Loyer emplacement food truck oct.', '-1.200,00', '16.710,55'],
        ['11/10/2024', 'Recettes journée CB', 'Payconiq + Bancontact', '+1.980,00', '18.690,55'],
        ['15/10/2024', 'IBRAHIM KONÉ', 'Salaire octobre 2024', '-1.750,00', '16.940,55'],
        ['15/10/2024', 'FATOU DIALLO', 'Salaire octobre 2024', '-1.620,00', '15.320,55'],
        ['18/10/2024', 'Recettes journée CB', 'Payconiq + Bancontact', '+2.250,00', '17.570,55'],
        ['21/10/2024', 'COLRUYT NV', 'Achats matières premières', '-520,00', '17.050,55'],
        ['22/10/2024', 'ENGIE SA', 'Energie octobre 2024', '-290,00', '16.760,55'],
        ['25/10/2024', 'Recettes journée CB', 'Payconiq + Bancontact', '+1.920,00', '18.680,55'],
        ['28/10/2024', 'BNP PARIBAS FORTIS', 'Assurance professionnelle', '-145,00', '18.535,55'],
    ]
    t = Table(data, colWidths=[2.2*cm, 5*cm, 5.5*cm, 2.5*cm, 2.8*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#00A651')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#F0FFF8')]),
        ('ALIGN', (3,1),(4,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_deutsche_bank_be_pdf():
    """Deutsche Bank Belgium — format international, en anglais, IBAN explicite"""
    path = os.path.join(OUTPUT_DIR, "test_deutsche_bank_be.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>Deutsche Bank AG — Belgium Branch</b>", styles['Title']))
    elems.append(Paragraph("Account Statement", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Account holder : IMPORT EXPORT BRUSSELS SA", styles['Normal']))
    elems.append(Paragraph("IBAN : BE71 1900 0000 0456   BIC : DEUTBEBBXXX", styles['Normal']))
    elems.append(Paragraph("Statement period : 01/11/2024 to 30/11/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    header = ['Booking date', 'Value date', 'Description', 'Debit EUR', 'Credit EUR']
    data = [header] + [
        ['04/11/2024', '04/11/2024', 'Transfer received — Client invoices November W1', '', '4.200,00'],
        ['05/11/2024', '05/11/2024', 'Supplier payment METRO CASH CARRY', '1.250,00', ''],
        ['06/11/2024', '06/11/2024', 'Direct debit PROXIMUS SA November', '89,45', ''],
        ['08/11/2024', '08/11/2024', 'Transfer received — Client invoices November W2', '', '3.800,00'],
        ['12/11/2024', '12/11/2024', 'Commercial rent November 2024', '2.800,00', ''],
        ['15/11/2024', '15/11/2024', 'Salary payment MARC JANSSENS', '1.750,00', ''],
        ['15/11/2024', '15/11/2024', 'Salary payment ANNE LEBLANC', '1.620,00', ''],
        ['18/11/2024', '18/11/2024', 'Transfer received — Client invoices November W3', '', '5.100,00'],
        ['20/11/2024', '20/11/2024', 'Supplier payment COLRUYT NV', '610,00', ''],
        ['22/11/2024', '22/11/2024', 'ENGIE Belgium energy professional', '375,00', ''],
        ['26/11/2024', '26/11/2024', 'Transfer received — Client invoices November W4', '', '3.650,00'],
        ['28/11/2024', '28/11/2024', 'Deutsche Bank insurance premium', '195,00', ''],
        ['29/11/2024', '29/11/2024', 'Supplier payment METRO CASH CARRY', '1.100,00', ''],
    ]
    t = Table(data, colWidths=[2.7*cm, 2.5*cm, 7.5*cm, 2.5*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#003366')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#EEF0F5')]),
        ('ALIGN', (3,1),(4,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


def make_triodos_pdf():
    """Triodos Bank Belgium — banque éthique, format minimaliste NL/FR"""
    path = os.path.join(OUTPUT_DIR, "test_triodos.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("<b>Triodos Bank NV/SA</b>", styles['Title']))
    elems.append(Paragraph("Rekeningoverzicht / Aperçu du compte", styles['Normal']))
    elems.append(Spacer(1, 0.3*cm))
    elems.append(Paragraph("Rekeninghouder : BIO RESTO NAMUR SCRL", styles['Normal']))
    elems.append(Paragraph("IBAN : BE58 5230 8000 0321", styles['Normal']))
    elems.append(Paragraph("Periode : 01/12/2024 - 31/12/2024", styles['Normal']))
    elems.append(Spacer(1, 0.4*cm))

    header = ['Datum / Date', 'Omschrijving / Libellé', 'Bedrag / Montant EUR', 'Saldo / Solde EUR']
    data = [header] + [
        ['02/12/2024', 'Betaling ontvangen / Recettes Bancontact semaine 1', '+2.650,00', '12.650,00'],
        ['04/12/2024', 'BIOFRESH GROSSISTE aankoop / achat', '-890,00', '11.760,00'],
        ['06/12/2024', 'PROXIMUS domiciliering / domiciliation', '-89,45', '11.670,55'],
        ['09/12/2024', 'Betaling ontvangen / Recettes Bancontact semaine 2', '+2.980,00', '14.650,55'],
        ['11/12/2024', 'HUUR / LOYER december / décembre 2024', '-1.500,00', '13.150,55'],
        ['15/12/2024', 'Loon / Salaire Arnaud COLLIN dec. 2024', '-1.750,00', '11.400,55'],
        ['15/12/2024', 'Loon / Salaire Claire RENARD dec. 2024', '-1.620,00', '9.780,55'],
        ['16/12/2024', 'Betaling ontvangen / Recettes Bancontact semaine 3', '+3.100,00', '12.880,55'],
        ['18/12/2024', 'BIOFRESH GROSSISTE aankoop / achat', '-780,00', '12.100,55'],
        ['20/12/2024', 'ENGIE ELECTRABEL groene energie pro', '-260,00', '11.840,55'],
        ['23/12/2024', 'Betaling ontvangen / Recettes Bancontact semaine 4', '+3.850,00', '15.690,55'],
        ['27/12/2024', 'TRIODOS VERZEKERING / ASSURANCE pro', '-165,00', '15.525,55'],
        ['31/12/2024', '13e maand / 13e mois COLLIN Arnaud', '-1.750,00', '13.775,55'],
    ]
    t = Table(data, colWidths=[2.5*cm, 9*cm, 3.2*cm, 3*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0), colors.HexColor('#5B9B4A')),
        ('TEXTCOLOR', (0,0),(-1,0), colors.white),
        ('FONTNAME', (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('GRID', (0,0),(-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, colors.HexColor('#F2F9F0')]),
        ('ALIGN', (2,1),(3,-1), 'RIGHT'),
    ]))
    elems.append(t)
    doc.build(elems)
    return path


if __name__ == '__main__':
    for fn in [make_cbc_pdf, make_crelan_pdf, make_argenta_pdf,
               make_beobank_pdf, make_axa_bank_pdf, make_hello_bank_pdf,
               make_deutsche_bank_be_pdf, make_triodos_pdf]:
        path = fn()
        print(f"✅ {path}")
