# -*- coding: utf-8 -*-
"""La déclaration de la liasse fiscale : un document par société et exercice.

Les 25 états, le fichier EDI, le PDF et le classeur existaient déjà, chacun
derrière son écran. Ce qui manquait, c'est le document qui les tient ensemble
et qui garde la trace du dépôt : où en est-on, quels tableaux ont été relus,
ce que les contrôles disaient le jour où on a confirmé, et quels fichiers ont
été produits.

Rien n'est recalculé ici. Les chiffres viennent du moteur de dbc_liasse_edi
(`account.report.handler.ma_coherence`), celui-là même qui alimente l'export
EDI et la page Incohérences, de sorte que la déclaration ne puisse jamais
raconter autre chose que les états.
"""
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.dbc_liasse_edi.models.account_report import _REPORT_TABLEAU
from odoo.addons.dbc_liasse_pdf.models.account_report import _FORM_NUMBER

_logger = logging.getLogger(__name__)

# L'état qui sert de porteur quand il faut construire des `options` : les trois
# générateurs redéduisent tout de la société et des dates, donc n'importe
# lequel des 25 ferait l'affaire - autant en nommer un seul et s'y tenir.
_REPORT_PORTEUR = 'dbc_liasse_fiscale.bilan_actif_report'

# Les fichiers que la déclaration sait produire.
#   bouton -> (méthode du générateur, type MIME, nom lisible pour le fil)
#
# Les trois premiers sont la liasse elle-même. Les deux derniers sont l'état
# des ventes, qui est une déclaration séparée : elle se dépose à part, elle a
# son propre fichier EDI (<LesEtatDesVentes>) et son propre classeur, et elle
# ne porte aucun code de la liasse. Un seul bouton de plus ne suffisait pas :
# le classeur se produit toujours, le XML refuse tant qu'un client nommé n'a
# pas d'ICE - les deux ne servent donc pas au même moment.
_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
_GENERATEURS = {
    'pdf':  ('dbc_liasse_pdf_export',  'application/pdf', "PDF"),
    'xlsx': ('dbc_liasse_xlsx_export', _XLSX_MIME, "Excel"),
    'xml':  ('dbc_liasse_edi_export',  'application/zip', "XML EDI"),
    'ventes_xlsx': ('dbc_etat_ventes_xlsx_export', _XLSX_MIME,
                    "Excel de l'état des ventes"),
    'ventes_xml':  ('dbc_etat_ventes_xml_export', 'application/zip',
                    "XML EDI de l'état des ventes"),
}


