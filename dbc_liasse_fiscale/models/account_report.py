# -*- coding: utf-8 -*-
import json
import logging
from platform import node
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Remote formules API
# --------------------------------------------------------------------------- #
_DEFAULT_API_URL = 'http://app.ozirpaie.ma:2024/api/ma/formules'
_API_TIMEOUT = 10

# Cache structure: {url: {'fetched_at': float, 'data': dict, 'ttl': int}}
# TTL is intentionally short (default 10s) so formula changes on the remote
# instance are picked up quickly. Raise via system param if your API is slow.
_API_CACHE = {}

# {url: timestamp before which the API is not worth calling again}.
#
# A failed call costs a full _API_TIMEOUT, and _get_formules_map runs several
# times per report render (the Saisie-code set, the editable-cell set, the
# evaluator, ...). Without remembering the failure, one unreachable API turned
# every render into a stack of 10s timeouts - measured at 31s to 84s per
# report, against well under a second when the API answers or its failure is
# already known. So a failure is cached like a success: the first call pays the
# timeout, the rest of that window falls straight through to the stale cache or
# the local ma.fiscal.setting table.
_API_FAILURE_CACHE = {}
_API_FAILURE_TTL = 60

# --------------------------------------------------------------------------- #
# "Data-entry" reports: no EDI code or formula behind any value - every
# field is a plain value the user keys in through the report's header "+"
# button (create) and per-row pencil/trash icons (edit/delete). Each field
# spec dict drives BOTH the report's account.report.column and the
# add/edit popup's inputs, so the two can never drift apart - see
# AccountReportHandlerMaDataEntry and action_ma_data_entry_save/delete
# below.
#
# dict keys: name (matches the model field and the column's
# expression_label), label, type ('text'/'number'/'date'/'select'/
# 'computed'), selection (required for type='select': [[value, label],
# ...]), summable (if True, included in the report's always-present
# "Totaux" line - only meaningful for 'number'/'computed' fields).
#
# A 'computed' field is read-only everywhere (report column AND the
# create/edit popup, which recomputes it live as the user types using the
# JS function named by its 'compute' key - see data_entry_popover.js). Its
# actual stored value always comes from the model's own create/write
# override (see ma_fiscal_dotation.py for an example), never from the
# client - action_ma_data_entry_save below drops it from clean_values.
#
# 'format': 'percent' renders a numeric value as "20.00%" instead of a
# plain number in the report column - independent of 'type', so it also
# works on an ordinary editable 'number' field (the popup input itself
# stays a plain number either way, only the report's own cell is
# formatted).
#
# 'blank_if_field': the name of a boolean field on the record that, when
# True, blanks THIS column's display (e.g. a legal-cap overflow) instead
# of showing its (still real, summable) stored value. Only makes sense on
# a 'computed' field.
#
# 'pair_field' + 'pair_formula': for two fields that are each other's
# inverse (e.g. taux = 100/duree and duree = 100/taux) and both stay
# freely editable - editing either one live-recomputes the other in the
# popup via the named MA_PAIR_FNS entry (data_entry_popover.js); neither
# field is 'computed', so both are saved as the user left them.
# --------------------------------------------------------------------------- #

# Every data-entry report's records now carry a plain "annee_fiscale"
# Selection field (2023 up to the current year) the user picks in the
# create/edit popup - it is what the report's date filter (re-enabled for
# these reports in data_entry_popover.xml) actually filters rows on, see
# AccountReportHandlerMaDataEntry._dynamic_lines_generator below. It is
# deliberately left out of every _MA_*_FIELDS list's matching report
# column, so it never shows up as a column in the table itself - only in
# the popup.
_MA_YEAR_START = 2023


def _ma_fiscal_year_selection():
    """Newest year first: the create popup's <select> defaults to
    whichever option comes first in this list when no value is set yet
    (see _defaultValue in data_entry_popover.js), and the current year is
    by far the most common one to pick for a brand-new row.

    Computed fresh on every call rather than once at import time, so next
    year's option appears the moment it starts, with no module update or
    server restart needed - both the 6 ma.fiscal.*.line models' own
    annee_fiscale field (selection=lambda self: ...) and
    _init_options_ma_add_line_button (which rebuilds the popup's field
    spec on every report render) call this instead of reading a frozen
    module-level list.
    """
    return [(str(y), str(y)) for y in range(fields.Date.today().year, _MA_YEAR_START - 1, -1)]


_MA_ANNEE_FISCALE_FIELD = {'name': 'annee_fiscale', 'label': "Année fiscale", 'type': 'select', 'selection': _ma_fiscal_year_selection()}

_MA_CAPITAL_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'nom_prenom', 'label': "Nom et prénom", 'type': 'text'},
    {'name': 'raison_sociale', 'label': "Raison sociale", 'type': 'text'},
    {'name': 'num_if', 'label': "N° IF", 'type': 'text'},
    {'name': 'num_cin', 'label': "N° C.N.I.", 'type': 'text'},
    {'name': 'num_ce', 'label': "N° Carte d'étranger", 'type': 'text'},
    {'name': 'adresse', 'label': "Adresse", 'type': 'text'},
    {'name': 'titres_precedent', 'label': "Nombre de titres (exercice précédent)", 'type': 'number'},
    {'name': 'titres_actuel', 'label': "Nombre de titres (exercice actuel)", 'type': 'number'},
    {'name': 'valeur_nominale', 'label': "Valeur nominale", 'type': 'number'},
    {'name': 'montant_souscrit', 'label': "Montant du capital - Souscrit", 'type': 'number'},
    {'name': 'montant_appele', 'label': "Montant du capital - Appelé", 'type': 'number'},
    {'name': 'montant_libere', 'label': "Montant du capital - Libéré", 'type': 'number'},
]

_MA_LOAN_TYPE_SELECTION = [('associe', "A. Associés"), ('tiers', "B. Tiers")]

_MA_LOAN_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'type', 'label': "Type", 'type': 'select', 'selection': _MA_LOAN_TYPE_SELECTION},
    {'name': 'nom_prenom', 'label': "Nom et prénom", 'type': 'text'},
    {'name': 'raison_sociale', 'label': "Raison sociale", 'type': 'text'},
    {'name': 'adresse', 'label': "Adresse", 'type': 'text'},
    {'name': 'num_if', 'label': "IF", 'type': 'text'},
    {'name': 'num_cin', 'label': "N° CNI", 'type': 'text'},
    {'name': 'montant_pret', 'label': "Montant du prêt", 'type': 'number', 'summable': True},
    {'name': 'date_pret', 'label': "Date du prêt", 'type': 'date'},
    {'name': 'duree_mois', 'label': "Durée du prêt (en mois)", 'type': 'number'},
    {'name': 'taux_interet', 'label': "Taux d'intérêts", 'type': 'number'},
    {'name': 'charge_financiere_globale', 'label': "Charge financière globale", 'type': 'number', 'summable': True},
    {'name': 'remb_anterieur_principal', 'label': "Remb. Ex antérieurs Principal", 'type': 'number', 'summable': True},
    {'name': 'remb_anterieur_interet', 'label': "Remb. Ex antérieurs Intérêt", 'type': 'number', 'summable': True},
    {'name': 'remb_actuel_principal', 'label': "Remb. Ex actuel Principal", 'type': 'number', 'summable': True},
    {'name': 'remb_actuel_interet', 'label': "Remb. Ex actuel Intérêt", 'type': 'number', 'summable': True},
    {'name': 'observations', 'label': "Observations", 'type': 'text'},
]

_MA_LEASING_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'rubrique', 'label': "Rubriques", 'type': 'text'},
    {'name': 'date_premiere_echeance', 'label': "Date de la 1ère échéance", 'type': 'date'},
    {'name': 'duree_contrat_mois', 'label': "Durée du contrat (mois)", 'type': 'number'},
    {'name': 'valeur_estimee', 'label': "Valeur estimée du bien à la date du contrat", 'type': 'number'},
    {'name': 'duree_theorique_amortissement', 'label': "Durée théorique d'amortissement du bien", 'type': 'number'},
    {'name': 'cumul_redevances_anterieures', 'label': "Cumul des redevances des exercices précédents", 'type': 'number'},
    {'name': 'montant_redevances_exercice', 'label': "Montant des redevances de l'exercice", 'type': 'number'},
    {'name': 'redevances_restant_moins_un_an', 'label': "Redevances restant à payer à moins d'un an", 'type': 'number'},
    {'name': 'redevances_restant_plus_un_an', 'label': "Redevances restant à payer à plus d'un an", 'type': 'number'},
    {'name': 'prix_achat_residuel', 'label': "Prix d'achat résiduel en fin de contrat", 'type': 'number'},
    {'name': 'observations', 'label': "Observations", 'type': 'text'},
]

# No _MA_ANNEE_FISCALE_FIELD here, like _MA_AMORT_FIELDS below: these rows are
# scoped by their own "Date de conclusion de l'acte" instead, strictly inside
# the selected period (see _ma_date_field/_ma_date_scope on
# AccountReportHandlerMaRental) - the reference liasse dates every bail and
# shows only the acts of the exercice being filed.
_MA_RENTAL_FIELDS = [
    {'name': 'nature_bien', 'label': "Nature du bien loué", 'type': 'text'},
    {'name': 'lieu_situation', 'label': "Lieu de situation", 'type': 'text'},
    {'name': 'nom_prenom', 'label': "Nom et prénoms du propriétaire", 'type': 'text'},
    {'name': 'raison_sociale', 'label': "Raison sociale du propriétaire", 'type': 'text'},
    {'name': 'adresse', 'label': "Adresse du propriétaire", 'type': 'text'},
    {'name': 'num_if', 'label': "N° IF du propriétaire", 'type': 'text'},
    {'name': 'num_cin', 'label': "N° CNI du propriétaire", 'type': 'text'},
    {'name': 'num_ce', 'label': "N° Carte d'étranger du propriétaire", 'type': 'text'},
    {'name': 'date_conclusion', 'label': "Date de conclusion de l'acte de location", 'type': 'date'},
    {'name': 'montant_annuel', 'label': "Montant annuel de location", 'type': 'number', 'summable': True},
    {'name': 'montant_loyer_charges', 'label': "Montant de location de l'exercice", 'type': 'number', 'summable': True},
    # Column 12 of the reference sheet is a single free-text "Contrat" cell the
    # filer writes the nature of the contract into ("Bail ordinaire", ...), not
    # a pair of X-marked columns, so leasing_nieme_periode has no UI any more.
    {'name': 'bail_ordinaire', 'label': "Contrat", 'type': 'text'},
]

# "taux"/"amort_normal"/"total_amort" are 'computed': read-only in both the
# report and the popup, recomputed live client-side (data_entry_popover.js,
# MA_COMPUTE_FNS) and authoritatively server-side on save (see
# MaFiscalDotationLine._ma_compute_amort_values):
#   taux and duree are each other's inverse (100/x) and both stay freely
#       editable - editing either one live-updates the other in the popup
#       (pair_field/pair_formula below); whichever value the user actually
#       left in place is what gets saved, no server-side override.
#   amort_normal is EDITABLE; the model only fills it in when a row is
#       created without it (annuity capped by what is left to amortise) -
#       see MaFiscalDotationLine._ma_default_amort_normal
#   total_amort = amort_anterieurs + amort_normal, computed and stored
#       (= "col.4 + col.7" per the DGI sheet's own column-9 header)
# No _MA_ANNEE_FISCALE_FIELD here, unlike every other data-entry table: this
# one is scoped by its own "Date d'entrée" instead (see _ma_date_field on
# AccountReportHandlerMaDotation). An immobilisation acquired in 2020 is still
# being amortised in 2025 and has to stay on the 2025 état, so tying its row
# to a single exercice would hide it the following year.
_MA_AMORT_FIELDS = [
    # Column 1 of the DGI grid ("Nature"), exported as codeEdi 10061. It had
    # no field spec and no report column for a long time, so the model's
    # `immobilisation` could never be filled and the column went out blank on
    # every row - the reference liasse names every immobilisation there.
    {'name': 'immobilisation', 'label': "Immobilisations concernées", 'type': 'text'},
    {'name': 'date_entree', 'label': "Date d'entrée", 'type': 'date'},
    {'name': 'prix_acquisition', 'label': "Valeur à amortir - Prix d'acquisition", 'type': 'number', 'summable': True},
    {'name': 'valeur_comptable', 'label': "Valeur à amortir - Valeur comptable après réévaluation", 'type': 'number', 'summable': True},
    {'name': 'amort_anterieurs', 'label': "Amortissements antérieurs", 'type': 'number', 'summable': True},
    {'name': 'taux', 'label': "Taux", 'type': 'number', 'format': 'percent', 'pair_field': 'duree', 'pair_formula': 'ma_duration_from_rate'},
    {'name': 'duree', 'label': "Durée", 'type': 'number', 'pair_field': 'taux', 'pair_formula': 'ma_rate_from_duration'},
    # amort_normal is editable: no rule reproduces a real état, since the
    # reference liasse prorates assets acquired during the year and not
    # uniformly, so the accountant has the last word. The model only derives
    # a default for a row created without one - see ma_fiscal_dotation.py.
    {'name': 'amort_normal', 'label': "Amortissements normaux ou accélérés de l'exercice", 'type': 'number', 'summable': True},
    # total_amort does have an obvious rule - the DGI sheet's column-9 header
    # is literally "col.4 + col.7" - so it stays read-only and derived.
    {'name': 'total_amort', 'label': "Total des amortissements à la fin de l'exercice", 'type': 'computed', 'compute': 'ma_amort_total', 'summable': True},
    {'name': 'observations', 'label': "Observations", 'type': 'text'},
]

