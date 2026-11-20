# -*- coding: utf-8 -*-
"""The coherence checks of the liasse - the engine behind the Incohérences page.

This module holds the checks and the figures; the page that displays them lives
in incoherences.py, whose handler inherits this one. There is deliberately no
account.report record pointing here any more: a first, plainly-formatted report
was tried and dropped in favour of the workbook-styled page, but its lines
generator is kept as the fallback the page uses when it is asked for lines
without having computed its payload (a hand-built options dict, a stale client)
and as what the PDF/XLSX exports ultimately rely on.


The reference workbook (Liasse_Fiscale_Unlocked.xlsm) carries an
"Incoherences" sheet: 26 cross-checks between its tableaux, each printing the
two amounts compared and their difference, with two counters on top (gaps
above 0,10 and gaps between 0,01 and 0,10). It is the only safety net the
Excel tool offers before filing.

This module is the same idea computed on OUR data: every figure comes from the
liasse reports themselves, through the very code path the EDI export uses
(`_dbc_edi_all_values`), so the screen and the exported file can never disagree
about a number.

Two checks the workbook does not have are added:
  * the T14 (affectation des resultats) against the Passif, which is where a
    reconstructed opening balance shows up;
  * an EDI coverage inventory - how many cells each tableau would contribute to
    the file, and which tableaux would contribute none. No spreadsheet can see
    a forgotten annex; this does.

One check of the workbook is deliberately left out: its first one, total debit
vs total credit of the balance. Double entry guarantees it in Odoo, so it would
read 0,00 for ever and detect nothing.
"""
import logging

from odoo import _, models

from .account_report import (
    _ANNEX_TABLES,
    _ANNEX_TOTALS,
    _REPORT_TABLEAU,
    _is_blank_cell,
)

_logger = logging.getLogger(__name__)

# The workbook's own two thresholds (Incoherences!C4 and C5): a gap of 0,01 to
# 0,10 is a rounding artefact worth seeing but not worth chasing, above 0,10 is
# a real inconsistency. Below 0,01 nothing is reported at all.
_SEUIL_MINEUR = 0.01
_SEUIL_INCOHERENCE = 0.10


def _codes(*codes):
    """A term made of codeEdi cells: `_codes('497', '502', '-207')`.

    A leading '-' subtracts that cell. Returns None for an empty list so a
    check can declare "no equivalent for the previous exercice".
    """
    if not codes:
        return None
    return ('codes', tuple(
        (-1.0, code[1:]) if code.startswith('-') else (1.0, code)
        for code in codes
    ))


def _annex(tableau_id, field, scope='all'):
    """A term summing one column of an annex table whose total has no codeEdi.

    `scope='exercice'` keeps only the rows dated inside the exercice, which is
    what "acquisitions de l'exercice" means on the T16 - its rows otherwise
    reach back to every asset still being amortised.
    """
    return ('annex', tableau_id, field, scope)


