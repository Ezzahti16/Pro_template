# -*- coding: utf-8 -*-
"""Le suivi des tableaux : une ligne par état de la liasse, à cocher.

Les 25 états sont justes ou faux chacun pour leur compte, et personne ne peut
le dire à leur place : il faut les ouvrir et les lire. Cette grille garde la
trace de ce travail - qui a relu quel tableau, et quand - et montre à côté de
chaque ligne le nombre de cellules qu'il livrerait au fichier EDI, parce
qu'un tableau à zéro est une annexe oubliée que nul contrôle croisé ne
détecte.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MaLiasseDeclarationTableau(models.Model):
    _name = 'ma.liasse.declaration.tableau'
    _description = "Suivi d'un tableau de la liasse"
    _order = 'sequence, id'

    declaration_id = fields.Many2one(
        'ma.liasse.declaration', string="Déclaration",
        required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string="Séquence", default=10)

    numero = fields.Integer(
        string="N°", readonly=True,
        help="Numéro de l'imprimé de la DGI, de 1 à 25.")
    variante = fields.Char(
        string="Partie", readonly=True,
        help="Le Bilan est un seul imprimé rendu par deux états : Actif et "
             "Passif.")
    name = fields.Char(string="Tableau", readonly=True, required=True)
    report_xmlid = fields.Char(string="Identifiant de l'état", readonly=True)
    tableau_edi = fields.Integer(
        string="Tableau EDI", readonly=True,
        help="Identifiant du tableau dans le cahier des charges EDI. Il n'a "
             "rien à voir avec le numéro de l'imprimé.")

    cellules = fields.Integer(
        string="Cellules EDI", readonly=True,
        help="Nombre de cellules que ce tableau livrerait au fichier EDI, "
             "relevé au dernier contrôle. Zéro signifie que le fichier le "
             "laisserait entièrement de côté.")
    verifie = fields.Boolean(string="Vérifié", copy=False)
    verifie_par = fields.Many2one(
        'res.users', string="Vérifié par", readonly=True, copy=False)
    verifie_le = fields.Datetime(
        string="Vérifié le", readonly=True, copy=False)

    company_id = fields.Many2one(
        related='declaration_id.company_id', store=True, index=True)

    @api.depends('name', 'numero', 'variante')
    def _compute_display_name(self):
        for tableau in self:
            numero = _("Tableau %s", tableau.numero) if tableau.numero else ''
            partie = ' (%s)' % tableau.variante if tableau.variante else ''
            tableau.display_name = '%s%s — %s' % (numero, partie, tableau.name or '')

    def write(self, vals):
        """Cocher « Vérifié » signe la relecture ; décocher l'efface.

        Une case cochée sans savoir par qui ni quand ne vaut rien le jour où
        il faut rendre compte du dépôt.
        """
        if 'verifie' in vals and 'verifie_par' not in vals:
            if vals['verifie']:
                vals = dict(vals,
                            verifie_par=self.env.user.id,
                            verifie_le=fields.Datetime.now())
            else:
                vals = dict(vals, verifie_par=False, verifie_le=False)
        for tableau in self:
            if tableau.declaration_id.state == 'declare':
                raise UserError(_(
                    "La déclaration %s est déposée : son suivi ne se modifie "
                    "plus.", tableau.declaration_id.display_name))
        return super().write(vals)

    def action_ouvrir(self):
        """Ouvrir l'état, sur l'exercice de la déclaration.

        Atterrir sur les dates du jour reviendrait à relire d'autres chiffres
        que ceux qu'on est en train de vérifier.
        """
        self.ensure_one()
        report = self.env.ref(f'dbc_liasse_fiscale.{self.report_xmlid}',
                              raise_if_not_found=False)
        if not report:
            raise UserError(_(
                "L'état « %s » est introuvable.", self.report_xmlid))
        return self.declaration_id._action_rapport(report)