_MA_TITRES_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'raison_sociale', 'label': "Raison sociale de la société émettrice", 'type': 'text'},
    {'name': 'num_if', 'label': "N° IF", 'type': 'text'},
    {'name': 'secteur_activite', 'label': "Secteur d'activité", 'type': 'text'},
    {'name': 'capital_social', 'label': "Capital social", 'type': 'number', 'summable': True},
    {'name': 'participation_capital', 'label': "Participation au capital %", 'type': 'number', 'summable': True},
    {'name': 'prix_acquisition_global', 'label': "Prix d'acquisition global", 'type': 'number', 'summable': True},
    {'name': 'valeur_comptable_nette', 'label': "Valeur comptable nette", 'type': 'number', 'summable': True},
    {'name': 'date_cloture', 'label': "Date de clôture", 'type': 'date'},
    {'name': 'situation_nette', 'label': "Situation nette", 'type': 'number', 'summable': True},
    {'name': 'resultat_net', 'label': "Résultat net", 'type': 'number', 'summable': True},
    {'name': 'produits_cpc', 'label': "Produits inscrits au C.P.C de l'exercice", 'type': 'number', 'summable': True},
]

# Tableau n° 10. Three of the eight columns are read-only 'computed' cells,
# because the DGI's own reference grid derives them from the other five and
# nothing else ('Liasse ExcelToXml V4.0 2026.xlsm', sheet T10, rows 10..19):
# E =C-D, G =IF(F>E,F-E,""), H =IF(F<E,E-F,"") - see MaFiscalCessionLine.
# They are declared AFTER the fields they read, because the popup recomputes
# the computed fields in this very order (_recomputeComputedFields in
# data_entry_popover.js), so valeur_nette has to be fresh before plus_values
# and moins_values look at it.
# Every numeric column carries the legacy sheet's own TOTAL row (T10!C20..H20
# = SUM of its range), so all six are summable=True; "Date de cession" and
# "Compte principal" are not summed - the DGI does publish a total code above
# the latter (2037) but gives it the constant 0.
_MA_CESSION_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'date_cession', 'label': "Date de cession ou de retrait", 'type': 'date'},
    {'name': 'compte_principal', 'label': "Compte principal", 'type': 'text'},
    {'name': 'montant_brut', 'label': "Montant brut", 'type': 'number', 'summable': True},
    {'name': 'amort_cumules', 'label': "Amortissements cumulés", 'type': 'number', 'summable': True},
    {'name': 'valeur_nette', 'label': "Valeur nette d'amortissements", 'type': 'computed', 'compute': 'ma_cession_valeur_nette', 'summable': True},
    {'name': 'produit_cession', 'label': "Produit de cession", 'type': 'number', 'summable': True},
    {'name': 'plus_values', 'label': "Plus values", 'type': 'computed', 'compute': 'ma_cession_plus_values', 'summable': True},
    {'name': 'moins_values', 'label': "Moins values", 'type': 'computed', 'compute': 'ma_cession_moins_values', 'summable': True},
]

# The four free-text blocks of tableau 7. On the DGI grid each is a FIVE-row
# range, not a single cell (C_822 is 'T3'!$B$13:$B$17, C_825 is
# 'T3'!$B$19:$B$23), each paired with a label code (821/824/827/829, Texte)
# and a "montant (-)" code; the "1- COURANTES"/"2- NON COURANTES" subtotal
# cells the paper form draws between them carry no codeEdi at all.
MA_PASSAGE_SECTIONS = [
    ('reint_courante', "II- RÉINTÉGRATIONS FISCALES — 1. Courantes"),
    ('reint_non_courante', "II- RÉINTÉGRATIONS FISCALES — 2. Non courantes"),
    ('deduc_courante', "III- DÉDUCTIONS FISCALES — 1. Courantes"),
    ('deduc_non_courante', "III- DÉDUCTIONS FISCALES — 2. Non Courantes"),
]

# The grid has exactly five slots per block, and numeroLigne has nowhere to
# go beyond that, so a sixth row could not be declared in the XML at all.
MA_PASSAGE_MAX_ROWS = 5

# "Passage du Résultat Net Comptable au Résultat Net Fiscal" (tableau 7) is
# NOT a data-entry report: it is a static report whose réintégrations and
# déductions blocks host an editable table inline. The rows live in
# ma.fiscal.passage.line, are shown under their own section line by
# AccountReportHandlerMaPassage._custom_line_postprocessor, and feed the
# subtotal cells through _INLINE_DETAIL_CODES below.
_MA_PASSAGE_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'section', 'label': "Rubrique", 'type': 'select', 'selection': MA_PASSAGE_SECTIONS},
    {'name': 'intitule', 'label': "Intitulé", 'type': 'text'},
    {'name': 'montant_plus', 'label': "Montant (+)", 'type': 'number', 'summable': True},
    {'name': 'montant_moins', 'label': "Montant (-)", 'type': 'number', 'summable': True},
]

# Which report line each block's rows hang under, by xml_id.
_PASSAGE_SECTION_LINES = {
    'dbc_liasse_fiscale.bilan_fisc_line_reint_courantes': 'reint_courante',
    'dbc_liasse_fiscale.bilan_fisc_line_reint_non_courantes': 'reint_non_courante',
    'dbc_liasse_fiscale.bilan_fisc_line_deduc_courantes': 'deduc_courante',
    'dbc_liasse_fiscale.bilan_fisc_line_deduc_non_courantes': 'deduc_non_courante',
}

# --- Tableau 240, "État pour le Calcul de l'Impôt sur les Sociétés" ---------
# Same shape as tableau 7: two free-text blocks whose libellé AND montant are
# ranges on the DGI grid ('T15'!$A$13:$A$14 / $B$13:$B$14 and $A$20:$A$21 /
# $B$20:$B$21), with no codeEdi on the subtotal rows above them. The rate a
# line applies to ("... de 8,75%", "... de 10%") is part of the wording the
# preparer types, not a fixed report line.
MA_IS_SECTIONS = [
    ('ca_taux_reduit', "3- CA soumis au taux réduit"),
    ('autres_produits', "4- Autres produits taxables (ajout)"),
]

_MA_IS_FIELDS = [
    _MA_ANNEE_FISCALE_FIELD,
    {'name': 'section', 'label': "Rubrique", 'type': 'select', 'selection': MA_IS_SECTIONS},
    {'name': 'intitule', 'label': "Intitulé", 'type': 'text'},
    {'name': 'montant', 'label': "Montant", 'type': 'number', 'summable': True},
]

_IS_SECTION_LINES = {
    'dbc_liasse_fiscale.bilan_is_line_ca_taux_reduit': 'ca_taux_reduit',
    'dbc_liasse_fiscale.bilan_is_line_autres_produits_ajout': 'autres_produits',
}

# Every cell of the liasse whose value is the sum of rows the user keys in
# INSIDE a report, as {codeEdi: (model, section, field)}. MaFiscalEvaluator
# .evaluate_code resolves these here before it ever consults the formules
# map: the DGI grid has no subtotal cell for any of them (C_822 and C_14353
# are the detail RANGES, not totals), so there is no formula that could be
# right, and a "Saisie" value keyed straight into the cell would disagree
# with the rows shown underneath it the moment either changed.
_INLINE_DETAIL_CODES = {
    # tableau 7 - réintégrations / déductions
    '822': ('ma.fiscal.passage.line', 'reint_courante', 'montant_plus'),
    '823': ('ma.fiscal.passage.line', 'reint_courante', 'montant_moins'),
    '825': ('ma.fiscal.passage.line', 'reint_non_courante', 'montant_plus'),
    '826': ('ma.fiscal.passage.line', 'reint_non_courante', 'montant_moins'),
    '828': ('ma.fiscal.passage.line', 'deduc_courante', 'montant_plus'),
    '830': ('ma.fiscal.passage.line', 'deduc_courante', 'montant_moins'),
    '831': ('ma.fiscal.passage.line', 'deduc_non_courante', 'montant_plus'),
    '7830': ('ma.fiscal.passage.line', 'deduc_non_courante', 'montant_moins'),
    # tableau 240 - IS
    '14353': ('ma.fiscal.is.line', 'ca_taux_reduit', 'montant'),
    '14356': ('ma.fiscal.is.line', 'autres_produits', 'montant'),
}


def _get_cache_ttl(icp):
    try:
        return int(icp.get_param('ma_formules.api_cache_ttl', '10') or '10')
    except ValueError:
        return 10


def _get_failure_ttl(icp):
    """How long an unreachable formules API is left alone, in seconds.
    Override with the ``ma_formules.api_failure_ttl`` system parameter; the
    "Vider le cache des formules" button clears it immediately either way."""
    try:
        return int(icp.get_param('ma_formules.api_failure_ttl', str(_API_FAILURE_TTL))
                   or _API_FAILURE_TTL)
    except ValueError:
        return _API_FAILURE_TTL


def _normalise_formule(formule):
    """Legacy formules may carry a leading '='; drop it so the parser is happy."""
    if not formule:
        return '0'
    return (formule.strip().lstrip('=').strip()) or '0'


def _invalidate_api_cache(url=None):
    """Force-expire the cache for a specific URL (or all URLs if None).
    Call this from a button action or a scheduled task after updating formulas.
    """
    if url:
        _API_CACHE.pop(url, None)
        _API_FAILURE_CACHE.pop(url, None)
    else:
        _API_CACHE.clear()
        # Clearing the cache means "try again now", so a remembered failure has
        # to go with it - otherwise the button would appear to do nothing while
        # the back-off window is still open.
        _API_FAILURE_CACHE.clear()


