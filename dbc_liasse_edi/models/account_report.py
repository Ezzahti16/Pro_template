# -*- coding: utf-8 -*-
import io
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from xml.sax.saxutils import escape

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Cahier des charges EDI SIMPL-IS 2.0.3, 4.1, prescribes this declaration
# literally. Note the DGI CHANGED it: version 1.0 of the cahier specified
# iso-8859-1, the current version specifies UTF-8 - which is also what the
# published sample file and the reference workbook emit. Declare and encode the
# same thing so the declaration is never a lie about the bytes.
_XML_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'
# The filed liasse is CRLF-terminated and spells the encoding name in
# lowercase. Both are free choices - XML compares an encoding name
# case-insensitively, and either line ending is legal - kept aligned with
# the filing so that diffing the two files shows only real content.
_XML_LINE_END = '\r\n'
_XML_ENCODING = 'utf-8'

# <modele><id>, as published by the DGI: 1 = Modele Comptable Normal
# (7 would be Comptable Simplifie, which this module does not produce - the
# liasse reports of dbc_liasse_fiscale are the Normal ones).
_MODELE_NORMAL_ID = '1'

# Simpl-IS rejects any upload above 1 Mo ("Fichier trop volumineux").
_MAX_FILE_BYTES = 1024 * 1024

# Same shape as the subformula regexes already used inside
# dbc_liasse_fiscale (MaFiscalEvaluator.__init__ and
# _report_custom_engine_ma_fiscal), merged so either spelling matches.
_EDI_SUBFORMULA_RE = re.compile(r'\s*(?:editable\s+)?edi[_ ](\S+?)\s*\Z')

# dbc_liasse_fiscale report xml_id -> DGI "tableau" id, for the Modele
# Comptable Normal, in the order the paper liasse presents them.
#
# Source: the `param` sheet of the DGI-oriented "Liasse ExcelToXml" workbook,
# cross-checked by comparing each tableau's published codeEdi set against the
# codes of ma.fiscal.setting. CAREFUL: ma.fiscal.setting.table_ is a DIFFERENT
# numbering inherited from the legacy software - Actif is table_ 1 but tableau
# 2, Passif is table_ 2 but tableau 1 - so this mapping cannot be derived from
# table_ and has to stay explicit.
#
# Tableau 201 (C6, "Etat des changements de methodes") is deliberately absent:
# dbc_liasse_fiscale has no report for it, and the cahier des charges allows a
# tableau to be left out entirely.
_REPORT_TABLEAU = {
    'bilan_actif_report': 2,            # Bilan (actif)
    'bilan_passif_report': 1,           # Bilan (passif)
    'bilan_cpc_report': 6,              # CPC
    'bilan_fisc_report': 7,             # T3  Passage resultat net comptable -> fiscal
    'bilan_immob_report': 11,           # T4  Immobilisations autres que financieres
    'bilan_esg_report': 32,             # T5  Etat des soldes de gestion
    'bilan_detail_cpc_report': 34,      # T6  Detail des postes du CPC
    'bilan_leasing_report': 23,         # T7  Biens en credit-bail
    'bilan_amort_report': 24,           # T8  Amortissements
    'bilan_prov_report': 37,            # T9  Provisions
    'bilan_cession_immob_report': 38,   # T10 Plus ou moins values sur cessions
    'bilan_titres_report': 39,          # T11 Titres de participation
    'bilan_tva15_report': 40,           # T12 Detail de la TVA
    'bilan_capital_report': 41,         # T13 Repartition du capital social
    'bilan_affect_report': 5,           # T14 Affectation des resultats
    'bilan_is_report': 240,             # T15 Calcul de l'impot
    'bilan_dotation_report': 12,        # T16 Dotations aux amortissements
    'bilan_fusion_report': 26,          # T17 Plus-values constatees (fusion)
    'bilan_loan_report': 27,            # T18 Interets des emprunts
    'bilan_rental_report': 28,          # T19 Locations et baux
    'bilan_stocks_report': 36,          # T20 Etat detaille des stocks
    'bilan_financement_report': 203,    # TF  Tableau de financement
    'bilan_devises_report': 200,        # C4  Operations en devises
    'bilan_derog_report': 202,          # A2  Etat des derogations
    'bilan_eval_report': 220,           # T220 Principales methodes d'evaluation
}