# (libelle A, terme A, terme A exercice precedent,
#  libelle B, terme B, terme B exercice precedent)
#
# The codes are those the liasse reports declare through their `edi_<code>`
# subformulas; the annex totals (1088..1094, 1279/1280, 2038..2043) are the
# "Totaux" rows of the unlimited tables, summed from the records themselves.
_CHECKS = (
    # ---- Bilan ---------------------------------------------------------
    ("Total Actif",
     _codes('188'), _codes('189'),
     "Total Passif",
     _codes('13368'), _codes('13453')),

    ("Résultat net du Bilan Passif",
     _codes('13409'), _codes('13420'),
     "Résultat net du C.P.C.",
     _codes('11438'), _codes('11439')),

    ("Résultat net du Bilan Passif",
     _codes('13409'), _codes('13420'),
     "Résultat net de l'E.S.G. (T5)",
     _codes('1332'), _codes('1357')),

    ("Emplois du tableau de financement",
     _codes('14281'), _codes('14283'),
     "Ressources du tableau de financement",
     _codes('14282'), _codes('14284')),

    # ---- Immobilisations ------------------------------------------------
    ("Immobilisations brutes de l'Actif (hors immob. financières)",
     _codes('497', '502', '507'), None,
     "Immobilisations du T4 (fin d'exercice)",
     _codes('14052'), None),

    ("Immobilisations brutes de l'Actif (hors financières, terrains et en cours)",
     _codes('497', '502', '507', '-207', '-213'), None,
     "Immobilisations du T16",
     _codes('1088'), None),

    ("Acquisitions de l'exercice du T4 (hors immob. en cours)",
     _codes('14046', '14047', '-991', '-1005'), None,
     "Acquisitions de l'exercice du T16",
     _annex(12, 'prix_acquisition', scope='exercice'), None),

    ("Cessions et retraits du T4 (valeurs brutes)",
     _codes('14049', '14050'), None,
     "Cessions et retraits du T10",
     _codes('2038'), None),

    ("Produits de cession des immobilisations du C.P.C.",
     _codes('11343'), _codes('11344'),
     "Produits de cession des immobilisations du T10",
     _codes('2041'), None),

    ("V.N.A. des immobilisations cédées du C.P.C.",
     _codes('11373'), _codes('11374'),
     "V.N.A. des immobilisations cédées du T10",
     _codes('2040'), None),

    # ---- Amortissements et provisions -----------------------------------
    ("Dotations totales du C.P.C.",
     _codes('11243', '11313', '11388'), _codes('11244', '11314', '11389'),
     "Dotations du T8 + T9 (exploitation + financières + non courantes)",
     _codes('14062', '1922', '1923', '1924'), None),

    ("Dotations aux amortissements du T8",
     _codes('14062'), None,
     "Dotations aux amortissements du T16",
     _codes('1093'), None),

    ("Reprises totales du C.P.C.",
     _codes('11213', '11288', '11363'), _codes('11214', '11289', '11364'),
     "Reprises du T9 (exploitation + financières + non courantes)",
     _codes('1925', '1926', '1927'), None),

    ("Amortissements cumulés - début d'exercice du T8",
     _codes('14061', '-14063'), None,
     "Amortissements antérieurs du T16",
     _codes('1090'), None),

    ("Amortissements cumulés - fin d'exercice du T8",
     _codes('14064'), None,
     "Amortissements cumulés du T16",
     _codes('1094'), None),

    ("Amortissements cumulés - fin d'exercice du T8",
     _codes('14064'), None,
     "Amortissements cumulés du Bilan Actif",
     _codes('498', '503', '508'), None),

    ("Amortissements des immobilisations sorties du T8",
     _codes('14063'), None,
     "Amortissements des immobilisations cédées ou retirées du T10",
     _codes('2039'), None),

    # ---- Charges externes et capital ------------------------------------
    ("Redevances de crédit-bail du T7",
     _annex(23, 'montant_redevances_exercice'), None,
     "Redevances de crédit-bail du T6",
     _codes('1535'), _codes('1576')),

    ("Loyers du T19",
     _codes('1280'), None,
     "Locations et charges locatives du T6",
     _codes('1534'), _codes('1575')),

    ("Montant du capital au Bilan Passif",
     _codes('13400'), _codes('13411'),
     "Montant du capital au T13",
     _annex(41, 'montant_souscrit'), None),

    # ---- Variations de stocks (T6 contre T20) ----------------------------
    ("Variation des stocks de marchandises au T6",
     _codes('1523'), _codes('1564'),
     "Variation des stocks de marchandises au T20",
     _codes('1415'), None),

    ("Variation des stocks de matières premières au T6",
     _codes('1527'), _codes('1568'),
     "Variation des stocks de matières premières au T20",
     _codes('1455'), None),

    ("Variation des stocks de matières, fournitures et emballages au T6",
     _codes('1529'), _codes('1570'),
     "Variation des stocks de matières, fournitures et emballages au T20",
     _codes('1463', '1471', '1487'), None),

    ("Variation des stocks de produits finis au T6",
     _codes('1977', '1978'), _codes('2003', '2004'),
     "Variation des stocks de produits finis au T20",
     _codes('-1806'), None),

    ("Variation des stocks de produits en cours au T6",
     _codes('1979'), _codes('2005'),
     "Variation des stocks de produits en cours au T20",
     _codes('-1691'), None),

    # ---- Affectation des résultats (absent du classeur) ------------------
    ("T14 - TOTAL A (origines des résultats à affecter)",
     _codes('481'), None,
     "T14 - TOTAL B (affectation des résultats)",
     _codes('495'), None),

    ("T14 - Résultat net de l'exercice à affecter",
     _codes('475'), None,
     "Résultat net de l'exercice précédent au Bilan Passif",
     _codes('13420'), None),

    ("T14 - Report à nouveau après affectation",
     _codes('493'), None,
     "Report à nouveau du Bilan Passif",
     _codes('13408'), None),
)