# --------------------------------------------------------------------------- #
# Formule parsing  (unchanged from your original — keeping as-is)
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | '(?P<sq>[^']*)'
  | "(?P<dq>[^"]*)"
  | (?P<op>[()+\-*/])
)""", re.VERBOSE)


def parse_formule(text):
    tokens = []
    pos = 0
    text = (text or '').strip()
    if not text:
        return ('const', 0.0)
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ValueError(_("Unexpected character near %s", text[pos:pos + 10]))
        if m.lastgroup == 'num':
            tokens.append(('num', float(m.group('num'))))
        elif m.lastgroup == 'ident':
            tokens.append(('ident', m.group('ident')))
        elif m.lastgroup in ('sq', 'dq'):
            tokens.append(('str', m.group(m.lastgroup)))
        elif m.lastgroup == 'op':
            tokens.append(('op', m.group('op')))
        pos = m.end()

    index = [0]

    def peek():
        return tokens[index[0]] if index[0] < len(tokens) else (None, None)

    def take(expected_kind=None):
        kind, value = peek()
        if expected_kind and kind != expected_kind:
            raise ValueError(_("Expected %s token", expected_kind))
        index[0] += 1
        return kind, value

    def expr():
        node = term()
        while peek() == ('op', '+') or peek() == ('op', '-'):
            op = take()[1]
            node = ('bin', op, node, term())
        return node

    def term():
        node = factor()
        while peek() == ('op', '*') or peek() == ('op', '/'):
            op = take()[1]
            node = ('bin', op, node, factor())
        return node

    def factor():
        negations = 0
        while True:
            kind, value = peek()
            if (kind, value) == ('op', '-'):
                take()
                negations += 1
            elif (kind, value) == ('op', '+'):
                take()
            else:
                break
        node = atom()
        for _i in range(negations):
            node = negate(node)
        return node

    def negate(node):
        if node[0] == 'se':
            return ('se', node[1], -node[2])
        if node[0] == 'deb':
            return ('deb', node[1], -node[2])
        if node[0] == 'cre':
            return ('cre', node[1], -node[2])
        if node[0] == 'const':
            return ('const', -node[1])
        return ('neg', node)

    def atom():
        kind, value = take()
        if kind == 'num':
            return ('const', value)
        if kind in ('str', 'dq', 'sq'):
            return ('const', 0.0)
        if kind == 'op' and value == '(':
            node = expr()
            if take() != ('op', ')'):
                raise ValueError(_("Unbalanced parenthesis"))
            return node
        if kind == 'ident':
            func = value.upper()
            if take()[0] != 'op':
                raise ValueError(_("Malformed call to %s", func))
            if func == 'CMP':
                # CMP(x): "positive part" of x, i.e. max(x, 0). Used throughout
                # the legacy formules to split a signed amount between two
                # columns (e.g. Montant(+)/Montant(-), or Emplois/Ressources):
                # one side is CMP(x), the other CMP(-x), so exactly one of the
                # two is non-zero depending on the sign of x.
                node = expr()
                if take() != ('op', ')'):
                    raise ValueError(_("Unbalanced parenthesis in call to %s", func))
                return ('cmp', node)
            arg_kind, arg = take()
            if not str(arg).strip():
                raise ValueError(_("%s expects a quoted argument", func))
            closing = take()
            if closing != ('op', ')'):
                raise ValueError(_("Unbalanced parenthesis in call to %s", func))
            if arg_kind == 'num':
                arg = str(int(arg))
            else:
                arg = str(arg).strip()
            if func in ('EDI', 'EDIST'):
                return ('edi', arg)
            if func == 'SE':
                return ('se', arg, 1)
            if func == 'ASE':
                return ('ase', arg)
            if func == 'DEB':
                # DEB('xxx') → total DEBIT only of accounts whose code starts
                # with 'xxx' (no credit netted in, unlike SE/ASE).
                return ('deb', arg, 1)
            if func == 'CRE':
                # CRE('xxx') → total CREDIT only of accounts whose code starts
                # with 'xxx' (no debit netted in, unlike SE/ASE).
                return ('cre', arg, 1)
            if func in ('MDE', 'MCE', 'DE'):
                return ('const', 0.0)
            raise ValueError(_("Unsupported function: %s", func))
        raise ValueError(_("Unexpected token"))

    result = expr()
    if index[0] != len(tokens):
        raise ValueError(_("Trailing characters in formula"))
    return result


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #
def collect_account_prefixes(ast, formules, code, prefixes=None, stack=()):
    prefixes = set() if prefixes is None else prefixes
    if code in stack:
        return prefixes

    def walk(node):
        kind = node[0]
        if kind in ('se', 'ase' , 'deb', 'cre'):
            prefixes.add(node[1])
        elif kind == 'edi':
            ref = node[1]
            if ref not in stack:
                prefixes.update(collect_account_prefixes(
                    parse_formule(formules.get(ref, '')) if ref in formules else ('const', 0.0),
                    formules, ref, prefixes, stack + (code,),
                ))
        else:
            for part in node[1:]:
                if isinstance(part, tuple):
                    walk(part)

    walk(ast)
    return prefixes


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class AccountReportExpression_MaFiscal(models.Model):
    _inherit = 'account.report.expression'

    def _get_auditable_engines(self):
        return super()._get_auditable_engines() | {'custom'}


class AccountReport_MaFiscal(models.Model):
    _inherit = 'account.report'

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _fetch_formules_from_api(self, url, timeout=_API_TIMEOUT):
        """Attempt to fetch the formules map from the remote API.

        Returns a non-empty dict on success, or None on any failure.
        Logs a WARNING (not ERROR) so the fallback is silent in normal ops.
        """
        try:
            req = urllib.request.Request(
                url,
                headers={'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    _logger.warning(
                        "Formules API returned HTTP %s for %s", resp.status, url
                    )
                    return None
                raw = resp.read().decode('utf-8')

            data = json.loads(raw)
            raw_formules = data.get('formules') or {}

            if not raw_formules:
                _logger.warning("Formules API returned an empty formules map (%s)", url)
                return None

            formules = {
                str(k).strip(): _normalise_formule(str(v))
                for k, v in raw_formules.items()
            }
            _logger.debug(
                "Formules API: fetched %d formules from %s", len(formules), url
            )
            return formules

        except json.JSONDecodeError as exc:
            _logger.warning("Formules API returned invalid JSON (%s): %s", url, exc)
        except OSError as exc:
            # Covers URLError, socket.timeout, ConnectionRefusedError, etc.
            _logger.warning(
                "Formules API unreachable (%s): %s — using local fallback", url, exc
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Formules API unexpected error (%s): %s", url, exc, exc_info=True
            )
        return None

    def _fetch_formules_from_local(self):
        """Return the formules map from the local ma.fiscal.setting table.

        Handles both common return shapes:
          • list of (code, formule) tuples  →  _get_formules() returns a list
          • dict {code: formule}            →  _get_formules() returns a dict
        """
        try:
            result = self.env['ma.fiscal.setting']._get_formules()
        except Exception as exc:  # noqa: BLE001
            _logger.error("Cannot read local ma.fiscal.setting formules: %s", exc)
            return {}

        if isinstance(result, dict):
            return {
                str(k).strip(): _normalise_formule(str(v or '0'))
                for k, v in result.items()
            }

        # Assume iterable of (code, formule) pairs
        try:
            return {
                str(k).strip(): _normalise_formule(str(v or '0'))
                for k, v in result
            }
        except (TypeError, ValueError) as exc:
            _logger.error(
                "ma.fiscal.setting._get_formules() returned an unexpected format: %s", exc
            )
            return {}

    # ------------------------------------------------------------------ #
    # Public: get the resolved formules map (API → local fallback)
    # ------------------------------------------------------------------ #
    def _get_fiscal_year(self, options):
        """The fiscal year that selects which per-client formules snapshot
        to use: the year of the report's own selected period end date
        (``options['date']['date_to']``), the same for *every* column of
        this render regardless of its ``date_scope`` — including the N-1
        comparison column, which is computed with the active filter year's
        formulas, not year-1's (explicit product decision: formulas are
        versioned by the year the report is being filed for, not by the
        year each individual column happens to represent)."""
        date_to = options['date']['date_to']
        return int(str(date_to)[:4])

    def _get_formules_map(self, year):
        """Return the {code: formule} map used to compute the report, for
        this client's snapshot of `year`.

        Resolution order
        ----------------
        1. In-process cache (if still fresh — default TTL: 10 s).
        2. Remote API (http://app.ozirpaie.ma:2024/api/ma/formules or the URL
           stored in the ``ma_formules.api_url`` system parameter), called
           with ``client_id`` (``ma_formules.client_id`` system parameter)
           and ``year`` as query parameters so the instance returns this
           client's formulas for that specific fiscal year.
           • On success the result is cached and returned.
           • On ANY failure (network, bad JSON, empty response …) we fall
             through silently to step 3.
        3. Local ``ma.fiscal.setting`` table (CSV-imported rows).
           This is used transparently whenever the API is unreachable and its
           data are NOT cached, so the report keeps working even offline.
           NOTE: this fallback is a single global snapshot with no year
           dimension — it cannot be year-accurate, it's a last resort only.

        Cache TTL
        ---------
        Keep the TTL short (default 10 s) so that formula changes on the remote
        instance are reflected quickly.  Raise it via the system parameter
        ``ma_formules.api_cache_ttl`` only if the remote API is slow or you
        deliberately want a longer refresh window. Each `year` gets its own
        independent cache entry (the cache key is the full URL, including
        the client_id/year query string).

        To force an immediate refresh (e.g. after updating formulas), call::

            from odoo.addons.your_module.models.ma_fiscal_report import _invalidate_api_cache
            _invalidate_api_cache()   # clears all workers' caches on next restart
                                      # or use the "Vider le cache des formules" button
        """
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        reports_enabled = icp.get_param('ma_formules.reports_enabled')
        client_id = icp.get_param('ma_formules.client_id') if reports_enabled else False
        cache_ttl = _get_cache_ttl(icp)

        if client_id:
            query = urllib.parse.urlencode({'client_id': client_id, 'year': year})
            url = f"{base_url}?{query}"
        else:
            # No client_id configured yet (e.g. before the "Enregistrer ce
            # client" setup step has been run), or "Activer les formules
            # modifiables" is off: fall back to the plain, unscoped URL,
            # i.e. the shared master formulas — same behaviour as before
            # this feature existed.
            url = base_url

        # 1. Check process-level cache
        cached = _API_CACHE.get(url)
        if cached and cached['fetched_at'] + cache_ttl > time.time():
            _logger.debug("Formules: serving from cache (%s)", url)
            return cached['data']

        # 2. Try the remote API, unless it just failed (see _API_FAILURE_CACHE)
        if _API_FAILURE_CACHE.get(url, 0) <= time.time():
            formules = self._fetch_formules_from_api(url)
            if formules:
                _API_FAILURE_CACHE.pop(url, None)
                _API_CACHE[url] = {'fetched_at': time.time(), 'data': formules, 'ttl': cache_ttl}
                return formules
            retry_ttl = _get_failure_ttl(icp)
            _API_FAILURE_CACHE[url] = time.time() + retry_ttl
            _logger.warning(
                "Formules API unreachable (%s); not retrying for %ss", url, retry_ttl)

        # 3. Fallback: local table
        # If we have a stale cache entry (API was reachable before but is now
        # temporarily down), prefer it over the local table — it's more recent.
        if cached and cached.get('data'):
            _logger.warning(
                "Formules API down; serving STALE cache (age %.0fs) for %s",
                time.time() - cached['fetched_at'], url,
            )
            return cached['data']

        _logger.warning(
            "Formules API unavailable and no cache — falling back to local ma.fiscal.setting"
        )
        return self._fetch_formules_from_local()

    def _register_client_with_formules_api(self, company_name):
        """One-time call to the formules instance's registration route:
        creates (or, if already registered, just returns) a client_id for
        `company_name`, and triggers that instance's own cloning of the
        master formulas into a full per-year snapshot (2020 -> current
        year) for this client. Returns the client_id as a string.

        Raises UserError on any failure (missing secret, network error, the
        instance rejecting the request, ...) so the caller (the settings
        screen's button) can surface it directly to the user.
        """
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        secret = icp.get_param('ma_formules.register_secret')
        if not secret:
            raise UserError(_(
                "Le paramètre système 'ma_formules.register_secret' n'est pas configuré "
                "(Paramètres > Rapports fiscaux)."
            ))

        parsed = urllib.parse.urlparse(base_url)
        register_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, '/api/ma/clients/register', '', '', '')
        )

        req = urllib.request.Request(
            register_url,
            data=json.dumps({'name': company_name}).encode('utf-8'),
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Register-Secret': secret,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise UserError(_(
                "L'instance des formules a refusé l'enregistrement (HTTP %s) : %s"
            ) % (exc.code, body)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise UserError(_(
                "Impossible de contacter l'instance des formules pour l'enregistrement : %s"
            ) % exc) from exc

        client_id = data.get('client_id')
        if not client_id:
            raise UserError(_("Réponse inattendue de l'instance des formules : %s") % data)
        return str(client_id)

    def _unregister_client_with_formules_api(self, client_id):
        """One-time call to the formules instance's unregistration route:
        deletes this client and every formula row it owns (every fiscal
        year) — never the master/template rows.

        Raises UserError on any failure, same shape as
        _register_client_with_formules_api.
        """
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        secret = icp.get_param('ma_formules.register_secret')
        if not secret:
            raise UserError(_(
                "Le paramètre système 'ma_formules.register_secret' n'est pas configuré "
                "(Paramètres > Rapports fiscaux)."
            ))

        parsed = urllib.parse.urlparse(base_url)
        unregister_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, '/api/ma/clients/unregister', '', '', '')
        )

        req = urllib.request.Request(
            unregister_url,
            data=json.dumps({'client_id': client_id}).encode('utf-8'),
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Register-Secret': secret,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise UserError(_(
                "L'instance des formules a refusé la suppression (HTTP %s) : %s"
            ) % (exc.code, body)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise UserError(_(
                "Impossible de contacter l'instance des formules pour la suppression : %s"
            ) % exc) from exc

    def _update_formula_on_api(self, client_id, year, edi_code, new_formula):
        """Set one EDI code's formula for this client's one fiscal year on
        the remote formules instance — never the master/template rows, so
        this can never leak into any other client's reports.

        Raises UserError on any failure, same shape as
        _register_client_with_formules_api.
        """
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        secret = icp.get_param('ma_formules.register_secret')
        if not secret:
            raise UserError(_(
                "Le paramètre système 'ma_formules.register_secret' n'est pas configuré "
                "(Paramètres > Rapports fiscaux)."
            ))

        parsed = urllib.parse.urlparse(base_url)
        update_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, '/api/ma/clients/formules/update', '', '', '')
        )

        req = urllib.request.Request(
            update_url,
            data=json.dumps({
                'client_id': int(client_id),
                'year': int(year),
                'edi': edi_code,
                'formule': new_formula,
            }).encode('utf-8'),
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Register-Secret': secret,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise UserError(_(
                "L'instance des formules a refusé la modification (HTTP %s) : %s"
            ) % (exc.code, body)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise UserError(_(
                "Impossible de contacter l'instance des formules pour la modification : %s"
            ) % exc) from exc

        if not data.get('ok'):
            raise UserError(_("Réponse inattendue de l'instance des formules : %s") % data)
        return data.get('formule', new_formula)

    def action_clear_formules_cache(self):
        """Button action: wipe the in-process formules cache for this worker.

        Add a button on the report form view that calls this action so that you
        can force a fresh fetch from the API without restarting Odoo:

            <button name="action_clear_formules_cache" type="object"
                    string="Vider le cache des formules" class="btn-secondary"/>
        """
        _invalidate_api_cache()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Cache vidé"),
                'message': _("Le cache des formules a été vidé. La prochaine génération du rapport "
                             "interrogera l'API distante."),
                'sticky': False,
                'type': 'success',
            },
        }

    # ------------------------------------------------------------------ #
    # Audit domain
    # ------------------------------------------------------------------ #
    def _get_expression_audit_aml_domain(self, expression_to_audit, options):
        if (expression_to_audit.engine == 'custom'
                and expression_to_audit.formula == '_report_custom_engine_ma_fiscal'):
            subformula = (expression_to_audit.subformula or '').strip()
            match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
            if match:
                formules = self._get_formules_map(self._get_fiscal_year(options))
                code = match.group(1)
                try:
                    ast = parse_formule(formules.get(code, ''))
                except ValueError:
                    return None
                prefixes = collect_account_prefixes(ast, formules, code)
                if prefixes:
                    return Domain.OR([
                        [('account_id.code', '=like', f"{prefix}%")]
                        for prefix in prefixes
                    ])
            return None
        return super()._get_expression_audit_aml_domain(expression_to_audit, options)

    # ------------------------------------------------------------------ #
    # Static line rendering — bold (not grey) on ESG computed/total rows
    # ------------------------------------------------------------------ #
    # The E.S.G report needs three visual levels: table-separator titles
    # (grey background, level 0), computed/total rows (plain bold, like the
    # actif report's section headers) and detail rows (regular).
    # The framework bolds a static line only if it is a level-0 header
    # (which also paints a grey background) or an unfoldable group header.
    # A flat bold line is therefore expressed through the standard
    # ``o_bold_tr`` CSS class (same mechanism as the bank reconciliation
    # report), injected here for the explicit list of total-line codes.
    _BOLD_TOTAL_CODES = frozenset({
        'ESG_TFR_MARGE_BRUTE',
        'ESG_TFR_PRODUCTION',
        'ESG_TFR_CONSOMMATION',
        'ESG_TFR_VALEUR_AJOUTEE',
        'ESG_TFR_EBE',
        'ESG_TFR_RESULTAT_EXPLOITATION',
        'ESG_TFR_RESULTAT_FINANCIER',
        'ESG_TFR_RESULTAT_COURANT',
        'ESG_TFR_RESULTAT_NON_COURANT',
        'ESG_TFR_RESULTAT_NET',
        'ESG_CAF_CAF',
        'ESG_CAF_AUTOFINANCEMENT',
        'AMORT_NON_VALEURS',
        'AMORT_INCORPORELLES',
        'AMORT_CORPORELLES',
        'AMORT_TOTAL',
        'TVA15_A_FACTUREE',
        'TVA15_B_RECUPERABLE',
        'TVA15_C_DUE_CREDIT',
        'AFFECT_TOTAL_A',
        'AFFECT_TOTAL_B',
        'FUSION_TOTAL',
        'STOCK_SECTION_APPRO',
        'STOCK_SECTION_ENCQ',
        'STOCK_SECTION_PRODUITS_FINIS',
        'STOCK_SECTION_RESIDUELS',
        'STOCK_TOTAL_APPRO',
        'STOCK_TOTAL_ENCQ',
        'STOCK_TOTAL_PRODUITS_FINIS',
        'STOCK_TOTAL_RESIDUELS',
        'STOCK_TOTAL_GENERAL',
        'DEVISES_TOTAL_ENTREES',
        'DEVISES_TOTAL_SORTIES',
        'DEVISES_BALANCE',
        'DEVISES_TOTAL',
        'FIN_SYNTH_3',
        'FIN_SYNTH_6',
        'FIN_SYNTH_7',
        'FIN_EMPL_TOTAL_1',
        'FIN_EMPL_TOTAL_2',
        'FIN_EMPL_TOTAL_GENERAL',
        'IS_AUTRES_PRODUITS',
        'IS_AUTRES_PRODUITS_TOTAL',
        'IS_DENOMINATEUR',
        'IS_MONTANT_DU',
    })

    # Deficit/loss EDI codes whose cell text must always render red,
    # regardless of the value's sign (text color only, via Bootstrap's
    # text-danger utility class — see _build_column_dict below).
    _ALWAYS_RED_EDI_CODES = frozenset({
        '820',   # Perte nette (bilan_fisc)
        '840',   # Déficit brut fiscal si T2 > T1 (B) (bilan_fisc)
        '6875',  # ou déficit net fiscal (B) (bilan_fisc)
    })

    # ---- Dynamic Saisie detection (derives the editable set from the live
    #      formules map so that formula changes are picked up immediately) ----

    def _get_saisie_codes(self, options):
        """Return a frozenset of EDI codes whose current formule is a manual
        entry marker: ``"Saisie"``, or ``"Somme"`` (used by the legacy system
        for a few rows - e.g. "CA soumis au taux réduit" in the IS report -
        that have no deeper formula either, just a manually-entered total).

        The result is cached in *options* (a fresh dict per render) so the
        formules map is read at most once per report load.
        """
        codes = options.get('ma_saisie_codes')
        if codes is None:
            formules = self._get_formules_map(self._get_fiscal_year(options))
            codes = frozenset(
                c for c, f in formules.items()
                if f.strip().strip('"').strip().lower() in ('saisie', 'somme')
            )
            options['ma_saisie_codes'] = codes
        return codes

    def _get_lines(self, options, all_column_groups_expression_totals=None, warnings=None):
        """Prime the Saisie-codes cache so every sub-call can use it without
        repeating the formules-map lookup."""
        self._get_saisie_codes(options)
        return super()._get_lines(
            options,
            all_column_groups_expression_totals=all_column_groups_expression_totals,
            warnings=warnings,
        )

    def _get_static_line_dict(self, options, line, all_column_groups_expression_totals, parent_id=None):
        rslt = super()._get_static_line_dict(
            options, line, all_column_groups_expression_totals, parent_id=parent_id,
        )
        code = line.code or ''
        if code in self._BOLD_TOTAL_CODES or (code.startswith('DETAIL_CPC_') and code.endswith('_TOTAL')):
            rslt['class'] = 'o_bold_tr'
        return rslt

    # Decimals shown in the annex tables (dotations, emprunts, credit-bail,
    # titres, cessions, capital, locations). Odoo's _build_column_dict
    # defaults to digits=1 for every `float` column, so 291,06 was displayed
    # as 291,1 - the stored value was never rounded, only its display. The
    # liasse is kept to the centime, so the display must be too.
    _MA_FLOAT_DIGITS = 2

    def _ma_is_liasse_report(self):
        """True when every record is one of this module's reports.

        They all declare a custom handler named ``account.report.handler.ma_*``
        (see the data/bilan_*_report.xml files), which is what keeps the
        formatting tweaks below away from Odoo's standard reports - this
        method is inherited by every account.report in the database.
        """
        return bool(self) and all(
            (report.custom_handler_model_name or '').startswith(
                'account.report.handler.ma_')
            for report in self
        )

    def _build_column_dict(
        self, col_value, col_data, options=None, currency=False, digits=1,
        column_expression=None, has_sublines=False, report_line_id=None,
    ):
        # Show the centimes in the annex tables (see _MA_FLOAT_DIGITS). The
        # figure_type is read exactly as super() reads it: from the column
        # expression when there is one, from the column options otherwise
        # (the data-entry handlers build their cells without an expression).
        figure_type = (
            (column_expression.figure_type if column_expression else None)
            or (col_data or {}).get('figure_type')
        )
        if figure_type == 'float' and self._ma_is_liasse_report():
            digits = self._MA_FLOAT_DIGITS

        # Highlight every cell whose EDI code is currently a manual ("Saisie")
        # entry point; the web client gets the pencil via edit_popup_data.
        rslt = super()._build_column_dict(
            col_value, col_data, options=options, currency=currency, digits=digits,
            column_expression=column_expression, has_sublines=has_sublines,
            report_line_id=report_line_id,
        )
        if column_expression:
            subformula = column_expression.subformula or ''
            match = re.fullmatch(r'\s*-?\s*edi[_ ](\S+)\s*', subformula)
            if match:
                code = match.group(1)
                classes = []
                saisie_codes = self._get_saisie_codes(options or {})
                if code in saisie_codes and code not in _INLINE_DETAIL_CODES:
                    classes.append('ma-editable-cell')
                if code in self._ALWAYS_RED_EDI_CODES:
                    classes.append('text-danger')
                if classes:
                    rslt['class'] = ' '.join(classes)
        return rslt

    def _can_edit_formulas(self, options):
        """True if the current user may edit EDI formulas from the report
        cells (the pin icon) — "Activer les formules modifiables" must be
        on, this installation must be registered with a client_id (editing
        before that would have no per-client row to write into), and the
        current user must be in the stricter 'Editeur de formules' group.
        Cached in *options* (a fresh dict per render), same pattern as
        _get_saisie_codes.
        """
        cached = options.get('ma_can_edit_formulas')
        if cached is None:
            icp = self.env['ir.config_parameter'].sudo()
            reports_enabled = icp.get_param('ma_formules.reports_enabled')
            client_id = icp.get_param('ma_formules.client_id') if reports_enabled else False
            cached = bool(client_id) and self.env.user.has_group(
                'dbc_liasse_fiscale.group_ma_fiscal_formula_editor')
            options['ma_can_edit_formulas'] = cached
        return cached

    def _build_static_line_columns(self, line, options, all_column_groups_expression_totals, groupby_model=None):
        # The framework only creates edit_popup_data (the pencil button) for
        # engine='external' expressions in the XML.  Our expressions are all
        # 'custom'; instead of hardcoding which columns are editable we derive
        # it here from the current formules map, so changing a formule to or
        # from "Saisie" is picked up without touching the XML.
        columns = super()._build_static_line_columns(
            line, options, all_column_groups_expression_totals, groupby_model=groupby_model,
        )
        if len(options.get('companies', [])) == 1:
            saisie_codes = self._get_saisie_codes(options)
            can_edit_formulas = self._can_edit_formulas(options)
            year = self._get_fiscal_year(options)
            formules = self._get_formules_map(year) if can_edit_formulas else None
            line_expr_map = {expr.label: expr for expr in line.expression_ids}
            for col_dict, col_options in zip(columns, options['columns']):
                column_expression = line_expr_map.get(col_options['expression_label'])
                if not column_expression:
                    continue
                subformula = column_expression.subformula or ''
                match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
                if not match:
                    continue
                code = match.group(1)
                # A subtotal of the inline table is computed from its rows,
                # never keyed into the cell - whatever the formules map says
                # about it (see _INLINE_DETAIL_CODES and
                # MaFiscalEvaluator.evaluate_code). Offering the pencil here
                # would silently write an account.report.external.value the
                # evaluator no longer reads.
                if code in saisie_codes and code not in _INLINE_DETAIL_CODES:
                    figure_type = column_expression.figure_type or col_options.get('figure_type', 'monetary')
                    rounding = None
                    if figure_type == 'integer':
                        rounding = 0
                    elif figure_type == 'monetary':
                        rounding = self.env.company.currency_id.decimal_places
                    col_dict['edit_popup_data'] = json.dumps({
                        'column_group_key': col_options['column_group_key'],
                        'target_expression_id': column_expression.id,
                        'rounding': rounding,
                        'figure_type': figure_type,
                    })
                # Not `elif`: a Saisie column still has an underlying EDI
                # formula (the one that makes it "Saisie" in the first
                # place) and should get both icons — the pencil to key in
                # a value, and the pin to rewrite that formula itself
                # (e.g. to change it away from "Saisie").
                if can_edit_formulas:
                    col_dict['ma_formula_edit_data'] = json.dumps({
                        'column_group_key': col_options['column_group_key'],
                        'edi_code': code,
                        'current_formula': formules.get(code, ''),
                        'year': year,
                    })
        return columns

    def action_modify_manual_value(self, line_id, options, column_group_key, new_value_str, target_expression_id, rounding, json_friendly_column_group_totals):
        """Same contract as the stock account_reports method, but:
        * the recompute set also includes the 'custom' engine expressions: our
          EDI totals reference the manually-edited "Saisie" codes in their
          formules, so they must refresh right after a manual value is saved;
        * clearing the input of a "Saisie" cell deletes the saved value instead
          of writing 0, so the cell goes back to its blank "not entered" state.
        """
        self.ensure_one()

        target_column_group_options = self._get_column_group_options(options, column_group_key)
        self._init_currency_table(target_column_group_options)

        target_expression = self.env['account.report.expression'].browse(target_expression_id)
        subformula = target_expression.subformula or ''
        match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
        is_ma_saisie = bool(match) and match.group(1) in self._get_saisie_codes(target_column_group_options)

        if target_column_group_options.get('compute_budget'):
            expressions_to_recompute = self.env['account.report.expression'].browse(target_expression_id) \
                                       | self.line_ids.expression_ids.filtered(lambda x: x.engine == 'aggregation')
            self._action_modify_manual_budget_value(line_id, target_column_group_options, new_value_str, target_expression_id, rounding)
        elif is_ma_saisie and new_value_str == '':
            # Clear the manual value: remove every saved amount of the target
            # expression within the date bounds, for the current company.
            if len(target_column_group_options['companies']) > 1:
                raise UserError(_("Editing a manual report line is not allowed when multiple companies are selected."))
            date_from, date_to = self._get_date_bounds_info(target_column_group_options, target_expression.date_scope)
            clear_domain = [
                ('target_report_expression_id', '=', target_expression.id),
                ('company_id', '=', self.env.company.id),
                ('date', '<=', date_to),
            ]
            if date_from:
                clear_domain.append(('date', '>=', date_from))
            self.env['account.report.external.value'].search(clear_domain).unlink()
            expressions_to_recompute = self.line_ids.expression_ids.filtered(
                lambda x: x.engine in ('external', 'custom', 'aggregation'))
        else:
            expressions_to_recompute = self.line_ids.expression_ids.filtered(
                lambda x: x.engine in ('external', 'custom', 'aggregation'))
            self._action_modify_manual_external_value(target_column_group_options, new_value_str, target_expression_id, rounding)

        all_column_groups_expression_totals = self._convert_json_friendly_column_group_totals(
            json_friendly_column_group_totals,
            expressions_to_exclude=expressions_to_recompute,
        )
        recomputed_expression_totals = self._compute_expression_totals_for_each_column_group(
            expressions_to_recompute, options, forced_all_column_groups_expression_totals=all_column_groups_expression_totals)

        return {
            'lines': self._get_lines(options, all_column_groups_expression_totals=recomputed_expression_totals),
            'column_groups_totals': self._get_json_friendly_column_group_totals(recomputed_expression_totals),
        }

    def action_modify_formula(self, line_id, options, column_group_key, edi_code, new_formula):
        """Pin-icon counterpart of action_modify_manual_value: rewrites one
        EDI code's formula for THIS client's one fiscal year on the remote
        formules instance (never the master rows — see
        _update_formula_on_api), logs the change, then recomputes every
        'custom'/'aggregation' expression in the report (a formula change
        can ripple through any nested EDI(...) reference, not just the
        edited cell — same broad recompute set the Saisie branch above
        already uses).
        """
        self.ensure_one()
        if not self.env.user.has_group('dbc_liasse_fiscale.group_ma_fiscal_formula_editor'):
            raise UserError(_("Vous n'avez pas le droit de modifier les formules."))

        icp = self.env['ir.config_parameter'].sudo()
        if not icp.get_param('ma_formules.reports_enabled'):
            raise UserError(_(
                "Les formules modifiables ne sont pas activées (Paramètres > Rapports fiscaux)."
            ))
        client_id = icp.get_param('ma_formules.client_id')
        if not client_id:
            raise UserError(_(
                "Ce client n'est pas encore enregistré auprès de l'instance des formules "
                "(Paramètres > Rapports fiscaux)."
            ))

        target_column_group_options = self._get_column_group_options(options, column_group_key)
        self._init_currency_table(target_column_group_options)
        year = self._get_fiscal_year(target_column_group_options)

        try:
            parse_formule(new_formula)
        except ValueError as exc:
            raise UserError(_("Formule invalide : %s") % exc) from exc

        old_formula = self._get_formules_map(year).get(edi_code, '')
        self._update_formula_on_api(client_id, year, edi_code, new_formula)

        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        query = urllib.parse.urlencode({'client_id': client_id, 'year': year})
        _invalidate_api_cache(f"{base_url}?{query}")

        self.env['ma.fiscal.formula.log'].sudo().create({
            'edi_code': edi_code,
            'year': year,
            'client_id': client_id,
            'old_formula': old_formula,
            'new_formula': new_formula,
        })

        expressions_to_recompute = self.line_ids.expression_ids.filtered(
            lambda x: x.engine in ('external', 'custom', 'aggregation'))
        recomputed_expression_totals = self._compute_expression_totals_for_each_column_group(
            expressions_to_recompute, options)

        return {
            'lines': self._get_lines(options, all_column_groups_expression_totals=recomputed_expression_totals),
            'column_groups_totals': self._get_json_friendly_column_group_totals(recomputed_expression_totals),
        }

    def _empty_column_groups_totals(self, options):
        """The data-entry reports (Répartition du Capital Social, État des
        Emprunts, ...) have no expression to aggregate - every row is a
        literal value, not a computed formula - so there is never anything
        to put in the column-group totals the add/edit/delete actions
        below have to return."""
        return self._get_json_friendly_column_group_totals(
            {column_group_key: {} for column_group_key in options['column_groups']}
        )

    def _ma_data_entry_handler(self):
        """Resolve the current report's data-entry handler (model/fields
        it manages), or raise - defence in depth so action_ma_data_entry_*
        below can never be pointed, via a tampered client call, at a
        report that isn't one of ours."""
        self.ensure_one()
        handler_model = self.custom_handler_model_id.model
        # `self.env[model]` is a recordset, falsy when empty - which it
        # always is here, since a handler is an AbstractModel with no
        # records - so it can never be used in a plain truthiness check.
        handler = self.env[handler_model] if handler_model in self.env.registry else None
        if handler is None or not getattr(handler, '_ma_model', None):
            raise UserError(_("Ce rapport ne supporte pas cette action."))
        return handler

    def action_ma_data_entry_save(self, line_id, options, values):
        """Create or update one row of a data-entry report (Répartition du
        Capital Social, État des Emprunts, ...) from its popup - the
        header "+" button for a new row, or a row's pencil icon to edit
        it. Every field here is a plain value - there is no EDI code or
        formula involved, unlike action_modify_formula above. The row's
        own "annee_fiscale" field (part of handler._ma_fields like any
        other) is what the report's date filter later matches against -
        see AccountReportHandlerMaDataEntry._dynamic_lines_generator.

        `line_id` is the generic id of the row being edited, or falsy to
        create a new record for the current company.
        """
        handler = self._ma_data_entry_handler()
        record_id = self._get_res_id_from_line_id(line_id, handler._ma_model) if line_id else None
        clean_values = {
            field['name']: (values.get(field['name']) or False if field['type'] == 'date' else values.get(field['name']))
            for field in handler._ma_fields
            # A 'computed' field is never client-writable - whatever the
            # popup displayed is only a live preview, the model's own
            # create/write override is the one source of truth for it.
            if field['name'] in values and field['type'] != 'computed'
        }

        if record_id:
            self.env[handler._ma_model].browse(record_id).write(clean_values)
        else:
            clean_values['company_id'] = self.env.company.id
            self.env[handler._ma_model].create(clean_values)

        return {
            'lines': self._get_lines(options),
            'column_groups_totals': self._empty_column_groups_totals(options),
        }

    def action_ma_data_entry_delete(self, line_id, options):
        """Trash-icon counterpart of action_ma_data_entry_save: removes
        one row of a data-entry report for good."""
        handler = self._ma_data_entry_handler()
        record_id = self._get_res_id_from_line_id(line_id, handler._ma_model)
        if record_id:
            self.env[handler._ma_model].browse(record_id).unlink()

        return {
            'lines': self._get_lines(options),
            'column_groups_totals': self._empty_column_groups_totals(options),
        }

    def _ma_is_data_entry_report(self):
        """True for a pure data-entry report (Répartition du Capital
        Social, État des Emprunts, ...) - same falsy-empty-recordset trap
        as _ma_data_entry_handler above, but silent (returns a bool,
        raises nothing) since callers like _generate_common_warnings below
        need to just skip their own logic for these reports, not error
        out."""
        handler_model = self.custom_handler_model_id.model
        handler = self.env[handler_model] if handler_model in self.env.registry else None
        return (
            handler is not None
            and bool(getattr(handler, '_ma_model', None))
            # A report that only HOSTS an editable table inside its static
            # lines (Passage du RNC au RNF) is not a pure data-entry report:
            # it has real account.move data behind its other cells, so the
            # warnings this gates still apply to it.
            and not getattr(handler, '_ma_hosts_static_lines', False)
        )

    def _generate_common_warnings(self, options, warnings):
        if self._ma_is_data_entry_report():
            # These reports have no account.move behind them at all - the
            # "unposted Journal Entries" warning (and everything else this
            # generates) is meaningless noise here.
            return
        return super()._generate_common_warnings(options, warnings)

    def _init_options_ma_add_line_button(self, options, previous_options):
        """Expose the current report's "+" header-button config (label and
        field specs for the create/edit popup) to the web client, if this
        report is one of our data-entry reports. Auto-discovered and run
        for every report by the framework's _get_options_initializers_in_
        sequence (it scans `dir(self)` for every `_init_options_*`
        method) - a no-op for every other report, since
        'ma_add_line_button' is simply left unset.
        """
        handler_model = self.custom_handler_model_id.model
        # Same falsy-empty-recordset trap as _ma_data_entry_handler above -
        # `handler is None`, never a plain `not handler`.
        handler = self.env[handler_model] if handler_model in self.env.registry else None
        if handler is not None and getattr(handler, '_ma_model', None):
            date_to = options.get('date', {}).get('date_to')
            filter_year = str(fields.Date.from_string(date_to).year) if date_to else str(fields.Date.today().year)
            # _ma_fields is a shared module-level list (same object for
            # every render/company) - never mutate it in place. The
            # annee_fiscale field spec needs a per-render 'default' so a
            # brand-new row defaults to whichever year the report's date
            # filter is currently showing (not always the current year),
            # and a freshly recomputed 'selection' so next year's option
            # shows up the moment it starts, without needing a restart.
            fields_specs = [
                dict(f, default=filter_year, selection=_ma_fiscal_year_selection()) if f['name'] == 'annee_fiscale' else f
                for f in handler._ma_fields
            ]
            options['ma_add_line_button'] = {
                'label': handler._ma_add_button_label,
                'fields': fields_specs,
                # Only a PURE data-entry report wants the date column-group
                # header, the "Options" filter and the "En .DH" button hidden
                # (they are leftovers of the framework's options['date']
                # plumbing there). A static report hosting the table inline
                # has real columns and a real date filter - see the
                # t-inherit blocks in static/src/js/data_entry_popover.xml.
                'hide_date_header': not getattr(handler, '_ma_hosts_static_lines', False),
            }