# Tableaux made of "sections illimitees" (cahier des charges 4.3.1.2): there
# the codeEdi designates a whole COLUMN and <numeroLigne> picks the row, so one
# editable Odoo record becomes one numeroLigne across all of that table's codes.
#
# {tableau_id: (model, [(extra_domain_leaf, [(field_name, codeEdi), ...]), ...])}
#
# The field order below is the official column order of each table; it already
# matches the order these models declare their fields in, column for column.
# `annee_fiscale` is never part of it - it only scopes which rows belong to the
# exercice being exported, it is not a liasse column.
#
# A tableau with several sections (27: Associes then Tiers) lists one block per
# section, and each block numbers its rows from 1 independently.
# Tableau 7's réintégrations/déductions blocks are four five-row ranges on
# the DGI grid (C_822 is 'T3'!$B$13:$B$17), each with its own label cell -
# the subtotals the paper form draws between them carry no codeEdi at all,
# which is why _INLINE_SUBTOTAL_CODES below keeps them out of the flat
# cells. One block per section, each numbering its rows from 1.
_ANNEX_TABLES = {
    7: ('ma.fiscal.passage.line', [
        (('section', '=', 'reint_courante'), [
            ('intitule', '821'),
            ('montant_plus', '822'),
            ('montant_moins', '823'),
        ]),
        (('section', '=', 'reint_non_courante'), [
            ('intitule', '824'),
            ('montant_plus', '825'),
            ('montant_moins', '826'),
        ]),
        (('section', '=', 'deduc_courante'), [
            ('intitule', '827'),
            ('montant_plus', '828'),
            ('montant_moins', '830'),
        ]),
        (('section', '=', 'deduc_non_courante'), [
            ('intitule', '829'),
            ('montant_plus', '831'),
            ('montant_moins', '7830'),
        ]),
    ]),
    240: ('ma.fiscal.is.line', [
        (('section', '=', 'ca_taux_reduit'), [
            ('intitule', '14990'),
            ('montant', '14353'),
        ]),
        (('section', '=', 'autres_produits'), [
            ('intitule', '14355'),
            ('montant', '14356'),
        ]),
    ]),
    12: ('ma.fiscal.dotation.line', [
        (None, [
            ('immobilisation', '10061'),
            ('date_entree', '1078'),
            ('prix_acquisition', '1079'),
            ('valeur_comptable', '1080'),
            ('amort_anterieurs', '1081'),
            ('taux', '1082'),
            ('duree', '1083'),
            ('amort_normal', '1084'),
            ('total_amort', '1085'),
            ('observations', '1086'),
        ]),
    ]),
    23: ('ma.fiscal.leasing.line', [
        (None, [
            ('rubrique', '1098'),
            ('date_premiere_echeance', '1099'),
            ('duree_contrat_mois', '1100'),
            ('valeur_estimee', '1101'),
            ('duree_theorique_amortissement', '1102'),
            ('cumul_redevances_anterieures', '1103'),
            ('montant_redevances_exercice', '1104'),
            ('redevances_restant_moins_un_an', '1105'),
            ('redevances_restant_plus_un_an', '1106'),
            ('prix_achat_residuel', '1107'),
            ('observations', '1108'),
        ]),
    ]),
    27: ('ma.fiscal.loan.line', [
        (('type', '=', 'associe'), [
            ('nom_prenom', '1208'),
            ('raison_sociale', '14969'),
            ('adresse', '1209'),
            ('num_if', '1210'),
            ('num_cin', '14968'),
            ('montant_pret', '1211'),
            ('date_pret', '1212'),
            ('duree_mois', '1213'),
            ('taux_interet', '1214'),
            ('charge_financiere_globale', '1215'),
            ('remb_anterieur_principal', '1216'),
            ('remb_anterieur_interet', '1217'),
            ('remb_actuel_principal', '1218'),
            ('remb_actuel_interet', '1219'),
            ('observations', '1220'),
        ]),
        (('type', '=', 'tiers'), [
            ('nom_prenom', '1221'),
            ('raison_sociale', '14970'),
            ('adresse', '1222'),
            ('num_if', '1223'),
            ('num_cin', '14971'),
            ('montant_pret', '1224'),
            ('date_pret', '1225'),
            ('duree_mois', '1226'),
            ('taux_interet', '1227'),
            ('charge_financiere_globale', '1228'),
            ('remb_anterieur_principal', '1229'),
            ('remb_anterieur_interet', '1250'),
            ('remb_actuel_principal', '1251'),
            ('remb_actuel_interet', '1252'),
            ('observations', '1253'),
        ]),
    ]),
    28: ('ma.fiscal.rental.line', [
        (None, [
            ('nature_bien', '1267'),
            ('lieu_situation', '1268'),
            ('nom_prenom', '1269'),
            ('raison_sociale', '14964'),
            ('adresse', '14965'),
            ('num_if', '14040'),
            ('num_cin', '14041'),
            ('num_ce', '17919'),
            ('date_conclusion', '1270'),
            ('montant_annuel', '1271'),
            ('montant_loyer_charges', '1272'),
            ('bail_ordinaire', '1273'),
            # Same source field: _X_MARKED_CODES decides which of the two
            # boxes the wording ticks, and the other one is left out.
            ('bail_ordinaire', '1274'),
        ]),
    ]),
    38: ('ma.fiscal.cession.line', [
        (None, [
            ('date_cession', '1929'),
            ('compte_principal', '1930'),
            ('montant_brut', '1931'),
            ('amort_cumules', '1932'),
            ('valeur_nette', '1933'),
            ('produit_cession', '1934'),
            ('plus_values', '1935'),
            ('moins_values', '1936'),
        ]),
    ]),
    39: ('ma.fiscal.titres.line', [
        (None, [
            ('raison_sociale', '2044'),
            ('num_if', '14065'),
            ('secteur_activite', '2045'),
            ('capital_social', '2046'),
            ('participation_capital', '2047'),
            ('prix_acquisition_global', '2048'),
            ('valeur_comptable_nette', '2049'),
            ('date_cloture', '2050'),
            ('situation_nette', '2051'),
            ('resultat_net', '2052'),
            ('produits_cpc', '2053'),
        ]),
    ]),
    41: ('ma.fiscal.capital.line', [
        (None, [
            ('nom_prenom', '2094'),
            ('raison_sociale', '17887'),
            ('num_if', '13536'),
            ('num_cin', '13537'),
            ('num_ce', '14560'),
            ('adresse', '2095'),
            ('titres_precedent', '2096'),
            ('titres_actuel', '2097'),
            ('valeur_nominale', '2098'),
            ('montant_souscrit', '2099'),
            ('montant_appele', '2100'),
            ('montant_libere', '2101'),
        ]),
    ]),
}