class MaLiasseDeclaration(models.Model):
    _name = 'ma.liasse.declaration'
    _description = "Déclaration de la liasse fiscale"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_to desc, id desc'

    # ------------------------------------------------------------------ champs

    name = fields.Char(
        string="Référence", compute='_compute_name', store=True, readonly=True)

    company_id = fields.Many2one(
        'res.company', string="Société", required=True, index=True,
        default=lambda self: self.env.company, tracking=True)
    raison_sociale = fields.Char(
        string="Raison sociale", compute='_compute_identite',
        store=True, readonly=False, tracking=True,
        help="Dénomination telle qu'elle doit figurer sur la liasse. Reprise "
             "du nom de la société, et modifiable ici : c'est la déclaration "
             "qui fait foi, pas la fiche, qui peut changer après le dépôt.")
    identifiant_fiscal = fields.Char(
        string="Identifiant fiscal", compute='_compute_identite',
        store=True, readonly=False, tracking=True,
        help="Identifiant Fiscal (IF) du contribuable déclarant. Simpl-IS "
             "rejette un fichier dont l'<identifiantFiscal> ne correspond pas.")

    # La DGI déclare un exercice entier, et le moteur de la liasse recale de
    # toute façon la période sur l'exercice comptable complet
    # (_dbc_edi_exercice_bounds). Laisser saisir un début libre reviendrait
    # donc à afficher une période que les chiffres ne respectent pas : le
    # début est déduit de la clôture, et l'en-tête ne peut pas mentir.
    date_to = fields.Date(
        string="Fin de période", required=True, tracking=True,
        default=lambda self: self._defaut_date_to(),
        help="Date de clôture de l'exercice déclaré. Le début de période en "
             "est déduit.")
    date_from = fields.Date(
        string="Début de période", compute='_compute_date_from',
        store=True, readonly=True, tracking=True)
    exercice = fields.Char(
        string="Exercice", compute='_compute_name', store=True, readonly=True)

    type_declaration = fields.Selection(
        [('initiale', "Déclaration initiale"),
         ('rectificative', "Déclaration rectificative")],
        string="Type de déclaration", required=True, default='initiale',
        tracking=True)
    all_entries = fields.Boolean(
        string="Inclure les écritures brouillon", tracking=True,
        help="Reprend le filtre « Écritures non comptabilisées » des états. "
             "À laisser décoché pour un dépôt réel : la liasse déclare des "
             "écritures comptabilisées. À cocher sur une base dont tout est "
             "encore au brouillon, sinon la liasse sort vide.")

    state = fields.Selection(
        [('draft', "Brouillon"),
         ('controle', "En contrôle"),
         ('confirme', "Confirmé"),
         ('declare', "Déclaré")],
        string="État", default='draft', required=True, tracking=True,
        copy=False)
    date_depot = fields.Date(string="Date de dépôt", readonly=True, copy=False,
                             tracking=True)

    tableau_ids = fields.One2many(
        'ma.liasse.declaration.tableau', 'declaration_id',
        string="Suivi des tableaux", copy=False)
    incoherence_ids = fields.One2many(
        'ma.liasse.declaration.incoherence', 'declaration_id',
        string="Contrôles de cohérence", readonly=True, copy=False)

    # « Arrêtés », pas « lancés » : le moteur sert son cache tant que ni les
    # écritures ni les annexes n'ont bougé, donc relancer les contrôles à
    # 20:47 peut légitimement laisser 20:20. C'est la date des chiffres qui
    # compte, et c'est elle que porte le champ ; l'étiquette doit la nommer.
    controle_le = fields.Datetime(
        string="Chiffres arrêtés le", readonly=True, copy=False,
        help="Quand les contrôles de cohérence affichés ci-dessous ont été "
             "calculés. Ils sont figés : ils disent ce qui était vrai à ce "
             "moment, pas ce que les comptes disent maintenant. Relancer les "
             "contrôles ne change cette date que si quelque chose a bougé "
             "depuis.")

    nb_tableaux = fields.Integer(
        string="Tableaux", compute='_compute_verification', store=True)
    nb_tableaux_verifies = fields.Integer(
        string="Tableaux vérifiés", compute='_compute_verification', store=True)
    # `avg` et non la somme par défaut : additionner deux pourcentages donne
    # un nombre qui ne veut rien dire, et un groupe de 100 % + 0 % affichait
    # « 100,00 % », ce qui rassure à tort. Toutes les déclarations portent les
    # mêmes 25 tableaux, donc la moyenne est exacte.
    taux_verification = fields.Float(
        string="Vérification", compute='_compute_verification', store=True,
        aggregator='avg')
    nb_cellules_edi = fields.Integer(
        string="Cellules EDI", readonly=True, copy=False,
        help="Nombre de cellules que le fichier EDI contiendrait, relevé au "
             "dernier contrôle.")
    nb_tableaux_vides = fields.Integer(
        string="Tableaux sans aucune cellule", readonly=True, copy=False)

    nb_incoherences = fields.Integer(
        string="Incohérences", compute='_compute_compteurs', store=True)
    nb_mineures = fields.Integer(
        string="Écarts mineurs", compute='_compute_compteurs', store=True)
    nb_non_testes = fields.Integer(
        string="Non testés", compute='_compute_compteurs', store=True)

    # --------------------------------------------------------------- défauts

    @api.model
    def _defaut_date_to(self):
        company = self.env.company
        return company.compute_fiscalyear_dates(fields.Date.context_today(self))['date_to']

    # -------------------------------------------------------------- computes

    @api.depends('company_id')
    def _compute_identite(self):
        for declaration in self:
            company = declaration.company_id
            declaration.raison_sociale = company._dbc_liasse_raison_sociale() if company else False
            declaration.identifiant_fiscal = \
                company._dbc_liasse_identifiant_fiscal() if company else False

    @api.depends('company_id', 'date_to')
    def _compute_date_from(self):
        for declaration in self:
            if not (declaration.company_id and declaration.date_to):
                declaration.date_from = False
                continue
            dates = declaration.company_id.compute_fiscalyear_dates(declaration.date_to)
            declaration.date_from = dates['date_from']

    @api.depends('date_to', 'type_declaration', 'company_id')
    def _compute_name(self):
        for declaration in self:
            annee = declaration.date_to.year if declaration.date_to else ''
            declaration.exercice = str(annee)
            suffixe = (_(" (rectificative)")
                       if declaration.type_declaration == 'rectificative' else '')
            declaration.name = _("Liasse fiscale %(annee)s%(suffixe)s",
                                 annee=annee, suffixe=suffixe)

    @api.depends('tableau_ids.verifie')
    def _compute_verification(self):
        for declaration in self:
            tableaux = declaration.tableau_ids
            verifies = len(tableaux.filtered('verifie'))
            declaration.nb_tableaux = len(tableaux)
            declaration.nb_tableaux_verifies = verifies
            declaration.taux_verification = (
                100.0 * verifies / len(tableaux)) if tableaux else 0.0

    @api.depends('incoherence_ids.statut')
    def _compute_compteurs(self):
        for declaration in self:
            statuts = declaration.incoherence_ids.mapped('statut')
            declaration.nb_incoherences = statuts.count('incoherence')
            declaration.nb_mineures = statuts.count('mineur')
            declaration.nb_non_testes = statuts.count('non_teste')

    # ---------------------------------------------------------- contraintes

    @api.constrains('company_id', 'date_to', 'type_declaration')
    def _check_doublon(self):
        """Une seule déclaration initiale par société et par exercice.

        Les rectificatives ne sont pas limitées : il peut y en avoir
        plusieurs, c'est même leur raison d'être.
        """
        for declaration in self:
            if declaration.type_declaration != 'initiale':
                continue
            # sudo : une contrainte d'unicité qui ne voit pas les
            # enregistrements des autres ne contraint rien.
            double = self.sudo().search_count([
                ('id', '!=', declaration.id),
                ('company_id', '=', declaration.company_id.id),
                ('date_to', '=', declaration.date_to),
                ('type_declaration', '=', 'initiale'),
            ])
            if double:
                raise UserError(_(
                    "Une déclaration initiale existe déjà pour %(societe)s sur "
                    "l'exercice clos le %(date)s.\n\nPour déposer à nouveau, "
                    "créez une déclaration rectificative plutôt qu'une seconde "
                    "initiale.",
                    societe=declaration.company_id.display_name,
                    date=declaration.date_to))

    # ------------------------------------------------------------ création

    @api.model_create_multi
    def create(self, vals_list):
        declarations = super().create(vals_list)
        declarations._generer_tableaux()
        return declarations

    def _generer_tableaux(self):
        """La grille des 25 états, numérotée comme la DGI numérote son imprimé.

        `_FORM_NUMBER` donne le numéro de l'imprimé (1 à 25) et, pour le
        Bilan, de quelle moitié il s'agit : c'est un seul tableau que nous
        rendons par deux états, Actif et Passif.
        """
        Tableau = self.env['ma.liasse.declaration.tableau']
        for declaration in self:
            if declaration.tableau_ids:
                continue
            lignes = []
            for xmlid, tableau_edi in _REPORT_TABLEAU.items():
                numero, variante = _FORM_NUMBER.get(str(tableau_edi), (99, None))
                report = self.env.ref(f'dbc_liasse_fiscale.{xmlid}',
                                      raise_if_not_found=False)
                lignes.append({
                    'numero': numero,
                    'variante': variante or '',
                    'report_xmlid': xmlid,
                    # `name`, pas `display_name` : ce dernier ajoute le code
                    # pays et chaque ligne se lirait « ... (MA) ».
                    'name': report.name if report else xmlid,
                    'tableau_edi': tableau_edi,
                })
            lignes.sort(key=lambda l: (l['numero'], l['variante']))
            for sequence, ligne in enumerate(lignes, start=1):
                ligne['sequence'] = sequence * 10
                ligne['declaration_id'] = declaration.id
            Tableau.create(lignes)

    # ------------------------------------------------------- outils moteur

    def _handler(self):
        """Le moteur de la liasse, lié à la société de la déclaration.

        Sans `with_company` ni `allowed_company_ids`, les états se rendraient
        pour la société active de l'utilisateur, qui n'est pas forcément
        celle qu'on déclare.
        """
        self.ensure_one()
        return self.env['account.report.handler.ma_coherence'] \
            .with_company(self.company_id) \
            .with_context(allowed_company_ids=self.company_id.ids)

    def _report_porteur(self):
        self.ensure_one()
        report = self.env.ref(_REPORT_PORTEUR, raise_if_not_found=False)
        if not report:
            raise UserError(_(
                "L'état « %s » est introuvable : le module dbc_liasse_fiscale "
                "n'est pas installé correctement.", _REPORT_PORTEUR))
        return report.with_company(self.company_id) \
                     .with_context(allowed_company_ids=self.company_id.ids)

    def _options_periode(self):
        """Les `options` d'un état, calées sur l'exercice de la déclaration."""
        self.ensure_one()
        return self.env['account.report.handler.ma_coherence'] \
            ._dbc_edi_period_options(self.date_from, self.date_to, self.all_entries)

    # ------------------------------------------------------------ contrôles

    def action_controler(self):
        """Relever les contrôles de cohérence et les figer sur la déclaration.

        Le relevé passe par le cache du moteur : rendre les 25 états coûte une
        vingtaine de secondes à froid, et rien ne sert de recommencer tant que
        ni les écritures ni les annexes n'ont bougé.
        """
        for declaration in self:
            declaration._verifier_modifiable()
            handler = declaration._handler()
            company = declaration.company_id
            calcul = handler._coh_values_datees(
                company, declaration.date_from, declaration.date_to,
                declaration.all_entries)
            resultats = handler._coh_resultats(
                calcul['values'], company, declaration.date_from, declaration.date_to)
            couverture, cellules, vides = handler._coh_couverture_data(
                calcul['per_tableau'], company, declaration.date_from, declaration.date_to)

            declaration.incoherence_ids.unlink()
            self.env['ma.liasse.declaration.incoherence'].create([
                dict(resultat, declaration_id=declaration.id, sequence=(index + 1) * 10)
                for index, resultat in enumerate(
                    declaration._lignes_incoherences(resultats))
            ])

            # Le nombre de cellules par tableau : un tableau à zéro est une
            # annexe que le fichier EDI laisserait entièrement de côté.
            par_tableau = {ligne['tableau']: ligne['nombre'] for ligne in couverture}
            for tableau in declaration.tableau_ids:
                tableau.cellules = par_tableau.get(tableau.tableau_edi, 0)

            declaration.write({
                'controle_le': calcul['calcule_le'],
                'nb_cellules_edi': cellules,
                'nb_tableaux_vides': vides,
            })
            declaration.message_post(body=_(
                "Contrôles relancés : %(incoherences)s incohérence(s), "
                "%(mineures)s écart(s) mineur(s), %(cellules)s cellule(s) EDI "
                "sur %(tableaux)s tableau(x), dont %(vides)s vide(s).",
                incoherences=declaration.nb_incoherences,
                mineures=declaration.nb_mineures,
                cellules=cellules, tableaux=len(couverture), vides=vides))
        return True

    @staticmethod
    def _lignes_incoherences(resultats):
        """Les résultats du moteur, mis à la forme du modèle enfant."""
        lignes = []
        for resultat in resultats:
            lignes.append({
                'libelle_a': resultat['libelle_a'],
                'libelle_b': resultat['libelle_b'],
                'montant_a': resultat['a'],
                'montant_b': resultat['b'],
                # L'écart est affiché en valeur absolue, comme le classeur de
                # référence le fait : c'est son ampleur qui se lit, le signe
                # dépend de l'ordre dans lequel le contrôle est écrit.
                'ecart': None if resultat['ecart'] is None else abs(resultat['ecart']),
                'statut': resultat['statut'] or 'non_teste',
                'montant_a_prev': resultat['a_prev'],
                'montant_b_prev': resultat['b_prev'],
                'ecart_prev': (None if resultat['ecart_prev'] is None
                               else abs(resultat['ecart_prev'])),
                'statut_prev': (resultat['statut_prev'] or 'non_teste')
                               if resultat['applicable_prev'] else 'sans_objet',
                'cible_a': resultat['cible_a'] or '',
                'cible_b': resultat['cible_b'] or '',
            })
        return lignes

    def _verifier_modifiable(self):
        self.ensure_one()
        if self.state == 'declare':
            raise UserError(_(
                "La déclaration %s est déposée : elle ne se modifie plus.\n\n"
                "Pour corriger un dépôt, créez une déclaration rectificative.",
                self.display_name))

    # --------------------------------------------------------- transitions

    def action_passer_en_controle(self):
        """Brouillon -> En contrôle, en lançant les contrôles au passage.

        C'est l'étape où l'on vérifie : y arriver sur un tableau vide
        obligerait à cliquer une fois de plus pour voir quoi que ce soit.
        """
        for declaration in self:
            if declaration.state != 'draft':
                continue
            declaration.state = 'controle'
        self.action_controler()
        return True

    def action_confirmer(self):
        """En contrôle -> Confirmé, une fois les 25 tableaux relus.

        La case « Vérifié » est tout l'objet de l'étape de contrôle ; laisser
        confirmer sans l'avoir cochée la viderait de son sens. Les
        incohérences, elles, ne bloquent pas : aucune n'est une règle de la
        DGI, et un écart de 0,32 ne doit pas empêcher un dépôt.
        """
        for declaration in self:
            if declaration.state != 'controle':
                continue
            restants = declaration.tableau_ids.filtered(lambda t: not t.verifie)
            if restants:
                raise UserError(_(
                    "%(nombre)s tableau(x) n'ont pas encore été vérifiés :\n\n"
                    "%(liste)s\n\n"
                    "Ouvrez-les depuis l'onglet « Suivi des tableaux », puis "
                    "cochez « Vérifié ».",
                    nombre=len(restants),
                    liste='\n'.join('  • %s' % t.display_name for t in restants[:10])
                          + ('\n  …' if len(restants) > 10 else '')))
            declaration.state = 'confirme'
        return True

    def action_declarer(self):
        """Confirmé -> Déclaré. Le document se fige."""
        for declaration in self:
            if declaration.state != 'confirme':
                continue
            declaration.write({
                'state': 'declare',
                'date_depot': fields.Date.context_today(declaration),
            })
        return True

    def action_retour_brouillon(self):
        """Déclaré/Confirmé -> Brouillon.

        La date de dépôt repart avec l'état. Un brouillon qui garde sa date de
        dépôt est simplement faux : le champ est masqué sur l'écran, mais une
        recherche, un filtre ou un export le verraient toujours et diraient
        déposé quelque chose qui ne l'a jamais été.
        """
        for declaration in self:
            declaration.write({'state': 'draft', 'date_depot': False})
        return True

    # ---------------------------------------------------------- ouvertures

    def action_ouvrir_incoherences(self):
        """La page Incohérences complète, sur l'exercice déclaré."""
        self.ensure_one()
        report = self.env.ref('dbc_liasse_edi.incoherences_report',
                              raise_if_not_found=False)
        if not report:
            raise UserError(_("L'état « Incohérences » est introuvable."))
        return self._action_rapport(report)

    def _action_rapport(self, report):
        """Afficher un état sur la période de la déclaration.

        `params.options` + `ignore_session` est la façon dont account_reports
        ouvre lui-même un état depuis un autre : sans `ignore_session`, les
        filtres mémorisés du poste reprendraient la main et on atterrirait sur
        un autre exercice que celui qu'on déclare.
        """
        self.ensure_one()
        report = report.with_company(self.company_id) \
                       .with_context(allowed_company_ids=self.company_id.ids)
        return {
            'type': 'ir.actions.client',
            'tag': 'account_report',
            'name': report.name,
            'context': {'report_id': report.id},
            'params': {'options': report.get_options(self._options_periode()),
                       'ignore_session': True},
        }

    # ---------------------------------------------------------- génération

    def action_generer_pdf(self):
        return self._generer('pdf')

    def action_generer_xlsx(self):
        return self._generer('xlsx')

    def action_generer_xml(self):
        return self._generer('xml')

    def action_generer_ventes_xlsx(self):
        return self._generer('ventes_xlsx')

    def action_generer_ventes_xml(self):
        return self._generer('ventes_xml')

    def _generer(self, genre):
        """Produire un des fichiers, l'attacher et le télécharger.

        Les générateurs sont ceux des boutons des états : même code, mêmes
        chiffres. On les appelle directement plutôt que par
        `dispatch_report_action`, qui est réservé aux appels venant du client
        et renverrait une action de téléchargement que seul l'écran des états
        sait exécuter.

        Le fichier reste attaché à la déclaration : c'est ce qui a été déposé,
        et une déclaration qui ne garde pas ses pièces n'en est pas une.
        """
        self.ensure_one()
        methode, mimetype, libelle = _GENERATEURS[genre]
        report = self._report_porteur()
        handler = self.env[report._get_custom_handler_model()] \
            .with_company(self.company_id) \
            .with_context(allowed_company_ids=self.company_id.ids)
        options = report.get_options(self._options_periode())
        resultat = getattr(handler, methode)(options)

        attachment = self.env['ir.attachment'].create({
            'name': self._nom_date(resultat['file_name']),
            'datas': base64.b64encode(resultat['file_content']),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': mimetype,
        })
        self.message_post(
            body=_("Fichier %(libelle)s généré.", libelle=libelle),
            attachment_ids=attachment.ids)
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'self',
        }

    def _nom_date(self, nom):
        """Dater la pièce jointe produite.

        Le même bouton pressé deux fois laissait deux pièces de nom
        identique, octet pour octet indiscernables tant que rien n'avait
        changé — et impossibles à départager dès qu'un chiffre bougeait entre
        les deux. Or l'intérêt d'attacher le fichier est justement de savoir
        lequel a été déposé. L'heure est celle du poste qui génère, pas l'UTC,
        parce que c'est celle-là qu'on relit.
        """
        self.ensure_one()
        racine, point, extension = nom.rpartition('.')
        if not point:
            racine, extension = nom, ''
        horodatage = fields.Datetime.context_timestamp(
            self, fields.Datetime.now()).strftime('%Y%m%d-%H%M')
        return '%s_%s%s%s' % (racine, horodatage, point, extension)

    # ------------------------------------------------------------ fil de discussion

    def _creation_message(self):
        """Le premier message du fil.

        Odoo compose « <description du modèle> créé » : le masculin est
        inévitable pour lui, pas pour nous, et une déclaration est féminine.
        C'est la première ligne que l'utilisateur lit sur l'enregistrement.
        """
        self.ensure_one()
        return _("Déclaration de la liasse fiscale créée.")