# --------------------------------------------------------------------------- #
# Custom report handler
# --------------------------------------------------------------------------- #
class AccountReportHandlerMaFiscal(models.AbstractModel):
    _name = 'account.report.handler.ma_fiscal'
    _inherit = 'account.report.custom.handler'
    _description = 'Moroccan Bilan Report Custom Handler'

    def _report_custom_engine_ma_fiscal(
        self, expressions, options, date_scope,
        current_groupby, next_groupby,
        offset=0, limit=None, warnings=None,
    ):
        report = self.env['account.report'].browse(options['report_id'])
        evaluator = MaFiscalEvaluator(report, self.env, options, date_scope)
        results = {'has_sublines': False}

        for expression in expressions:
            subformula = expression.subformula or ''
            match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
            if not match:
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=expression.display_name, sub=subformula,
                ))
            code = match.group(1)
            if expression.figure_type == 'string':
                # Tableau 220's free-text cells: the manual value lives in
                # text_value, not value, so it is read directly instead of
                # going through evaluate_code (which is float arithmetic).
                # An empty one stays None = blank cell, same rule as below.
                results[subformula] = evaluator.manual_text(code) or None
            elif evaluator.is_saisie_code(code) and not evaluator.has_manual_value(code):
                # Not entered yet → blank cell (instead of showing 0).
                results[subformula] = None
            else:
                results[subformula] = evaluator.evaluate_code(code)

        return results

    def _report_custom_engine_ma_fiscal_scoped(
        self, expressions, options, date_scope,
        current_groupby, next_groupby,
        offset=0, limit=None, warnings=None,
    ):
        """_report_custom_engine_ma_fiscal, but a nested code that is a real
        cell somewhere in the liasse is evaluated with the date_scope THAT
        cell declares, instead of inheriting this one. See
        MaFiscalEvaluator._evaluate_nested_code for the why and the exact
        rule; only from_beginning cells are affected.

        Used by the Tableau de Financement, and by it alone, because it is the
        one report built entirely out of other reports' totals and out of
        differences between two balance-sheet dates:

          * its masses (Financement permanent, Actif immobilisé...) are the
            Bilan's own totals, whose sub-cells do not all share one scope -
            "Résultat net de l'exercice" is strict_range inside an otherwise
            from_beginning Passif total;
          * its flux (Capacité d'autofinancement, Acquisitions
            d'immobilisations, Emplois en non valeurs) are cells the ESG and
            the Tableau des Immobilisations declare strict_range, which
            cumulate every exercice when re-read at from_beginning;
          * its Variations columns are CMP(EDI(N-1 code) - EDI(N code)), i.e.
            one term per balance-sheet date, which no single scope can
            evaluate - both terms land on the same date and every variation
            cancels to 0,00 (the same trap as the stocks variation column,
            see _report_custom_engine_ma_stock_variation).

        Switching this report to the ordinary engine again would silently
        bring all three back.
        """
        report = self.env['account.report'].browse(options['report_id'])
        evaluator = MaFiscalEvaluator(
            report, self.env, options, date_scope, pin_declared_scopes=True)
        results = {'has_sublines': False}

        for expression in expressions:
            subformula = expression.subformula or ''
            match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
            if not match:
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=expression.display_name, sub=subformula,
                ))
            code = match.group(1)
            if evaluator.is_saisie_code(code) and not evaluator.has_manual_value(code):
                results[subformula] = None
            else:
                results[subformula] = evaluator.evaluate_code(code)

        return results

    def _report_custom_engine_ma_amort_cumul_fin(
        self, expressions, options, date_scope,
        current_groupby, next_groupby,
        offset=0, limit=None, warnings=None,
    ):
        """"Cumul d'amortissement fin exercice" = cumul début + dotations -
        amortissements sortis, each evaluated with ITS OWN date_scope (read
        from that line's own cumul_debut / dotations / amort_sorties cells -
        see below), instead of one shared scope for the whole formula.

        A plain ``EDI('debut_code')+EDI('dot_code')-EDI('sorties_code')`` formula run
        through ``_report_custom_engine_ma_fiscal`` can't do this: that engine uses a
        single date_scope for the whole formula tree, so the nested EDI(debut_code)
        would get re-evaluated at from_beginning too - silently becoming the
        period-END balance (which already nets in this year's sorties), on top of
        the formula's own explicit "-sorties" term, i.e. sorties gets counted twice
        (see bilan_amort_report.xml for the bug this fixes).

        Using Odoo's native 'aggregation' engine to sum the sibling cells instead
        isn't safe either: an unfilled "Saisie" (manual-entry) cell like dotations
        is deliberately None (blank) as a *cell* value, and 'aggregation' can't do
        arithmetic on None. evaluate_code() below always resolves a Saisie code to
        a real float (0.0 default), same as every other nested EDI() reference,
        avoiding that trap.

        subformula format: "cf_<debut_code>_<dotations_code>_<sorties_code>"
        (plain EDI codes, no 'edi_' prefix, underscore-separated).

        It MUST be a valid Python identifier: the framework extracts each
        expression's value out of this function's returned dict by calling
        ``safe_eval(expression.subformula, result_dict)`` - i.e. it evaluates
        the subformula *as a Python expression* using the returned dict as
        the namespace. That's how "edi_1236" works elsewhere (a bare name
        lookup). A comma-separated subformula like "1236,1237,1238" is not a
        name - Python parses it as the *tuple literal* (1236, 1237, 1238),
        ignoring the returned dict entirely and returning that tuple of raw
        EDI-code numbers as the "value", which then crashes trying to format
        it as a monetary amount.
        Each term's date_scope is read from that SAME line's own cumul_debut /
        dotations / amort_sorties expressions (not hardcoded here), so this
        function can never drift out of sync with whatever scope those cells
        are configured with in the XML - exactly what happened when Dotations
        was switched to strict_range but this function still hardcoded
        from_beginning for it, producing a cumul_fin that no longer matched
        debut + dotations - sorties as displayed on screen.
        """
        report = self.env['account.report'].browse(options['report_id'])
        evaluators_by_scope = {}

        def get_evaluator(scope):
            if scope not in evaluators_by_scope:
                evaluators_by_scope[scope] = MaFiscalEvaluator(report, self.env, options, scope)
            return evaluators_by_scope[scope]

        results = {'has_sublines': False}

        for expression in expressions:
            subformula = expression.subformula or ''
            match = re.fullmatch(r'\s*cf_(\d+)_(\d+)_(\d+)\s*', subformula)
            if not match:
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=expression.display_name, sub=subformula,
                ))
            debut_code, dotations_code, sorties_code = match.groups()

            siblings = {s.label: s for s in expression.report_line_id.expression_ids}
            debut_scope = siblings['cumul_debut'].date_scope if 'cumul_debut' in siblings else 'to_beginning_of_period'
            dotations_scope = siblings['dotations'].date_scope if 'dotations' in siblings else 'from_beginning'
            sorties_scope = siblings['amort_sorties'].date_scope if 'amort_sorties' in siblings else 'from_beginning'

            results[subformula] = (
                get_evaluator(debut_scope).evaluate_code(debut_code)
                + get_evaluator(dotations_scope).evaluate_code(dotations_code)
                - get_evaluator(sorties_scope).evaluate_code(sorties_code)
            )

        return results


    def _report_custom_engine_ma_stock_variation(
        self, expressions, options, date_scope,
        current_groupby, next_groupby,
        offset=0, limit=None, warnings=None,
    ):
        """"Variation de stock en valeur (+ ou -)" = stock initial net - stock
        final net, each term evaluated with ITS OWN date_scope, read from that
        same line's net_init / net_fin cells. Same fix, and for exactly the same
        reason, as _report_custom_engine_ma_amort_cumul_fin above.

        The master formula behind every one of these cells is
        ``EDI('<net_init code>')-EDI('<net_fin code>')``, which matches the DGI
        sheet's own column-7 header ("7 = 6 - 3"). Run through
        _report_custom_engine_ma_fiscal it can only ever return 0: that engine
        applies ONE date_scope to the whole formula tree, so the nested
        EDI(net_init code) is re-evaluated at the variation cell's own scope
        (from_beginning) instead of to_beginning_of_period - silently becoming
        the period-END balance, i.e. the stock final again. initial - final then
        cancels to 0.00 on every line, however much the stock actually moved.
        Flipping the cell's date_scope to to_beginning_of_period just breaks it
        the other way round (both terms become the opening balance).

        Unlike the cumul-fin engine this one takes no codes of its own in the
        subformula: it reads the two operands straight off the sibling
        net_init / net_fin expressions, both of which this very line already
        displays. So the subformula stays a plain "edi_<code>" naming the
        variation's own codeEdi - which keeps the cell visible to the EDI
        exporter (dbc_liasse_edi discovers cells by that subformula) and keeps
        it impossible for this column to disagree with the two columns it is
        the difference of.

        Reading the operands from the line also sidesteps two mistakes in the
        master formulas, which no date_scope could have rescued:
          * 1610 (Emballages perdus) reads EDI('1609')+EDI('1606') - a "+"
            where every other variation code has a "-".
          * 1670 (Travaux en cours) reads EDI('1669')-EDI('1658'), and 1658 is
            the net final of "Etudes en cours", the line above; the Travaux
            line's own net final is 1666.
        """
        report = self.env['account.report'].browse(options['report_id'])
        evaluators_by_scope = {}

        def get_evaluator(scope):
            if scope not in evaluators_by_scope:
                evaluators_by_scope[scope] = MaFiscalEvaluator(report, self.env, options, scope)
            return evaluators_by_scope[scope]

        def term(expression, siblings, label, default_scope):
            sibling = siblings.get(label)
            if not sibling:
                raise UserError(_(
                    "Line %(line)s has no %(label)s cell for %(expr)s to take "
                    "the variation de stock from.",
                    line=expression.report_line_id.display_name,
                    label=label, expr=expression.display_name,
                ))
            match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', sibling.subformula or '')
            if not match:
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=sibling.display_name, sub=sibling.subformula,
                ))
            return get_evaluator(sibling.date_scope or default_scope).evaluate_code(match.group(1))

        results = {'has_sublines': False}

        for expression in expressions:
            subformula = expression.subformula or ''
            if not re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula):
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=expression.display_name, sub=subformula,
                ))
            siblings = {s.label: s for s in expression.report_line_id.expression_ids}
            results[subformula] = (
                term(expression, siblings, 'net_init', 'to_beginning_of_period')
                - term(expression, siblings, 'net_fin', 'from_beginning')
            )

        return results