# Columns the DGI grid expects as a TICK rather than as the value stored in
# Odoo, as {codeEdi: predicate(raw value)} - the cell goes out as "X" when the
# predicate holds and is left out otherwise.
#
# Tableau 19's columns 12 and 13 ("Nature du contrat: Bail ordinaire" /
# "Leasing (Neme periode)") are two such boxes. The report shows them as the
# single free-text "Contrat" cell the paper liasse actually prints
# (BILAN.xlsx!Locations M9 = "Bail ordinaire"), so the text has to be turned
# back into the right tick here: the real filed liasse carries
# <codeEdi>1273</codeEdi><valeur>X</valeur> on each row, never the wording.
_X_MARKED_CODES = {
    '1273': lambda v: bool(v) and 'leasing' not in str(v).strip().lower(),
    '1274': lambda v: bool(v) and 'leasing' in str(v).strip().lower(),
}

# Column totals of the unlimited tables: {tableau_id: [(codeEdi, field)]}.
#
# These codes are the tableau's "Totaux" row. ma.fiscal.setting does define
# them, as EDIST('<row code>') - "sum of that column's detail rows" - but
# dbc_liasse_fiscale parses EDIST exactly like EDI (models/account_report.py,
# `if func in ('EDI', 'EDIST')`), so it resolves to a plain codeEdi lookup;
# since a row code such as 1079 has no formule of its own, the lookup misses
# and the total evaluates to 0. They are therefore summed here straight from
# the records, which is what EDIST means and what the reference workbook does
# (T16!C22 = SUM(C17:C21), and so on).
#
# A `None` field means the DGI publishes a total code for a column that cannot
# be summed (tableau 39 code 2060 sits above the "Date de cloture" column);
# ma.fiscal.setting gives it the constant 0, so 0 is what gets declared.
#
# Tableau 27's totals deliberately carry no type filter: its SUM ranges span
# rows 11..20, i.e. the Associes AND Tiers sections together, which is also
# how ma.fiscal.setting writes them - EDIST('1211')+EDIST('1224').
# The cells an inline-table report shows as the subtotal of a block. They
# exist on screen - tableau 7's feed 18009/18010/18012/18013, tableau 240's
# feed 14380 and 17929 - but NOT in the DGI grid, where the very same codes
# are the detail RANGES emitted by _ANNEX_TABLES above, each row carrying a
# numeroLigne. Exporting both would declare one code twice with two different
# meanings, so the flat cell is dropped and only the rows go out.
_INLINE_SUBTOTAL_CODES = frozenset({
    '822', '823', '825', '826', '828', '830', '831', '7830',   # tableau 7
    '14353', '14356',                                          # tableau 240
})

_ANNEX_TOTALS = {
    12: [
        ('1088', 'prix_acquisition'),
        ('1089', 'valeur_comptable'),
        ('1090', 'amort_anterieurs'),
        ('1093', 'amort_normal'),
        ('1094', 'total_amort'),
    ],
    27: [
        ('1257', 'montant_pret'),
        ('1261', 'charge_financiere_globale'),
        ('1262', 'remb_anterieur_principal'),
        ('1263', 'remb_anterieur_interet'),
        ('1264', 'remb_actuel_principal'),
        ('1265', 'remb_actuel_interet'),
    ],
    28: [
        ('1279', 'montant_annuel'),
        ('1280', 'montant_loyer_charges'),
    ],
    38: [
        # 2037 sits above the "Compte principal" column, which cannot be
        # summed; ma.fiscal.setting gives it the constant 0, like 2060.
        ('2037', None),
        ('2038', 'montant_brut'),
        ('2039', 'amort_cumules'),
        ('2040', 'valeur_nette'),
        ('2041', 'produit_cession'),
        ('2042', 'plus_values'),
        ('2043', 'moins_values'),
    ],
    39: [
        ('2056', 'capital_social'),
        ('2057', 'participation_capital'),
        ('2058', 'prix_acquisition_global'),
        ('2059', 'valeur_comptable_nette'),
        ('2060', None),
        ('2061', 'situation_nette'),
        ('2062', 'resultat_net'),
        ('2063', 'produits_cpc'),
    ],
}

# Non-tabular values attached to a tableau (cahier des charges 4.3.1.2),
# emitted under <extraFieldvaleurs> as <extraField><code>. Version 1.0 of the
# cahier printed that inner tag as <codeEDI> in its prose example, which was a
# typo (that same example also has three closing tags missing their slash);
# version 2.0.3 prints <code>, matching the published sample and the reference
# workbook.
#
# {tableau_id: [(code, spec)]} where spec is:
#   ('date_to',)          -> exercice closing date      ("Exercice clos le")
#   ('date_from',)        -> exercice opening date      ("du")
#   ('edi', code)         -> the computed value of another codeEdi
#   ('total', model, fld) -> sum of `fld` over that annex model's rows
_EXTRA_FIELDS = {
    12: [
        # "Montant global" mirrors the T16 totals row for the "amortissements
        # normaux de l'exercice" column (workbook: T16!J6 = H16 = H22).
        ('22', ('total', 'ma.fiscal.dotation.line', 'amort_normal')),
        ('50', ('date_to',)),
        ('51', ('date_from',)),
        ('52', ('date_to',)),
    ],
    23: [('53', ('date_to',))],
    26: [('54', ('date_to',))],
    27: [('55', ('date_to',))],
    34: [('70', ('date_to',))],
    37: [('71', ('date_to',))],
    38: [('72', ('date_to',))],
    # "Montant du capital" is the Capital social ou personnel of the Passif
    # (workbook: T13!G6 = Passif!C8, which carries the name C_13400).
    41: [('18', ('edi', '13400'))],
}


# Cells that dbc_liasse_fiscale computes through an engine OTHER than its
# edi_<code> one, and which therefore carry no codeEdi anywhere - they would be
# silently absent from the file even though the DGI defines them and every real
# filing carries them. Both tables below were checked cell by cell against the
# reference workbook's own C_ grid.