class AccountReportHandlerMaCoherence(models.AbstractModel):
    """Shared engine of the liasse coherence checks (no report of its own)."""
    _name = 'account.report.handler.ma_coherence'
    _inherit = 'account.report.custom.handler'
    _description = "Contrôles de cohérence de la liasse - moteur partagé"

    # ------------------------------------------------------------ valeurs

    def _coh_values(self, company, date_from, date_to, all_entries):
        """({codeEdi: value}, {tableau_id: cells}) for the whole liasse.

        The 25 reports are all rendered, not just the ones the checks read:
        the coverage inventory has to see every tableau, including the ones
        that turn out empty - that is the whole point of it.
        """
        values, per_tableau = self._dbc_edi_all_values(date_from, date_to, all_entries)

        # The "Totaux" rows of the unlimited tables carry codeEdis too, but the
        # export only materialises them at serialisation time, so they are not
        # in `values` yet. The checks need them (2038..2043, 1088..1094,
        # 1279/1280), and they cannot collide with a report's own codes.
        year = str(date_to.year)
        for tableau_id in _ANNEX_TOTALS:
            for code, value, _numero in self._dbc_edi_annex_totals(
                tableau_id, company, year, date_from, date_to,
            ):
                values.setdefault(code, value)
        return values, per_tableau

    def _coh_declared_codes(self):
        """Every codeEdi the 25 liasse reports declare, rendered or not.

        A code in this set but absent from the computed values is a cell the
        report shows BLANK - an unfilled "Saisie". Accounting-wise that is a
        zero, and that is how the reference workbook reads it, so the check
        takes it as 0 rather than giving up.

        A code that is NOT in this set is a mapping mistake in _CHECKS (a
        typo, or a code that moved), and must not be silently read as 0: the
        check reports itself as untested instead.
        """
        codes = {code for totals in _ANNEX_TOTALS.values() for code, _field in totals}
        for xmlid in _REPORT_TABLEAU:
            target = self.env.ref(f'dbc_liasse_fiscale.{xmlid}', raise_if_not_found=False)
            if not target:
                continue
            expressions = target.line_ids.expression_ids
            xmlids = expressions.get_external_id()
            for expression in expressions:
                code = self._dbc_edi_code_of(expression, xmlids, set())
                if code:
                    codes.add(code)
        return frozenset(codes)

    def _coh_term(self, term, values, company, date_from, date_to, declares=frozenset()):
        """The value of one side of a check, or None when it cannot be read.

        None propagates: a check with an unreadable side is reported as "non
        testé" rather than as a difference against an assumed zero.
        """
        if term is None:
            return None
        kind = term[0]
        if kind == 'codes':
            total = 0.0
            for sign, code in term[1]:
                value = values.get(code)
                if value is None:
                    if code not in declares:
                        return None
                    value = 0.0            # cellule vide = zéro
                if isinstance(value, str):
                    return None
                total += sign * float(value)
            return total
        # ('annex', tableau_id, field, scope)
        _kind, tableau_id, field, scope = term
        model_name = _ANNEX_TABLES[tableau_id][0]
        year = str(date_to.year)
        domain = self._dbc_edi_annex_domain(tableau_id, company, year, date_from, date_to)
        if scope == 'exercice':
            date_field = self._ANNEX_DATE_FIELD.get(tableau_id, (None, None))[0]
            if date_field:
                domain = domain + [(date_field, '>=', date_from)]
        return sum(self.env[model_name].search(domain).mapped(field))

    @staticmethod
    def _coh_statut(ecart):
        """'incoherence' / 'mineur' / 'ok' / None, on the workbook's scale."""
        if ecart is None:
            return None
        ecart = abs(round(ecart, 2))
        if ecart > _SEUIL_INCOHERENCE:
            return 'incoherence'
        if ecart >= _SEUIL_MINEUR:
            return 'mineur'
        return 'ok'

    def _coh_resultats(self, values, company, date_from, date_to, declares=None):
        """One entry per check, both exercices evaluated.

        The single place where _CHECKS is turned into figures: the standard
        report, the BILAN2-styled page and the export's refusal message all
        read this, so the three can never tell a different story.

        `applicable_prev` is False when the check has no previous-exercice
        equivalent at all - the annex tables (T4, T8, T10, T13, T16, T19) only
        ever carry the exercice being filed. That is not a failure to test,
        it is nothing to test, and it is counted as neither.
        """
        if declares is None:
            declares = self._coh_declared_codes()
        resultats = []
        for label_a, a, a_prev, label_b, b, b_prev in _CHECKS:
            valeur_a = self._coh_term(a, values, company, date_from, date_to, declares)
            valeur_b = self._coh_term(b, values, company, date_from, date_to, declares)
            valeur_a_prev = self._coh_term(a_prev, values, company, date_from, date_to, declares)
            valeur_b_prev = self._coh_term(b_prev, values, company, date_from, date_to, declares)
            ecart = None if valeur_a is None or valeur_b is None else valeur_a - valeur_b
            ecart_prev = (None if valeur_a_prev is None or valeur_b_prev is None
                          else valeur_a_prev - valeur_b_prev)
            resultats.append({
                'libelle_a': label_a, 'libelle_b': label_b,
                'a': valeur_a, 'b': valeur_b,
                'ecart': ecart, 'statut': self._coh_statut(ecart),
                'a_prev': valeur_a_prev, 'b_prev': valeur_b_prev,
                'ecart_prev': ecart_prev, 'statut_prev': self._coh_statut(ecart_prev),
                'applicable_prev': a_prev is not None and b_prev is not None,
            })
        return resultats

    @staticmethod
    def _coh_compteurs(resultats):
        """{colonne: {incoherence, mineur, non_teste}} on the workbook's scale."""
        compte = {'montant': {'incoherence': 0, 'mineur': 0, 'non_teste': 0},
                  'montant_prev': {'incoherence': 0, 'mineur': 0, 'non_teste': 0}}
        for resultat in resultats:
            for colonne, statut, applicable in (
                ('montant', resultat['statut'], True),
                ('montant_prev', resultat['statut_prev'], resultat['applicable_prev']),
            ):
                if not applicable:
                    continue
                if statut is None:
                    compte[colonne]['non_teste'] += 1
                elif statut in compte[colonne]:
                    compte[colonne][statut] += 1
        return compte

    def _coh_ecarts(self, values, company, date_from, date_to):
        """[(libelle A, libelle B, valeur A, valeur B, ecart)] for the checks
        that come out non-zero on the exercice being filed.

        Used by the EDI export to name, in its refusal message, everything
        else that looks wrong - so that one trip to the screen is enough.
        """
        values = dict(values)
        year = str(date_to.year)
        for tableau_id in _ANNEX_TOTALS:
            for code, value, _numero in self._dbc_edi_annex_totals(
                tableau_id, company, year, date_from, date_to,
            ):
                values.setdefault(code, value)
        return [
            (r['libelle_a'], r['libelle_b'], r['a'], r['b'], r['ecart'])
            for r in self._coh_resultats(values, company, date_from, date_to)
            if r['statut'] in ('incoherence', 'mineur')
        ]

    # ------------------------------------------------------------- lignes

    _CLASSE = {'incoherence': 'text-danger', 'mineur': 'text-warning'}

    def _coh_line(self, report, options, markup, name, cells, level=1, klass=None):
        columns = []
        for col_options in options['columns']:
            value, statut = cells.get(col_options['expression_label'], (None, None))
            col_dict = report._build_column_dict(value, col_options, options=options)
            css = self._CLASSE.get(statut)
            if css:
                col_dict['class'] = css
            columns.append(col_dict)
        line = {
            'id': report._get_generic_line_id('', None, markup=markup),
            'name': name,
            'level': level,
            'columns': columns,
            'unfoldable': False,
            'unfold': False,
        }
        if klass:
            line['class'] = klass
        return line

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        companies = self.env['res.company'].browse(report.get_report_company_ids(options))
        if len(companies) > 1:
            # One liasse covers one taxpayer; comparing cells merged across
            # several companies would compare nothing meaningful.
            return [(0, self._coh_line(
                report, options, 'coh_multi',
                _("Contrôle indisponible : %(count)s sociétés sélectionnées (%(names)s). "
                  "Choisissez une seule société.",
                  count=len(companies), names=', '.join(companies.mapped('display_name'))),
                {}, level=0, klass='o_bold_tr',
            ))]
        company = companies or self.env.company

        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        values, per_tableau = self._coh_values(
            company, date_from, date_to, bool(options.get('all_entries')))
        declares = self._coh_declared_codes()

        resultats = self._coh_resultats(values, company, date_from, date_to, declares)
        compte = self._coh_compteurs(resultats)

        lines = []
        corps = []
        for index, resultat in enumerate(resultats):
            ecart, ecart_prev = resultat['ecart'], resultat['ecart_prev']
            corps.append((0, self._coh_line(
                report, options, f'coh_{index}_a', resultat['libelle_a'],
                {'montant': (resultat['a'], None), 'montant_prev': (resultat['a_prev'], None)},
            )))
            corps.append((0, self._coh_line(
                report, options, f'coh_{index}_b', resultat['libelle_b'],
                {
                    'montant': (resultat['b'], None),
                    'ecart': (None if ecart is None else abs(round(ecart, 2)),
                              resultat['statut']),
                    'montant_prev': (resultat['b_prev'], None),
                    'ecart_prev': (None if ecart_prev is None else abs(round(ecart_prev, 2)),
                                   resultat['statut_prev']),
                },
            )))

        for colonne, titre in (('montant', _("Exercice")),
                               ('montant_prev', _("Exercice précédent"))):
            c = compte[colonne]
            lines.append((0, self._coh_line(
                report, options, f'coh_compteur_{colonne}',
                _("%(titre)s : %(incoherences)s incohérence(s) (écart > 0,10) · "
                  "%(mineurs)s écart(s) mineur(s) (0,01 à 0,10) · "
                  "%(non_testes)s contrôle(s) non testé(s)",
                  titre=titre, incoherences=c['incoherence'],
                  mineurs=c['mineur'], non_testes=c['non_teste']),
                {}, level=0,
                klass='o_bold_tr' + (' text-danger' if c['incoherence'] else ''),
            )))

        lines += corps
        lines += self._coh_couverture(report, options, per_tableau, company, date_from, date_to)
        return lines

    # -------------------------------------------------------- couverture

    def _coh_couverture_data(self, per_tableau, company, date_from, date_to):
        """How many cells each tableau would contribute to the EDI file,
        counted exactly as the file counts them (the fixed cells, plus the
        annex totals and rows, minus the blank ones). A tableau at zero is one
        the file would leave out entirely - a forgotten annex, or one with
        nothing but zeros in it.

        Returns ([{tableau, nom, nombre}], total cells, number of empty ones).
        """
        year = str(date_to.year)
        noms = {}
        for xmlid, tableau_id in _REPORT_TABLEAU.items():
            target = self.env.ref(f'dbc_liasse_fiscale.{xmlid}', raise_if_not_found=False)
            # `name`, not `display_name`: the latter appends the country
            # code, so every row would read "... (MA)".
            noms[tableau_id] = target.name if target else xmlid

        lignes, total_cellules, vides = [], 0, 0
        for tableau_id in sorted(per_tableau):
            cells = [(code, value, None) for code, value in per_tableau[tableau_id].items()]
            if tableau_id in _ANNEX_TABLES:
                cells += self._dbc_edi_annex_totals(tableau_id, company, year, date_from, date_to)
                cells += self._dbc_edi_annex_rows(tableau_id, company, year, date_from, date_to)
            nombre = len([c for c in cells if not _is_blank_cell(c)])
            total_cellules += nombre
            if not nombre:
                vides += 1
            lignes.append({'tableau': tableau_id,
                           'nom': noms.get(tableau_id, ''),
                           'nombre': nombre})
        return lignes, total_cellules, vides

    def _coh_couverture(self, report, options, per_tableau, company, date_from, date_to):
        """The coverage inventory as report lines (see _coh_couverture_data)."""
        lignes, total_cellules, vides = self._coh_couverture_data(
            per_tableau, company, date_from, date_to)
        out = [(0, self._coh_line(
            report, options, 'coh_cov_header',
            _("Couverture EDI : %(cellules)s cellule(s) sur %(tableaux)s tableau(x)"
              "%(vides)s",
              cellules=total_cellules, tableaux=len(per_tableau),
              vides=_(", %(n)s sans aucune cellule", n=vides) if vides else ''),
            {}, level=0, klass='o_bold_tr' + (' text-danger' if vides else ''),
        ))]
        for ligne in lignes:
            out.append((0, self._coh_line(
                report, options, 'coh_cov_%s' % ligne['tableau'],
                _("Tableau %(id)s · %(nom)s — %(nombre)s cellule(s)%(vide)s",
                  id=ligne['tableau'], nom=ligne['nom'], nombre=ligne['nombre'],
                  vide=_("  ⚠ ABSENT DU FICHIER") if not ligne['nombre'] else ''),
                {}, klass='text-danger' if not ligne['nombre'] else None,
            )))
        return out