class AccountReportHandlerMaDataEntry(models.AbstractModel):
    """Shared dynamic-lines base for every "data-entry" report (no EDI
    code or formula behind any cell): one line per record of the
    subclass's model, for the current company, plus an always-present
    "Totaux" line summing whichever fields are marked summable=True - no
    account.report.line/expression records at all (same pattern as
    Odoo's own Partner Ledger), since the rows are created/edited/deleted
    by the user through the report's header "+" button and per-row
    pencil/trash icons, not declared in XML. Rows are scoped to a fiscal
    year via their own "annee_fiscale" Selection field (picked in the
    create/edit popup, see _MA_ANNEE_FISCALE_FIELD above) - the report's
    date filter (re-enabled for these reports, see data_entry_popover.xml)
    is what drives which year's rows get shown: the selected date's year
    is matched against annee_fiscale, not any accounting-period logic.

    A subclass just sets _ma_model (the model it manages),
    _ma_fields (one of the _MA_*_FIELDS specs above) and
    _ma_add_button_label (the report's header "+" button).

    A subclass can also set _ma_section_field to the name of a Selection
    field on _ma_model (e.g. 'type') to have its rows grouped under a bold
    section-header line per selection value - same look as the section
    lines (hierarchy_level 0) the static bilan_* reports use - instead of
    showing that field as an ordinary column. The field stays in
    _ma_fields (and so in the create/edit popup) either way; it is simply
    left out of the report's own account.report.column records.
    """
    _name = 'account.report.handler.ma_data_entry'
    _inherit = 'account.report.custom.handler'
    _description = "Moroccan Data-Entry Report - Shared Custom Handler"

    _ma_model = None
    _ma_fields = []
    _ma_add_button_label = ""
    _ma_section_field = None
    # Name of a Date field on _ma_model to scope rows by, INSTEAD of matching
    # their annee_fiscale against the filter's year, and how far back that
    # scope reaches:
    #   'to_period_end' (default) -> every row dated on or before the end of
    #       the selected period, whatever exercice it belongs to. What an
    #       état d'amortissements needs: an asset bought in 2020 is still being
    #       amortised in 2025 and must stay on the 2025 sheet.
    #   'strict_range'            -> only rows dated inside the selected
    #       period. What a tableau des locations needs: it lists the actes of
    #       the exercice being filed, nothing earlier.
    _ma_date_field = None
    _ma_date_scope = 'to_period_end'

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        company_ids = report.get_report_company_ids(options)
        domain = self._ma_records_domain(report, options)
        records = self.env[self._ma_model].search(domain)

        if self._ma_section_field:
            lines = []
            selection = dict(records._fields[self._ma_section_field].selection)
            for value, label in selection.items():
                section_records = records.filtered(lambda r, v=value: r[self._ma_section_field] == v)
                lines.append((0, self._ma_get_section_header_dict(report, options, value, label)))
                lines += [(0, self._ma_get_record_line_dict(report, options, record)) for record in section_records]
        else:
            lines = [(0, self._ma_get_record_line_dict(report, options, record)) for record in records]

        totals_line = self._ma_get_totals_line_dict(report, options, records)
        if totals_line:
            lines.append((0, totals_line))
        if not lines:
            # With zero rows and no Totaux/section line to keep this
            # non-empty, account_reports' own template swaps the whole
            # table - headers included - for its generic "nothing to
            # display" placeholder the moment controller.lines.length is
            # 0. A single blank line keeps the real header (and the "+"
            # button) visible with an empty body instead, which is what
            # we actually want for an otherwise-empty data-entry table.
            lines.append((0, self._ma_get_empty_line_dict(report, options)))
        return lines

    def _ma_records_domain(self, report, options):
        """Which rows of _ma_model belong to the period *options* selects.

        Two schemes, per handler:
        * _ma_date_field set -> rows selected on that date field, as far back
          as _ma_date_scope says: the whole history up to the end of the
          period ('to_period_end', the état des dotations aux amortissements,
          which lists every asset still being amortised whatever exercice it
          was acquired in) or the period alone ('strict_range', the tableau
          des locations et baux, which lists the actes of this exercice).
        * otherwise -> rows whose own annee_fiscale equals the filter's year.
        """
        domain = [('company_id', 'in', report.get_report_company_ids(options))]
        date_to = options.get('date', {}).get('date_to')
        if not date_to:
            return domain
        model_fields = self.env[self._ma_model]._fields
        if self._ma_date_field and self._ma_date_field in model_fields:
            date_to = fields.Date.from_string(date_to)
            domain.append((self._ma_date_field, '<=', date_to))
            if self._ma_date_scope == 'strict_range':
                domain.append((self._ma_date_field, '>=', self._ma_period_start(options, date_to)))
        elif 'annee_fiscale' in model_fields:
            domain.append(('annee_fiscale', '=', str(fields.Date.from_string(date_to).year)))
        return domain

    def _ma_period_start(self, options, date_to):
        """Lower bound of the selected period, for _ma_date_scope='strict_range'.

        options['date']['date_from'] can only be trusted in 'range' mode. These
        reports declare filter_date_range=False, and for a single date
        account_reports' own _init_options_date fills date_from with the start
        of date_to's *month* - so reading it blindly would hide every acte but
        December's. Fall back to the exercice date_to falls in, which is what
        "the year in the filter" means on a liasse.
        """
        date_options = options.get('date', {})
        if date_options.get('mode') == 'range' and date_options.get('date_from'):
            return fields.Date.from_string(date_options['date_from'])
        return self.env.company.compute_fiscalyear_dates(date_to)['date_from']

    def _ma_get_empty_line_dict(self, report, options):
        columns = [report._build_column_dict(None, col_options, options=options) for col_options in options['columns']]
        return {
            'id': report._get_generic_line_id('', None, markup='ma_empty_line'),
            'name': '',
            'level': 1,
            'columns': columns,
            'unfoldable': False,
            'unfold': False,
        }

    def _ma_get_section_header_dict(self, report, options, value, label):
        columns = [report._build_column_dict(None, col_options, options=options) for col_options in options['columns']]
        return {
            'id': report._get_generic_line_id('', None, markup=f'ma_section_{value}'),
            'name': label,
            'level': 0,
            'columns': columns,
            'unfoldable': False,
            'unfold': False,
            'class': 'o_bold_tr',
        }

    @staticmethod
    def _ma_json_safe_value(value, field_type):
        """A record's raw field value isn't always JSON-serialisable as-is
        (a Date field is a `datetime.date`, not a string) - this is what
        the create/edit popup's `<input type="date">` expects anyway, so
        converting it also doubles as the right format for that input."""
        if field_type == 'date' and value:
            return fields.Date.to_string(value)
        if value:
            return value
        return 0 if field_type == 'number' else ''

    def _ma_get_record_line_dict(self, report, options, record):
        fields_by_name = {field['name']: field for field in self._ma_fields}
        columns = []
        for col_options in options['columns']:
            label = col_options['expression_label']
            if label == 'ma_actions':
                col_dict = report._build_column_dict(None, col_options, options=options)
                col_dict['ma_edit_data'] = json.dumps({
                    'values': {
                        field['name']: self._ma_json_safe_value(record[field['name']], field['type'])
                        for field in self._ma_fields
                    },
                })
                col_dict['ma_delete_data'] = json.dumps({})
            else:
                value = record[label]
                field_def = record._fields.get(label)
                if field_def and field_def.type == 'selection':
                    value = dict(field_def.selection).get(value, value)
                field_spec = fields_by_name.get(label)
                if field_spec and field_spec.get('blank_if_field') and record[field_spec['blank_if_field']]:
                    value = None
                elif field_spec and field_spec.get('format') == 'percent' and value not in (None, False):
                    value = f"{value:.2f}%"
                col_dict = report._build_column_dict(value, col_options, options=options)
            columns.append(col_dict)

        return {
            'id': report._get_generic_line_id(self._ma_model, record.id),
            'name': '',
            'level': 1,
            'columns': columns,
            'unfoldable': False,
            'unfold': False,
        }

    def _ma_get_totals_line_dict(self, report, options, records):
        summable_names = [field['name'] for field in self._ma_fields if field.get('summable')]
        if not summable_names:
            return None

        columns = []
        for col_options in options['columns']:
            label = col_options['expression_label']
            value = sum(records.mapped(label)) if label in summable_names else None
            columns.append(report._build_column_dict(value, col_options, options=options))

        return {
            'id': report._get_generic_line_id('', None, markup='ma_totals_line'),
            'name': _("Totaux"),
            'level': 1,
            'columns': columns,
            'unfoldable': False,
            'unfold': False,
            'class': 'o_bold_tr',
        }