# bilan_amort_report's "Cumul d'amortissement fin exercice" column, declared as
# cf_<debut>_<dotations>_<sorties> (see _report_custom_engine_... in
# dbc_liasse_fiscale/models/account_report.py). On the workbook's T8 sheet that
# column is E, and each row's E cell holds the code mapped here.
_CF_CODES = {
    'cf_1231_1232_1233': '1234',
    'cf_1236_1237_1238': '1239',
    'cf_1241_1242_1243': '1244',
    'cf_1246_1247_1248': '1249',
    'cf_1624_1629_1650': '1679',
    'cf_1625_1630_1651': '1680',
    'cf_1626_1631_1652': '1681',
    'cf_1627_1632_1653': '1682',
    'cf_1628_1633_1654': '1683',
    'cf_1700_1708_1716': '1724',
    'cf_1701_1709_1717': '1725',
    'cf_1702_1710_1718': '1726',
    'cf_1703_1711_1719': '1727',
    'cf_1704_1712_1720': '1728',
    'cf_1705_1713_1721': '1729',
    'cf_1706_1714_1722': '1730',
    'cf_1707_1715_1723': '1731',
    'cf_14061_14062_14063': '14064',
}

# bilan_passif_report's three total lines, computed with Odoo's own
# 'aggregation' engine (formula "AC_TOTAL.n + BA_TOTAL.n + ...") and so having
# no subformula at all. Keyed by the expression's xml_id, which is stable -
# unlike the line label, which repeats across lines. On the workbook's Passif
# sheet column C is Exercice and D Exercice precedent, giving:
#   row 19 "Total des capitaux propres (A)" -> 13410 / 13421
#   row 36 "TOTAL I (A+B+C+D+E)"            -> 13385 / 13436
#   row 54 "TOTAL GENERAL I+II+III"         -> 13368 / 13453
_AGGREGATE_CODES = {
    'dbc_liasse_fiscale.bilan_passif_expr_ac_total_n': '13410',
    'dbc_liasse_fiscale.bilan_passif_expr_ac_total_n_prev': '13421',
    'dbc_liasse_fiscale.bilan_passif_expr_total_1_n': '13385',
    'dbc_liasse_fiscale.bilan_passif_expr_total_1_n_prev': '13436',
    'dbc_liasse_fiscale.bilan_passif_expr_total_general_n': '13368',
    'dbc_liasse_fiscale.bilan_passif_expr_total_general_n_prev': '13453',
}

# Pre-flight coherence. Simpl-IS accepts a file on its format alone - cahier
# des charges 3.3.2 is explicit that the only business rule able to reject one
# is a declaration already sitting "en attente de validation" - so an
# unbalanced liasse is loaded into the EFI draft without a word. These are the
# identities that cannot legitimately fail in a double-entry ledger, checked
# here for the same reason the reference workbook refuses to generate while its
# "Incoherences" sheet is non-zero.
#   188 / 189   = Bilan Actif  TOTAL GENERAL, Net and Net N-1
#   13368/13453 = Bilan Passif TOTAL GENERAL I+II+III, N and N-1
_COHERENCE_CHECKS = (
    ("Total Actif / Total Passif - exercice", '188', '13368'),
    ("Total Actif / Total Passif - exercice precedent", '189', '13453'),
)
# Generous enough to absorb centime rounding on the way to 2 decimals, tight
# enough that a real imbalance cannot slip through.
_COHERENCE_TOLERANCE = 1.0



def _write_element(element, lines, depth=0):
    """Serialize one element per line, indented the way the reference does.

    xmlGen.bas writes every tag on its own row and then prints the rows one by
    one, so a liasse produced there is a tall, readable file rather than a
    single line. An element holding text keeps that text on the same line as
    its tags - putting a newline inside <valeur> would make the newline part
    of the declared value.

    Indentation is two spaces per level, which lines the output up with the
    filed liasse of this company row for row.  It is cosmetic and cannot
    change a declared value: whitespace between elements is insignificant for
    element-only content, and nothing is ever inserted inside an element that
    carries text.

    An element with neither children nor text is written `<tag> </tag>`,
    following the reference, which renders an empty <extraFieldvaleurs> that
    way.  `text is None` is what tells such a container apart from a leaf
    whose text is the empty string; every leaf this module builds is assigned
    a string, so the two cannot be confused.
    """
    pad = '  ' * depth
    if len(element):
        lines.append(f'{pad}<{element.tag}>')
        for child in element:
            _write_element(child, lines, depth + 1)
        lines.append(f'{pad}</{element.tag}>')
    elif element.text is None:
        lines.append(f'{pad}<{element.tag}> </{element.tag}>')
    else:
        lines.append(f'{pad}<{element.tag}>{escape(element.text)}</{element.tag}>')


def _clean_text(value):
    """Flatten a text value to plain ASCII-ish content.

    The reference generator (xmlGen.bas) rewrites accents and a couple of
    punctuation marks out of every value before writing the file, so a liasse
    produced here stays byte-comparable with one produced there and cannot trip
    the DGI parser on an encoding mismatch.
    """
    text = str(value).replace('€', 'e')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return text.replace('°', ' ').replace("'", ' ').strip()


