# -*- coding: utf-8 -*-
"""The "Incohérences" page - the reference workbook's sheet, in Odoo.

Same figures as the plain Contrôle de cohérence report (both read
`_coh_resultats`), but laid out the way the purchased workbook lays them out:
the company and exercice on top, the two counter boxes with their 0,01 / 0,10
brackets, a "VS" marker between the two sides of each test, and one
Montant / Écart pair per exercice.

The layout is a full replacement of the account_reports screen template, the
same mechanism dbc_liasse_fiscale already uses for the Tableau de financement
(models/account_report_financement.py): `custom_display_config.templates`
swaps the AccountReport component's template, and the figures travel to it in
`custom_display_config.ma_incoherences`.

Computing them in _custom_options_initializer rather than in the lines
generator is deliberate: the whole liasse has to be rendered to produce them,
and doing it twice (once for the screen, once for the lines that feed the
PDF/XLSX export) would double a 20-second screen. The lines generator reads
what the initializer already computed.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_ESPACE_FINE = ' '     # narrow no-break space, for thousands


def _montant(valeur):
    """A number the way the liasse prints it: 1 234 567,89. '' when absent."""
    if valeur is None:
        return ''
    return '{:,.2f}'.format(valeur).replace(',', _ESPACE_FINE).replace('.', ',')


class AccountReportHandlerMaIncoherences(models.AbstractModel):
    """Custom handler of the "Incohérences" page."""
    _name = 'account.report.handler.ma_incoherences'
    _inherit = 'account.report.handler.ma_coherence'
    _description = "Incohérences (présentation du classeur) - Custom Handler"

    _CLASSE_CELLULE = {'incoherence': 'o_ma_incoh_ko', 'mineur': 'o_ma_incoh_warn'}

    # ------------------------------------------------------------- options

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options)
        config = options.setdefault('custom_display_config', {})
        config.setdefault('templates', {})['AccountReport'] = \
            'dbc_liasse_edi.AccountReportIncoherences'
        config['ma_incoherences'] = self._incoh_payload(report, options)
        options.setdefault('buttons', []).append({
            'name': _('Recalculer'),
            'sequence': 90,
            # Without always_show the button is filed away in the cog menu
            # (see AccountReportButtonsBar.barButtons in account_reports);
            # a control screen's refresh has to be in plain sight.
            'always_show': True,
            'action': 'action_ma_recalculer',
        })

    # -------------------------------------------------------------- actions

    def action_ma_recalculer(self, options):
        """Drop this exercice's cached figures and reopen the page.

        The fingerprint already catches anything saved in Odoo; this is for
        what it cannot see - a formula edited on the remote API, a changed
        fiscal year - and for simple peace of mind before filing.
        """
        company = self.env['res.company'].browse(
            self.env['account.report'].browse(options['report_id'])
                .get_report_company_ids(options)) or self.env.company
        date_from, date_to = self._dbc_edi_exercice_bounds(company[:1], options)
        self._coh_vider_cache(self._coh_cle_cache(
            self.env.cr.dbname, company[:1], date_from, date_to,
            bool(options.get('all_entries'))))
        return self._incoh_action_rapport(options['report_id'], options)

    def action_ma_ouvrir_etat(self, options, xmlid):
        """Open the liasse report a test's figure comes from, on the SAME
        exercice - landing on today's dates would mean reading other numbers
        than the ones just clicked."""
        cible = self.env.ref('dbc_liasse_fiscale.%s' % xmlid, raise_if_not_found=False)
        if not cible:
            raise UserError(_("L'état %s est introuvable.", xmlid))
        company = self.env['res.company'].browse(
            self.env['account.report'].browse(options['report_id'])
                .get_report_company_ids(options)) or self.env.company
        date_from, date_to = self._dbc_edi_exercice_bounds(company[:1], options)
        cible_options = cible.get_options(self._dbc_edi_period_options(
            date_from, date_to, bool(options.get('all_entries'))))
        return self._incoh_action_rapport(cible.id, cible_options, name=cible.name)

    @staticmethod
    def _incoh_action_rapport(report_id, options, name=None):
        """The client action that displays a report with given options.

        `params.options` + `ignore_session` is how account_reports itself
        opens one report from another (see caret_option_open_general_ledger in
        account_reports/models/account_report.py) - without ignore_session the
        viewer's own stored filters would win and the period would be lost.
        """
        action = {
            'type': 'ir.actions.client',
            'tag': 'account_report',
            'context': {'report_id': report_id},
            'params': {'options': options, 'ignore_session': True},
        }
        if name:
            action['name'] = name
        return action

    def _incoh_payload(self, report, options):
        """Everything the page prints, ready to display - no formatting left
        to the template, so the screen and a PDF of it read identically."""
        companies = self.env['res.company'].browse(report.get_report_company_ids(options))
        if len(companies) > 1:
            return {'erreur': _(
                "Une liasse couvre une seule société, or %(count)s sont sélectionnées "
                "(%(names)s).", count=len(companies),
                names=', '.join(companies.mapped('display_name')))}
        company = companies or self.env.company

        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        calcul = self._coh_values_datees(
            company, date_from, date_to, bool(options.get('all_entries')))
        values, per_tableau = calcul['values'], calcul['per_tableau']
        resultats = self._coh_resultats(values, company, date_from, date_to)
        compte = self._coh_compteurs(resultats)
        couverture, cellules, vides = self._coh_couverture_data(
            per_tableau, company, date_from, date_to)

        tests = []
        for resultat in resultats:
            # The raw figures travel next to the formatted ones so that the
            # lines generator - which feeds the PDF and XLSX exports - does
            # not have to render the whole liasse a second time.
            tests.append({
                'brut': {cle: resultat[cle] for cle in (
                    'libelle_a', 'libelle_b', 'a', 'b', 'ecart', 'statut',
                    'a_prev', 'b_prev', 'ecart_prev', 'statut_prev')},
                # Clicking a label opens the report that figure comes from,
                # on this same exercice (see action_ma_ouvrir_etat).
                'cible_a': resultat['cible_a'] or '',
                'cible_b': resultat['cible_b'] or '',
                'libelle_a': resultat['libelle_a'],
                'libelle_b': resultat['libelle_b'],
                'montant_a': _montant(resultat['a']),
                'montant_b': _montant(resultat['b']),
                'ecart': _montant(None if resultat['ecart'] is None
                                  else abs(round(resultat['ecart'], 2))),
                'classe': self._CLASSE_CELLULE.get(resultat['statut'], ''),
                'montant_a_prev': _montant(resultat['a_prev']),
                'montant_b_prev': _montant(resultat['b_prev']),
                'ecart_prev': _montant(None if resultat['ecart_prev'] is None
                                       else abs(round(resultat['ecart_prev'], 2))),
                'classe_prev': self._CLASSE_CELLULE.get(resultat['statut_prev'], ''),
                # An untested side prints a dash rather than a blank, so that
                # "nothing to compare" never looks like "compared, found zero".
                'non_teste': resultat['statut'] is None,
                'non_teste_prev': (resultat['applicable_prev']
                                   and resultat['statut_prev'] is None),
            })

        return {
            'societe': company.display_name,
            'exercice': _("Exercice du %(du)s au %(au)s",
                          du=date_from.strftime('%d/%m/%Y'),
                          au=date_to.strftime('%d/%m/%Y')),
            # Only the Modele Comptable Normal is produced by these reports -
            # see _MODELE_NORMAL_ID in account_report.py.
            'modele': _("Modèle Normal"),
            'titre': _("Tableau de vérification des incohérences"),
            # Reading the whole liasse takes ~25 s, so the result is cached;
            # saying when it was computed is what makes that honest.
            'calcule_le': _(
                "Calculé le %(quand)s%(cache)s",
                quand=fields.Datetime.context_timestamp(
                    self, calcul['calcule_le']).strftime('%d/%m/%Y à %H:%M'),
                cache=_(" (en cache)") if calcul['depuis_cache'] else ''),
            # The column headings travel with the data instead of being
            # written in the template: a bare word like "Test" sitting in a
            # QWeb template gets picked up by the web client's translation
            # catalogue and came out as "Tester" on a French session.
            'entetes': {
                'test': _("Test"),
                'exercice': _("Exercice"),
                'exercice_prec': _("Exercice précédent"),
                'montant': _("Montant"),
                'ecart': _("Écart"),
                'numero': _("N°"),
                'tableau': _("Tableau"),
                'cellules': _("Cellules"),
                'fichier': _("Dans le fichier EDI"),
                'present': _("oui"),
                'absent': _("ABSENT — aucune cellule"),
            },
            'compteurs': [
                {'titre': _("Nbre d'incohérences exercice"),
                 'mineur': compte['montant']['mineur'],
                 'incoherence': compte['montant']['incoherence'],
                 'non_teste': compte['montant']['non_teste']},
                {'titre': _("Nbre d'incohérences ex. précédent"),
                 'mineur': compte['montant_prev']['mineur'],
                 'incoherence': compte['montant_prev']['incoherence'],
                 'non_teste': compte['montant_prev']['non_teste']},
            ],
            'tests': tests,
            # The EDI coverage inventory - no spreadsheet can see a forgotten
            # annex, so it is kept next to the workbook's own checks.
            'couverture_titre': _(
                "Couverture EDI : %(cellules)s cellule(s) sur %(tableaux)s tableau(x)",
                cellules=cellules, tableaux=len(couverture)),
            'couverture_vides': vides,
            'couverture': couverture,
        }

    # -------------------------------------------------------------- lignes

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        """The same two-rows-per-test lines as the plain report.

        The screen never shows them - the custom template renders the payload
        instead - but the PDF and XLSX exports do, and they have to carry the
        figures rather than an empty table.
        """
        payload = (options.get('custom_display_config') or {}).get('ma_incoherences')
        if payload and payload.get('erreur'):
            return [(0, self._coh_line(
                report, options, 'incoh_erreur', payload['erreur'], {},
                level=0, klass='o_bold_tr'))]
        if not payload or not payload.get('tests'):
            # No payload (options built by hand, or a stale client): fall back
            # to the plain report's generator, which recomputes everything.
            return super()._dynamic_lines_generator(
                report, options, all_column_groups_expression_totals, warnings=warnings)

        lines = []
        for index, test in enumerate(payload['tests']):
            brut = test['brut']
            ecart, ecart_prev = brut['ecart'], brut['ecart_prev']
            lines.append((0, self._coh_line(
                report, options, f'incoh_{index}_a', brut['libelle_a'],
                {'montant': (brut['a'], None), 'montant_prev': (brut['a_prev'], None)},
            )))
            lines.append((0, self._coh_line(
                report, options, f'incoh_{index}_b', brut['libelle_b'],
                {
                    'montant': (brut['b'], None),
                    'ecart': (None if ecart is None else abs(round(ecart, 2)), brut['statut']),
                    'montant_prev': (brut['b_prev'], None),
                    'ecart_prev': (None if ecart_prev is None else abs(round(ecart_prev, 2)),
                                   brut['statut_prev']),
                },
            )))
        return lines