class AccountReportHandlerMaCapital(models.AbstractModel):
    _name = 'account.report.handler.ma_capital'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "Répartition du Capital Social - Custom Handler"

    _ma_model = 'ma.fiscal.capital.line'
    _ma_fields = _MA_CAPITAL_FIELDS
    _ma_add_button_label = "Ajouter un associé"


class AccountReportHandlerMaLoan(models.AbstractModel):
    _name = 'account.report.handler.ma_loan'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "État des Emprunts et Dettes Assimilées - Custom Handler"

    _ma_model = 'ma.fiscal.loan.line'
    _ma_fields = _MA_LOAN_FIELDS
    _ma_add_button_label = "Ajouter un prêt"
    _ma_section_field = 'type'


class AccountReportHandlerMaLeasing(models.AbstractModel):
    _name = 'account.report.handler.ma_leasing'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "Tableau des Biens en Crédit-Bail - Custom Handler"

    _ma_model = 'ma.fiscal.leasing.line'
    _ma_fields = _MA_LEASING_FIELDS
    _ma_add_button_label = "Ajouter un bien"


class AccountReportHandlerMaRental(models.AbstractModel):
    _name = 'account.report.handler.ma_rental'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "Tableau des Locations et Baux autres que le Crédit-Bail - Custom Handler"

    _ma_model = 'ma.fiscal.rental.line'
    _ma_fields = _MA_RENTAL_FIELDS
    _ma_add_button_label = "Ajouter une location"
    _ma_date_field = 'date_conclusion'
    _ma_date_scope = 'strict_range'