def _is_blank_cell(cell):
    """True for a cell that has nothing to declare, and is therefore left out.

    4.3.1: "Si une valeur n'est pas a renseigner, la balise ValeurCellule
    correspondante peut ne pas etre integree dans le fichier XML." The real
    filed liasse applies that to every empty cell AND to every flat cell whose
    value is 0 - of its 536 cells only three are numerically zero, and all
    three sit inside a detail row that carries other values.

    So the rule is read off the reference, not invented:

    * a cell with no value at all (None/False/empty text) is always left out;
    * a FLAT cell (no numeroLigne) worth 0 is left out too - declaring it
      would overwrite whatever SIMPL-IS holds with a zero the report never
      had a figure for, and 4.3.1 lets the batch keep the tableaux it is not
      sent;
    * a 0 inside a DETAIL ROW is kept, because the row exists and dropping
      one of its columns would leave it ragged.
    """
    _code, value, numero = cell
    if value is None or value is False or (isinstance(value, str) and not value.strip()):
        return True
    if numero is None and isinstance(value, (int, float)) and not isinstance(value, bool):
        return abs(value) < 0.005
    return False


def _clean_cell_text(value):
    """Normalise one Texte cell value, keeping its accents.

    _clean_text (used for the file NAME) folds French text down to ASCII.
    A cell value must not be folded: the real filed liasse of this company
    declares encoding="utf-8" and carries its tableau 220 sentences with
    every accent and apostrophe intact ("La valeur d'entree est constituee"
    is written "La valeur d'entrée est constituée"), and 5.2 of the cahier
    des charges only says Texte holds "valeurs alphanumériques" - with no
    character restriction, in a file the spec itself requires to be UTF-8.
    Folding here would therefore make every sentence of tableau 220 differ
    from the filing it is meant to reproduce, and mangle it on screen at the
    DGI ("d entree", "cout de revient") for no gain.

    What is still removed: control characters (illegal in XML 1.0, and they
    would make the file unparseable rather than merely different) and the
    surrounding whitespace.
    """
    text = str(value)
    text = ''.join(ch for ch in text if ch >= ' ' or ch in '	')
    return text.strip()


def _format_value(value):
    """Serialize one cell value using the data types of the cahier des charges:
    Double (decimal point, 2 decimals), Entier, Date (jj/mm/aaaa) or Texte.
    """
    if value is None or value == '':
        return ''
    # bool is an int subclass, and an unset Char/Date comes back as False -
    # both must read as "nothing to declare", never as "0"/"False".
    if isinstance(value, bool):
        return ''
    if isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    if isinstance(value, float):
        rounded = round(value, 2)
        return str(int(rounded)) if rounded == int(rounded) else str(rounded)
    if isinstance(value, int):
        return str(value)
    return _clean_cell_text(value)


# Codes the DGI's own file writes as a date followed by a midnight time
# ("01/01/2025 00:00:00") rather than the plain jj/mm/aaaa that 5.2 of the
# cahier des charges describes. Only the date of effect of a location /
# credit-bail contract is written that way in the filed liasse of this
# company; every other date in it, including the extraFields and tableau 16's
# dates of entry, is plain. Matching it cell for cell is the point of the
# list, so it holds exactly those codes and no others - a code added here
# starts declaring a time the DGI may not expect.
_DATETIME_CODES = {'1270'}


def _looks_like_date(text):
    """True for a jj/mm/aaaa string, without pulling in a regex."""
    return (len(text) == 10 and text[2] == '/' and text[5] == '/'
            and text.replace('/', '').isdigit())


def _format_cell_value(code, value):
    """_format_value, plus the midnight time for the codes that carry one.

    Applied to ValeurCellule only, never to an ExtraFieldValeur: the filing
    writes all four of its date extraFields (50, 51, 52, 70) plain.
    """
    text = _format_value(value)
    if str(code) in _DATETIME_CODES and _looks_like_date(text):
        text += ' 00:00:00'
    return text