class AccountReportHandlerMaDotation(models.AbstractModel):
    _name = 'account.report.handler.ma_dotation'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "État des Dotations aux Amortissements - Custom Handler"

    _ma_model = 'ma.fiscal.dotation.line'
    _ma_fields = _MA_AMORT_FIELDS
    _ma_add_button_label = "Ajouter une immobilisation"
    _ma_date_field = 'date_entree'


class AccountReportHandlerMaTitres(models.AbstractModel):
    _name = 'account.report.handler.ma_titres'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "Tableau des Titres de Participation - Custom Handler"

    _ma_model = 'ma.fiscal.titres.line'
    _ma_fields = _MA_TITRES_FIELDS
    _ma_add_button_label = "Ajouter un titre de participation"


class AccountReportHandlerMaCession(models.AbstractModel):
    _name = 'account.report.handler.ma_cession'
    _inherit = 'account.report.handler.ma_data_entry'
    _description = "Tableau des Plus ou Moins Values sur Cessions ou Retraits d'Immobilisations - Custom Handler"

    _ma_model = 'ma.fiscal.cession.line'
    _ma_fields = _MA_CESSION_FIELDS
    _ma_add_button_label = "Ajouter une cession"
    # No _ma_date_field: scoped by annee_fiscale like the capital and titres
    # tables. "Date de cession ou de retrait" is a column of the tableau
    # (codeEdi 1929), and a cession belongs to the exercice it happened in -
    # it must not carry over to the next one the way an asset still being
    # amortised does (AccountReportHandlerMaDotation).


class AccountReportHandlerMaInlineTable(models.AbstractModel):
    """Shared base for a report that HOSTS an editable table inside its own
    static lines, rather than being one (the six handlers above).

    Such a report keeps every static line and every EDI-driven cell; a few
    of those lines additionally own a block of rows the user keys in, shown
    one level underneath them. So this inherits BOTH the fiscal engine
    (every other cell is computed from the formules map) and the data-entry
    base (the "+" button, the create/edit popup and the per-row pencil/trash
    all come from there) - and then turns off the one piece of the
    data-entry base that does not apply: its _dynamic_lines_generator, which
    would append the rows at the bottom of the report instead of under the
    line they belong to.

    A subclass sets _ma_model / _ma_fields / _ma_add_button_label like any
    data-entry handler, plus _ma_section_lines: {report.line xml_id: value of
    the model's `section` field}.

    _ma_hosts_static_lines is what tells the rest of the module this is the
    hybrid case: the client keeps the date column header and the "En .DH"
    button (_init_options_ma_add_line_button), and the report keeps the
    common warnings (_ma_is_data_entry_report).
    """
    _name = 'account.report.handler.ma_inline_table'
    _inherit = ['account.report.handler.ma_fiscal', 'account.report.handler.ma_data_entry']
    _description = "Report Hosting an Inline Editable Table - Shared Custom Handler"

    _ma_hosts_static_lines = True
    _ma_section_lines = {}

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        # The rows are not dynamic lines of their own: they are injected
        # under their section's static line by _custom_line_postprocessor.
        return []

    def _custom_line_postprocessor(self, report, options, lines):
        lines = super()._custom_line_postprocessor(report, options, lines)
        if not self._ma_section_lines:
            return lines

        date_to = options.get('date', {}).get('date_to')
        year = str(fields.Date.from_string(date_to).year) if date_to else str(fields.Date.today().year)
        records = self.env[self._ma_model].search([
            ('company_id', 'in', report.get_report_company_ids(options)),
            ('annee_fiscale', '=', year),
        ])

        # {report.line id: section} for the lines that own a block.
        section_by_line_id = {}
        for xmlid, section in self._ma_section_lines.items():
            section_line = self.env.ref(xmlid, raise_if_not_found=False)
            if section_line:
                section_by_line_id[section_line.id] = section

        out = []
        for line in lines:
            out.append(line)
            model, res_id = report._get_model_info_from_id(line['id'])
            if model != 'account.report.line':
                continue
            section = section_by_line_id.get(res_id)
            if not section:
                continue
            for record in records.filtered(lambda r, sec=section: r.section == sec):
                out.append(self._ma_inline_row_line_dict(report, options, line, record))
        return out

    def _ma_inline_row_line_dict(self, report, options, section_line, record):
        """One detail row, rendered one level under its section line.

        The intitulé goes in the line's own name (the first column of these
        reports is their label column, not an account.report.column), and the
        amounts land in their columns by matching the model's field names to
        the columns' expression labels - which is exactly what
        _ma_get_record_line_dict already does.
        """
        rslt = self._ma_get_record_line_dict(report, options, record)
        rslt['name'] = record.intitule or ''
        rslt['level'] = (section_line.get('level') or 1) + 1
        return rslt


class AccountReportHandlerMaPassage(models.AbstractModel):
    _name = 'account.report.handler.ma_passage'
    _inherit = 'account.report.handler.ma_inline_table'
    _description = "Passage du Résultat Net Comptable au Résultat Net Fiscal - Custom Handler"

    _ma_model = 'ma.fiscal.passage.line'
    _ma_fields = _MA_PASSAGE_FIELDS
    _ma_add_button_label = "Ajouter une ligne de détail"
    _ma_section_lines = _PASSAGE_SECTION_LINES


class AccountReportHandlerMaIs(models.AbstractModel):
    _name = 'account.report.handler.ma_is'
    _inherit = 'account.report.handler.ma_inline_table'
    _description = "État pour le Calcul de l'Impôt sur les Sociétés - Custom Handler"

    _ma_model = 'ma.fiscal.is.line'
    _ma_fields = _MA_IS_FIELDS
    _ma_add_button_label = "Ajouter une ligne de détail"
    _ma_section_lines = _IS_SECTION_LINES