class AccountReportCustomHandlerEdi(models.AbstractModel):
    # Extending the shared base rather than one specific handler: the 25 liasse
    # reports are split across account.report.handler.ma_fiscal (21 of them)
    # and the account.report.handler.ma_data_entry family (the 6 editable
    # ones), which are two independent branches. _dbc_edi_tableau_for() keeps
    # the button off every other report in the database.
    _inherit = 'account.report.custom.handler'

    # ------------------------------------------------------------------ button

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        if self._dbc_edi_tableau_for(report):
            options.setdefault('buttons', []).append({
                'name': _('EDI XML'),
                'sequence': 25,
                'action': 'export_file',
                'action_param': 'dbc_liasse_edi_export',
                'file_export_type': _('ZIP'),
            })

    def _dbc_edi_tableau_for(self, report):
        """The DGI tableau id of `report`, or None when it is not one of the
        25 reports making up the Modele Normal liasse."""
        xmlid = report.get_external_id().get(report.id) or ''
        module, _dot, name = xmlid.partition('.')
        if module != 'dbc_liasse_fiscale':
            return None
        return _REPORT_TABLEAU.get(name)

    # --------------------------------------------------------------- generator

    def dbc_liasse_edi_export(self, options):
        """Build the Simpl-IS EDI liasse for the period currently selected on
        the report, and hand it back as a ZIP holding a single XML file."""
        report = self.env['account.report'].browse(options['report_id'])
        company = self._dbc_edi_company(report, options)

        identifiant = company._dbc_liasse_identifiant_fiscal()
        if not identifiant:
            raise UserError(_(
                "No Identifiant Fiscal is set on company %(company)s.\n\n"
                "Simpl-IS rejects an EDI file whose <identifiantFiscal> does not "
                "match the declaring taxpayer, so it cannot be generated without "
                "it. Set it in Settings > Users & Companies > Companies, on the "
                "\"Identifiants Fiscaux\" tab, field IF (iff).",
                company=company.display_name,
            ))

        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        xml_bytes = self._dbc_edi_build_xml(
            company, identifiant, date_from, date_to, bool(options.get('all_entries')),
        )

        if len(xml_bytes) > _MAX_FILE_BYTES:
            _logger.warning(
                "EDI liasse for %s is %d bytes, above the 1 Mo Simpl-IS limit",
                company.display_name, len(xml_bytes),
            )

        basename = self._dbc_edi_basename(company, date_to)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f'{basename}.xml', xml_bytes)

        return {
            'file_name': f'{basename}.zip',
            'file_content': buffer.getvalue(),
            'file_type': 'zip',
        }

    def _dbc_edi_company(self, report, options):
        """The single company the liasse is filed for.

        One liasse carries exactly one <identifiantFiscal>, so exporting with
        several companies selected would silently merge unrelated taxpayers.
        """
        companies = self.env['res.company'].browse(report.get_report_company_ids(options))
        if len(companies) > 1:
            raise UserError(_(
                "An EDI liasse covers a single company, but %(count)s are currently "
                "selected (%(names)s).\n\nSwitch to just the company you are filing "
                "for before generating the file.",
                count=len(companies),
                names=', '.join(companies.mapped('display_name')),
            ))
        return companies or self.env.company

    def _dbc_edi_exercice_bounds(self, company, options):
        """(date_from, date_to) of the exercice being declared, snapped to the
        whole fiscal year containing the report's date filter.

        A liasse declares a CLOSED exercice. The date filter defaults to "as of
        today", so taking it literally produced a 10-month declaration that
        Simpl-IS would have loaded as the real period. Snapping also keeps the
        header honest: every report is recomputed on these same bounds, so the
        figures always cover exactly the exercice the header claims. Which year
        gets exported is still the user's choice - it follows the filter.
        """
        dates = company.compute_fiscalyear_dates(
            fields.Date.from_string(options['date']['date_to'])
        )
        return dates['date_from'], dates['date_to']

    def _dbc_edi_period_options(self, date_from, date_to, all_entries):
        """`previous_options` pinning any report to the exercice being declared.

        `all_entries` is carried over from the report the button was pressed on:
        the liasse reports expose a "With Draft Entries" filter
        (filter_show_draft), and the file has to contain what the user is
        looking at. Without this the other 24 reports would silently rebuild
        posted-only and the whole liasse would come out empty on a database
        whose entries are still in draft.
        """
        return {
            'date': {
                'date_from': date_from.strftime('%Y-%m-%d'),
                'date_to': date_to.strftime('%Y-%m-%d'),
                'mode': 'range',
                'filter': 'custom',
            },
            'all_entries': all_entries,
        }

    def _dbc_edi_check_coherence(self, values, company, date_from, date_to):
        """Refuse to produce a liasse whose balance sheet does not balance."""
        problems = []
        for label, actif_code, passif_code in _COHERENCE_CHECKS:
            actif, passif = values.get(actif_code), values.get(passif_code)
            if actif is None or passif is None:
                continue
            if abs(actif - passif) > _COHERENCE_TOLERANCE:
                problems.append((label, actif_code, actif, passif_code, passif, actif - passif))
        if not problems:
            return
        details = '\n'.join(
            f"  - {label}:\n"
            f"      Actif  {actif:>18,.2f}  (codeEdi {actif_code})\n"
            f"      Passif {passif:>18,.2f}  (codeEdi {passif_code})\n"
            f"      ecart  {ecart:>18,.2f}"
            for label, actif_code, actif, passif_code, passif, ecart in problems
        )
        # The balance sheet is the only thing worth refusing a file over, but
        # while we are refusing it, name everything else the coherence report
        # would show - one trip instead of two. Computed here only, so the
        # happy path costs nothing.
        autres = self.env['account.report.handler.ma_coherence']._coh_ecarts(
            values, company, date_from, date_to)
        if autres:
            details += _(
                "\n\n  Autres contrôles en écart (sans effet sur le dépôt) :\n"
            ) + '\n'.join(
                f"  - {label_a} / {label_b}:\n"
                f"      {valeur_a:>18,.2f}  /  {valeur_b:>18,.2f}"
                f"      ecart {ecart:>14,.2f}"
                for label_a, label_b, valeur_a, valeur_b, ecart in autres
            )
        raise UserError(_(
            "The liasse of %(company)s for the exercice %(date_from)s - %(date_to)s "
            "does not balance, so no file was produced:\n\n%(details)s\n\n"
            "Simpl-IS validates a file on its format alone and would load these "
            "figures into the declaration exactly as they are. Review the "
            "Bilan (Actif) and Bilan (Passif) reports for this exercice first.",
            company=company.display_name,
            date_from=date_from, date_to=date_to, details=details,
        ))

    def _dbc_edi_basename(self, company, date_to):
        name = _clean_text(company.name).replace(' ', '_')
        name = re.sub(r'[^A-Za-z0-9_-]', '', name)
        return f"Liasse_{name}_Au_{date_to.strftime('%d_%m_%Y')}"

    # --------------------------------------------------------- value gathering

    def _dbc_edi_report_values(self, report, report_options):
        """{codeEdi: value} for every cell `report` computes, read off the
        RENDERED report rather than recomputed from the formulas.

        Reading the rendered lines is what makes the file agree with the screen
        by construction. Recomputing looks equivalent and is not: evaluating a
        target code resolves its whole formula tree under ONE date_scope, so
        any cell whose subtree mixes scopes comes out wrong. The Passif totals
        are exactly that case - they sum the capital accounts (from_beginning)
        with the resultat (strict_range) - and re-deriving them cumulatively
        inflated every one of them by the previous exercice's result.
        """
        labels = [column['expression_label'] for column in report_options['columns']]
        rendered = {}
        for line in report._get_lines(report_options):
            model, res_id = report._get_model_info_from_id(line['id'])
            if model != 'account.report.line':
                continue
            columns = line.get('columns') or []
            rendered[res_id] = {
                labels[i]: columns[i].get('no_format')
                for i in range(min(len(labels), len(columns)))
            }

        values = {}
        skipped = set()
        xmlids = report.line_ids.expression_ids.get_external_id()
        for report_line in report.line_ids:
            per_label = rendered.get(report_line.id) or {}
            for expression in report_line.expression_ids:
                code = self._dbc_edi_code_of(expression, xmlids, skipped)
                if code is None:
                    continue
                value = per_label.get(expression.label)
                # None is a deliberately blank cell (an unfilled "Saisie"),
                # not a zero. 4.3.1 lets an unfilled cell be left out, which
                # is more faithful than declaring a 0 the report never showed.
                if value is None:
                    continue
                values[code] = value

        if skipped:
            _logger.debug(
                "EDI liasse: %s - %d non-DGI code(s) left out: %s",
                report.display_name, len(skipped), ', '.join(sorted(skipped)),
            )
        return values

    def _dbc_edi_code_of(self, expression, xmlids, skipped):
        """The published codeEdi a given report cell declares, or None."""
        subformula = (expression.subformula or '').strip()
        match = _EDI_SUBFORMULA_RE.fullmatch(subformula)
        if match:
            code = match.group(1)
            if code in _INLINE_SUBTOTAL_CODES:
                # Declared by _ANNEX_TABLES as detail rows instead.
                return None
            # Every codeEdi the DGI publishes is purely numeric (all 2275 of
            # them, across both models). dbc_liasse_fiscale additionally binds
            # some on-screen control columns to codes of its own that live in
            # ma.fiscal.setting but mean nothing to the DGI - the "ECART"
            # columns of the Amortissements / Immobilisations / TVA reports,
            # declared as edi_E1234, edi_E1064, edi_E2068... Declaring those
            # would make Simpl-IS reject the file on an unknown cell, so
            # anything non-numeric is left out.
            if not code.isdigit():
                skipped.add(code)
                return None
            return code
        if subformula in _CF_CODES:
            return _CF_CODES[subformula]
        return _AGGREGATE_CODES.get(xmlids.get(expression.id))

    # {tableau_id: (date field, scope)} for a table scoped by a date of its own
    # rather than by annee_fiscale - must stay in step with _ma_date_field and
    # _ma_date_scope on the matching handler, or the file would carry different
    # rows than the screen. 'to_period_end' takes the whole history up to the
    # end of the exercice, 'strict_range' only the rows dated inside it.
    _ANNEX_DATE_FIELD = {
        12: ('date_entree', 'to_period_end'),
        28: ('date_conclusion', 'strict_range'),
    }

    def _dbc_edi_annex_domain(self, tableau_id, company, year, date_from, date_to):
        domain = [('company_id', '=', company.id)]
        date_field, scope = self._ANNEX_DATE_FIELD.get(tableau_id, (None, None))
        if date_field:
            domain.append((date_field, '<=', date_to))
            if scope == 'strict_range':
                domain.append((date_field, '>=', date_from))
        else:
            domain.append(('annee_fiscale', '=', year))
        return domain

    def _dbc_edi_annex_rows(self, tableau_id, company, year, date_from, date_to):
        """[(codeEdi, value, numeroLigne)] for a tableau whose rows are
        unlimited, read from the editable records captured in Odoo."""
        model_name, sections = _ANNEX_TABLES[tableau_id]
        rows = []
        for extra_leaf, columns in sections:
            domain = self._dbc_edi_annex_domain(tableau_id, company, year, date_from, date_to)
            if extra_leaf:
                domain.append(extra_leaf)
            # _order is 'sequence, id' on every one of these models, so the
            # numeroLigne sequence follows the order the report displays.
            for numero, record in enumerate(self.env[model_name].search(domain), start=1):
                for field_name, code in columns:
                    value = record[field_name]
                    tick = _X_MARKED_CODES.get(code)
                    if tick:
                        if not tick(value):
                            continue
                        value = 'X'
                    # A row cell with nothing in it is left out, like any other
                    # unfilled cell (4.3.1). A numeric 0 is NOT nothing here:
                    # the real filing does carry 0 inside a row that has other
                    # values (tableau 12 rows 5 and 6), because dropping it
                    # would leave the row ragged.
                    elif value is None or value is False or value == '':
                        continue
                    rows.append((code, value, numero))
        return rows

    def _dbc_edi_annex_totals(self, tableau_id, company, year, date_from, date_to):
        """[(codeEdi, value, None)] for the "Totaux" row of an unlimited table,
        summed over the same records that produced its detail rows."""
        model_name, _sections = _ANNEX_TABLES[tableau_id]
        records = self.env[model_name].search(
            self._dbc_edi_annex_domain(tableau_id, company, year, date_from, date_to))
        totals = []
        for code, field_name in _ANNEX_TOTALS.get(tableau_id, ()):
            value = sum(records.mapped(field_name)) if field_name else 0.0
            totals.append((code, value, None))
        return totals

    def _dbc_edi_extra_fields(self, tableau_id, company, year, date_from, date_to, all_values):
        out = []
        for code, spec in _EXTRA_FIELDS.get(tableau_id, ()):
            kind = spec[0]
            if kind == 'date_to':
                value = date_to
            elif kind == 'date_from':
                value = date_from
            elif kind == 'edi':
                value = all_values.get(spec[1], 0.0)
            else:  # ('total', model, field)
                domain = self._dbc_edi_annex_domain(tableau_id, company, year, date_from, date_to)
                value = sum(self.env[spec[1]].search(domain).mapped(spec[2]))
            out.append((code, value))
        return out

    def _dbc_edi_all_values(self, date_from, date_to, all_entries, only_reports=None):
        """(all_values, per_tableau) for the whole liasse over one exercice.

        `all_values` is {codeEdi: value} merged across every tableau,
        `per_tableau` is {tableau_id: {codeEdi: value}}.

        Computing this up front is what lets an extraField point at a codeEdi
        living in another tableau (code 18 of tableau 41 is the Passif's
        Capital social). It is also what the coherence report reads, so that
        the screen and the exported file can never disagree about a figure:
        they come from the same rendered reports.

        `only_reports` restricts the work to a set of dbc_liasse_fiscale report
        xml_id names; None means the whole liasse.
        """
        period_options = self._dbc_edi_period_options(date_from, date_to, all_entries)
        per_tableau = {}
        all_values = {}
        seen_in = {}
        for xmlid, tableau_id in _REPORT_TABLEAU.items():
            if only_reports is not None and xmlid not in only_reports:
                continue
            target = self.env.ref(f'dbc_liasse_fiscale.{xmlid}', raise_if_not_found=False)
            if not target:
                _logger.warning("EDI liasse: report %r not found, tableau %s skipped", xmlid, tableau_id)
                continue
            target_options = target.get_options(dict(period_options))
            values = self._dbc_edi_report_values(target, target_options)
            for code in values:
                if code in seen_in:
                    _logger.warning(
                        "EDI liasse: codeEdi %s computed by both %s and %s; "
                        "it will be declared under both tableaux",
                        code, seen_in[code], xmlid,
                    )
                else:
                    seen_in[code] = xmlid
            per_tableau[tableau_id] = values
            all_values.update(values)
        return all_values, per_tableau

    # ----------------------------------------------------------- xml assembly

    def _dbc_edi_build_xml(self, company, identifiant, date_from, date_to, all_entries):
        year = str(date_to.year)

        # Pass 1 - compute every tableau's cells.
        all_values, per_tableau = self._dbc_edi_all_values(date_from, date_to, all_entries)

        self._dbc_edi_check_coherence(all_values, company, date_from, date_to)

        # Pass 2 - serialize.
        root = ET.Element('Liasse')
        modele = ET.SubElement(root, 'modele')
        ET.SubElement(modele, 'id').text = _MODELE_NORMAL_ID
        resultat = ET.SubElement(root, 'resultatFiscal')
        ET.SubElement(resultat, 'identifiantFiscal').text = identifiant
        ET.SubElement(resultat, 'exerciceFiscalDu').text = date_from.strftime('%Y-%m-%d')
        ET.SubElement(resultat, 'exerciceFiscalAu').text = date_to.strftime('%Y-%m-%d')
        groupe = ET.SubElement(root, 'groupeValeursTableau')

        n_cells = 0
        for tableau_id in per_tableau:
            cells = [(code, value, None) for code, value in per_tableau[tableau_id].items()]
            if tableau_id in _ANNEX_TABLES:
                cells += self._dbc_edi_annex_totals(tableau_id, company, year, date_from, date_to)
                cells += self._dbc_edi_annex_rows(tableau_id, company, year, date_from, date_to)
            cells = [c for c in cells if not _is_blank_cell(c)]

            if not cells:
                # Leaving a whole tableau out is explicitly allowed (cahier des
                # charges 4.3.1); a tableau present with an empty
                # <groupeValeurs> is not clearly allowed, and the reference
                # generator jumps over exactly these cases (passerLeTableau in
                # xmlGen.bas, guarded on the first row being blank). Take the
                # side the published schema is known to accept.
                _logger.info("EDI liasse: tableau %s has no value to declare, left out", tableau_id)
                continue

            valeurs_tableau = ET.SubElement(groupe, 'ValeursTableau')
            tableau = ET.SubElement(valeurs_tableau, 'tableau')
            ET.SubElement(tableau, 'id').text = str(tableau_id)
            groupe_valeurs = ET.SubElement(valeurs_tableau, 'groupeValeurs')
            for code, value, numero in cells:
                cellule_value = ET.SubElement(groupe_valeurs, 'ValeurCellule')
                cellule = ET.SubElement(cellule_value, 'cellule')
                ET.SubElement(cellule, 'codeEdi').text = str(code)
                ET.SubElement(cellule_value, 'valeur').text = _format_cell_value(code, value)
                if numero is not None:
                    ET.SubElement(cellule_value, 'numeroLigne').text = str(numero)
                n_cells += 1

            extra_fields = ET.SubElement(valeurs_tableau, 'extraFieldvaleurs')
            for code, value in self._dbc_edi_extra_fields(
                tableau_id, company, year, date_from, date_to, all_values,
            ):
                extra_value = ET.SubElement(extra_fields, 'ExtraFieldValeur')
                extra_field = ET.SubElement(extra_value, 'extraField')
                ET.SubElement(extra_field, 'code').text = str(code)
                ET.SubElement(extra_value, 'valeur').text = _format_value(value)

        _logger.info(
            "EDI liasse for %s (%s..%s): %d tableaux, %d cells",
            company.display_name, date_from, date_to, len(per_tableau), n_cells,
        )
        lines = [_XML_DECLARATION]
        _write_element(root, lines)
        # CRLF and no trailing newline: the byte layout of the filed
        # liasse this module is validated against.
        text = _XML_LINE_END.join(lines)
        # xmlcharrefreplace rather than a crash or a silent mangle: anything
        # outside ISO-8859-1 becomes a numeric character reference, which stays
        # valid XML and loses nothing.
        return text.encode(_XML_ENCODING, errors='xmlcharrefreplace')