# --------------------------------------------------------------------------- #
# Evaluator
# --------------------------------------------------------------------------- #
class MaFiscalEvaluator:

    # Reports where every EDI code whose OWN formula directly calls SE(...)
    # or ASE(...) must display as an absolute (always-positive) value,
    # regardless of the account's natural debit/credit sign. Requested
    # specifically for these two reports (not module-wide): "Tableau des
    # Provisions" and "Tableau des Amortissements" both have columns (Cumul
    # début, Reprises, ...) that are plain account-balance snapshots whose
    # sign flips depending on how the live formula happens to be written
    # (SE vs -SE / ASE vs -ASE), instead of being deliberately signed.
    _ABS_SE_ASE_REPORT_XMLIDS = (
        'dbc_liasse_fiscale.bilan_prov_report',
        'dbc_liasse_fiscale.bilan_amort_report',
    )

    def __init__(self, report, env, options, date_scope,
                 pin_declared_scopes=False, scope_pool=None):
        self.report = report
        self.env = env
        self.options = options
        self.date_scope = date_scope
        self.abs_se_ase = any(
            report == env.ref(xmlid, raise_if_not_found=False)
            for xmlid in self._ABS_SE_ASE_REPORT_XMLIDS
        )
        # Formules map is fetched ONCE per evaluator instance (= once per
        # report render), so all EDI codes on the same report page use the
        # exact same snapshot of the formules. The fiscal year is derived
        # from the report's own selected period (not from `date_scope`), so
        # an N-1 comparison column still uses the active filter year's
        # formulas — see AccountReport_MaFiscal._get_fiscal_year.
        self.formules = report._get_formules_map(report._get_fiscal_year(options))
        self._code_cache = {}       # memo: code → float (value from evaluate_code)
        self._manual_cache = {}     # memo: code → float (sum of saved manual values)
        self._manual_exists = {}    # memo: code → bool (any saved value in scope?)
        self._manual_text_cache = {}  # memo: code → str (tableau 220's text cells)
        self._prefix_sums = {}      # memo: prefix → (debit, credit)
        self._inline_cache = {}     # memo: code → float (inline table subtotal)
        # See _evaluate_nested_code: off by default, so every existing report
        # keeps evaluating a whole formula tree at the cell's own date_scope.
        self._pin_scopes = pin_declared_scopes
        # Shared between an evaluator and the per-scope siblings it spawns, so
        # the registry is built once and each scope's evaluator (with its own
        # memo caches) is reused for the whole render.
        self._scope_pool = scope_pool if scope_pool is not None else {}

        # Map every expression whose subformula is ``edi_<code>`` (with or
        # without the "editable " prefix) to the EDI code, so that
        # _manual_value / has_manual_value can resolve the DB ids of
        # account.report.external.value to search.
        code_to_expressions = {}
        for expression in report.line_ids.expression_ids:
            # (\w+) not (\d+): some EDI codes are alphanumeric (e.g. "ET_18A"
            # in the IS report), not just numeric ids like "1704".
            m = re.fullmatch(r'(?:editable\s+)?edi[_ ](\w+)', expression.subformula or '')
            if m:
                code_to_expressions.setdefault(m.group(1), set()).add(expression.id)
        self._code_to_expressions = {code: list(ids) for code, ids in code_to_expressions.items()}

    # ------------------------------------------------ Saisie helpers

    def is_saisie_code(self, code):
        """True if the code's current formule is a manual entry marker:
        the literal ``"Saisie"`` or ``"Somme"`` (see _get_saisie_codes)."""
        return self.formules.get(code, '').strip().strip('"').strip().lower() in ('saisie', 'somme')

    def _manual_target_expressions(self):
        """``{code: [expression ids]}`` for the expressions that ACTUALLY hold
        a stored manual value, anywhere in the database.

        _code_to_expressions, built in __init__, only knows the report being
        rendered, which is right for everything it is used for except one case:
        a formula that reaches a "Saisie" cell living on ANOTHER report. The
        Tableau de financement is built that way throughout - 14235 is
        EDI('10083'), whose terms are the "Acquisitions" cells keyed in on the
        Immobilisations report - so the manual value was invisible from here
        and the cell silently read 0 however much had been entered.

        This is keyed off account.report.external.value and NOT off the
        expression table, which matters for speed: there is one row per value
        somebody has actually typed (tens of them), against ~1900 custom
        expressions. A code nobody ever keyed a value for is therefore absent
        from this map and short-circuits to 0.0 with no query at all - exactly
        as the per-report map did before. Scanning every expression instead
        made each nested code pay an external-value search and took a report
        render from under a second to over a minute.

        Cached on *options*, a fresh dict per render, so it is built once
        however many evaluators (one per date_scope) a render constructs.
        """
        mapping = self.options.get('ma_manual_target_expressions')
        if mapping is None:
            mapping = {}
            targets = self.env['account.report.external.value'].search_read(
                [('company_id', 'in', self.report.get_report_company_ids(self.options))],
                ['target_report_expression_id'],
            )
            expression_ids = {
                row['target_report_expression_id'][0]
                for row in targets if row.get('target_report_expression_id')
            }
            for expression in self.env['account.report.expression'].browse(
                    sorted(expression_ids)).exists():
                match = re.fullmatch(
                    r'(?:editable\s+)?edi[_ ](\w+)', (expression.subformula or '').strip())
                if match:
                    mapping.setdefault(match.group(1), []).append(expression.id)
            self.options['ma_manual_target_expressions'] = mapping
        return mapping

    def _expressions_for_manual(self, code):
        """Expressions whose stored manual value is this code's, preferring the
        report being rendered and falling back to wherever the value lives."""
        return self._code_to_expressions.get(code) or self._manual_target_expressions().get(code)

    def _manual_domain(self, expression_ids):
        """Domain that scopes account.report.external.value to the current
        date range + companies of this evaluator instance."""
        date_from, date_to = self.report._get_date_bounds_info(self.options, self.date_scope)
        domain = [
            ('target_report_expression_id', 'in', expression_ids),
            ('date', '<=', date_to),
            ('company_id', 'in', self.report.get_report_company_ids(self.options)),
        ]
        if date_from:
            domain.append(('date', '>=', date_from))
        return domain

    def has_manual_value(self, code):
        """True if at least one manual value record exists for *code* in the
        current period/company scope (used to decide blank vs 0 display)."""
        if code in self._manual_exists:
            return self._manual_exists[code]
        expression_ids = self._expressions_for_manual(code)
        if not expression_ids:
            self._manual_exists[code] = False
            return False
        domain = self._manual_domain(expression_ids)
        exists = bool(self.env['account.report.external.value'].search_count(domain))
        self._manual_exists[code] = exists
        return exists

    def _manual_value(self, code):
        """Sum of the saved manual amounts (account.report.external.value)
        stored for the expression(s) bound to *code*, within the date bounds
        and companies of the current evaluator scope.  Returns 0 when nothing
        was entered yet (arithmetic-safe default).
        """
        if code in self._manual_cache:
            return self._manual_cache[code]
        expression_ids = self._expressions_for_manual(code)
        if not expression_ids:
            self._manual_cache[code] = 0.0
            return 0.0
        domain = self._manual_domain(expression_ids)
        value = sum(self.env['account.report.external.value'].search(domain).mapped('value') or [0.0])
        self._manual_cache[code] = value
        return value

    def manual_text(self, code):
        """The saved manual TEXT of *code* (account.report.external.value's
        text_value), or '' when nothing was entered yet.

        Tableau 220 (Principales Méthodes d'Evaluation) is the one tableau of
        the liasse whose 23 cells are free text, not amounts: every one of its
        codes is "Saisie" in the formules map, and no other cell's formula
        ever references them.  Odoo stores a manual value in `value` or in
        `text_value` depending on the target expression's figure_type, so
        _manual_value's sum() reads 0.0 for these - which is why they are read
        here instead, and why the engine (not evaluate_code) decides which of
        the two to call: evaluate_code feeds arithmetic, and must keep
        returning floats.

        Several rows can exist for one code (one per date in scope); the most
        recent non-empty one wins, which matches what the cell shows on
        screen after an edit.
        """
        if code in self._manual_text_cache:
            return self._manual_text_cache[code]
        expression_ids = self._expressions_for_manual(code)
        if not expression_ids:
            self._manual_text_cache[code] = ''
            return ''
        records = self.env['account.report.external.value'].search(
            self._manual_domain(expression_ids), order='date ASC, id ASC')
        text = ''
        for record in records:
            if (record.text_value or '').strip():
                text = record.text_value
        self._manual_text_cache[code] = text
        return text

    # ------------------------------ Tableau 7 inline-table subtotals

    def _inline_detail_total(self, code):
        """Sum of the editable rows behind one of the inline-table subtotals
        (tableau 7's réintégrations/déductions, tableau 240's taux réduit and
        autres produits), or None if *code* is not one of them.

        These eight codes are resolved here rather than through the formules
        map on purpose. They are the only cells of the liasse whose value is
        a sum of rows the user keys in INSIDE the report, and the DGI grid
        has no subtotal cell for them at all (C_822 is the five-row range
        'T3'!$B$13:$B$17, not a total) - so there is no formula that could
        be right, and a "Saisie" value keyed straight into the cell would
        disagree with the rows shown underneath it the moment either changed.

        Scope note: the rows carry their own annee_fiscale, exactly like the
        six data-entry annexes, so they follow the report's date filter and
        not `date_scope` - an N-1 comparison column of this report shows the
        same rows as its N column, which is also what the formules-driven
        cells of a Saisie code do.
        """
        spec = _INLINE_DETAIL_CODES.get(code)
        if not spec:
            return None
        if code in self._inline_cache:
            return self._inline_cache[code]
        model_name, section, field_name = spec
        date_to = self.options.get('date', {}).get('date_to')
        year = str(fields.Date.from_string(date_to).year) if date_to else str(fields.Date.today().year)
        rows = self.env[model_name].search([
            ('company_id', 'in', self.report.get_report_company_ids(self.options)),
            ('annee_fiscale', '=', year),
            ('section', '=', section),
        ])
        value = sum(rows.mapped(field_name))
        self._inline_cache[code] = value
        return value

    # ------------------------------------------------------------------ EDI
    def evaluate_code(self, code, stack=()):
        code = str(code).strip()
        if code in self._code_cache:
            return self._code_cache[code]

        # Before anything else: a subtotal of an inline editable table is
        # owned by that table, whatever (if anything) the formules map holds
        # for it.
        inline_total = self._inline_detail_total(code)
        if inline_total is not None:
            self._code_cache[code] = inline_total
            return inline_total
        if code in stack:
            raise UserError(_(
                "Circular EDI reference detected: %(chain)s → %(code)s",
                chain=' → '.join(stack), code=code,
            ))
        raw_formule = self.formules.get(code, '')
        if not raw_formule:
            # Code not found in either source → treat as zero, log once
            _logger.warning("EDI code %r not found in formules map — returning 0", code)
            self._code_cache[code] = 0.0
            return 0.0

        # Manually entered ("Saisie" or "Somme") codes resolve to the saved
        # manual value (account.report.external.value), not to a formule.
        if raw_formule.strip().strip('"').strip().lower() in ('saisie', 'somme'):
            value = self._manual_value(code)
            self._code_cache[code] = value
            return value

        try:
            ast = parse_formule(raw_formule)
        except ValueError as exc:
            raise UserError(_(
                "Cannot parse formule of EDI code %(code)s (%(formule)r): %(error)s",
                code=code, formule=raw_formule, error=exc,
            ))

        value = self._eval_node(ast, stack + (code,))
        if (
            self.abs_se_ase
            and re.search(r"A?SE\(", raw_formule)
            and not raw_formule.strip().startswith('-')
        ):
            # Take the abs of the fully-combined value (not of each SE/ASE
            # term before combining them), so a formula like
            # SE('613')+SE('614')-SE('6148') still nets its terms correctly
            # before the sign gets normalized. A formula written with an
            # explicit leading '-' (e.g. -ASE('2834')) is left alone: that
            # minus is the formula author's deliberate sign choice, not
            # convention drift - only a *bare* SE/ASE (no leading '-') gets
            # auto-corrected to positive.
            value = abs(value)
        self._code_cache[code] = value
        return value

    # ------------------------------------- nested codes and their scopes

    def _evaluate_nested_code(self, code, stack):
        """Resolve a code referenced from INSIDE another code's formula.

        By default that is simply evaluate_code() on this same evaluator: the
        nested code inherits the cell's date_scope, which is what every report
        but the Tableau de Financement relies on.

        When this evaluator was built with pin_declared_scopes=True, a nested
        code that is ALSO a real cell somewhere in the liasse is instead
        evaluated with the date_scope that cell declares. The Tableau de
        Financement needs this because it is assembled out of other reports'
        totals: EDI('13385') (Financement permanent) nests EDI('13409')
        "Résultat net de l'exercice" = -SE('7')-SE('6'), which the Bilan
        Passif declares strict_range precisely because Odoo never zeroes class
        6/7 at year end. Re-evaluated at this cell's from_beginning it returns
        every exercice's result added together, inflating Financement
        permanent (and so the FDR and the Trésorerie nette under it).

        Only from_beginning is corrected, and deliberately so: it is the scope
        that silently cumulates a flux code, whereas to_beginning_of_period is
        how the N-1 columns of this report shift a balance back one year, and
        pinning there would turn their nested flux codes back into the current
        exercice's, making column (b) repeat column (a).
        """
        if self._pin_scopes and self.date_scope == 'from_beginning':
            declared = self._declared_scopes().get(code)
            if declared and declared != self.date_scope:
                return self._scoped_sibling(declared).evaluate_code(code, stack)
        return self.evaluate_code(code, stack)

    def _declared_scopes(self):
        """{edi code: date_scope} over every cell of every Moroccan report.

        A code's scope turns out to be a property of the CODE, not of the
        report showing it: of the 1554 codes declared as a cell somewhere, not
        one is declared with two different scopes. So this registry is
        unambiguous, and it is read from the reports themselves rather than
        hardcoded, so it cannot drift when a scope is changed in XML.
        """
        if self._scope_pool.get('scopes') is None:
            scopes = {}
            rows = self.env['account.report.expression'].search_read(
                [('engine', '=', 'custom'), ('subformula', '!=', False)],
                ['subformula', 'date_scope'],
            )
            for row in rows:
                match = re.fullmatch(
                    r'\s*-?\s*(?:editable\s+)?edi[_ ](\w+)\s*', row['subformula'] or '')
                if match:
                    scopes.setdefault(match.group(1), row['date_scope'])
            self._scope_pool['scopes'] = scopes
        return self._scope_pool['scopes']

    def _scoped_sibling(self, scope):
        """This evaluator's twin for another date_scope, created once."""
        pool = self._scope_pool.setdefault('evaluators', {})
        if scope not in pool:
            pool[scope] = MaFiscalEvaluator(
                self.report, self.env, self.options, scope,
                pin_declared_scopes=True, scope_pool=self._scope_pool,
            )
        return pool[scope]

    def _eval_node(self, node, stack):
        kind = node[0]
        if kind == 'const':
            return node[1]
        if kind == 'neg':
            return -self._eval_node(node[1], stack)
        if kind == 'cmp':
            value = self._eval_node(node[1], stack)
            return value if value > 0 else 0.0
        if kind == 'bin':
            left = self._eval_node(node[2], stack)
            right = self._eval_node(node[3], stack)
            op = node[1]
            if op == '+':
                return left + right
            if op == '-':
                return left - right
            if op == '*':
                return left * right
            if right == 0:
                raise UserError(_("Division by zero while evaluating an EDI formule"))
            return left / right
        if kind == 'edi':
            return self._evaluate_nested_code(node[1], stack)
        if kind == 'se':
            debit, credit = self._get_prefix_sums(node[1])
            # # SE('xxx') → absolute balance (always positive)
            # return abs(debit - credit)
            # SE('xxx') → signed balance; -SE('xxx') → its negation
            return (debit - credit) * node[2]
        if kind == 'ase':
            debit, credit = self._get_prefix_sums(node[1])
            # ASE('xxx') → signed balance
            return debit - credit
        if kind == 'deb':
            debit, _credit = self._get_prefix_sums(node[1])
            # DEB('xxx') → total DEBIT only (never nets in the credit side);
            # -DEB('xxx') → its negation.
            return debit * node[2]
        if kind == 'cre':
            _debit, credit = self._get_prefix_sums(node[1])
            # CRE('xxx') → total CREDIT only (never nets in the debit side);
            # -CRE('xxx') → its negation.
            return credit * node[2]
        raise UserError(_("Unknown formule node kind: %s", kind))

    # ----------------------------------------------- account prefix totals
    def _get_prefix_sums(self, prefix):
        """Return (total_debit, total_credit) for all journal items posted on
        accounts whose code starts with *prefix*, within the date scope of this
        evaluator.  Results are memoised for the duration of the render.
        """
        if prefix in self._prefix_sums:
            return self._prefix_sums[prefix]

        if not re.fullmatch(r'[0-9A-Za-z]+', prefix):
            raise UserError(_("Invalid account code prefix: %r") % prefix)

        query = self.report._get_report_query(
            self.options,
            self.date_scope,
            domain=[('account_id.code', '=like', f"{prefix}%")],
        )
        self.env.cr.execute(SQL(
            """
                SELECT COALESCE(SUM(%(debit)s), 0.0),
                       COALESCE(SUM(%(credit)s), 0.0)
                  FROM %(table_references)s
                       %(currency_table_join)s
                 WHERE %(search_condition)s
            """,
            debit=self.report._currency_table_apply_rate(SQL("account_move_line.debit")),
            credit=self.report._currency_table_apply_rate(SQL("account_move_line.credit")),
            table_references=query.from_clause,
            currency_table_join=self.report._currency_table_aml_join(self.options),
            search_condition=query.where_clause,
        ))
        debit, credit = self.env.cr.fetchone()
        result = (debit or 0.0, credit or 0.0)
        self._prefix_sums[prefix] = result
        return result