# -*- coding: utf-8 -*-
"""Les contrôles croisés, figés sur la déclaration.

Ce sont les mêmes 28 contrôles que la page Incohérences, relevés par le même
moteur (`_coh_resultats`). La différence est qu'ici ils sont **conservés** :
une déclaration doit garder la trace de ce qui a été vérifié le jour où on
l'a confirmée, pas seulement de ce que les comptes disent aujourd'hui. Les
lignes sont réécrites à chaque « Relancer les contrôles », et l'horodatage du
relevé est porté par la déclaration.

Aucun de ces contrôles n'est une règle de la DGI : ils informent, ils ne
bloquent pas le dépôt. Seul le bilan déséquilibré arrête l'export EDI, et
c'est dbc_liasse_edi qui s'en charge.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MaLiasseDeclarationIncoherence(models.Model):
    _name = 'ma.liasse.declaration.incoherence'
    _description = "Contrôle de cohérence d'une déclaration"
    _order = 'sequence, id'

    declaration_id = fields.Many2one(
        'ma.liasse.declaration', string="Déclaration",
        required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string="Séquence", default=10)

    libelle_a = fields.Char(string="Premier terme", readonly=True)
    libelle_b = fields.Char(string="Second terme", readonly=True)

    montant_a = fields.Float(string="Montant A", readonly=True, digits=(16, 2))
    montant_b = fields.Float(string="Montant B", readonly=True, digits=(16, 2))
    ecart = fields.Float(string="Écart", readonly=True, digits=(16, 2))
    statut = fields.Selection(
        [('ok', "Conforme"),
         ('mineur', "Écart mineur"),
         ('incoherence', "Incohérence"),
         ('non_teste', "Non testé"),
         ('sans_objet', "Sans objet")],
        string="Résultat", readonly=True, default='non_teste')

    montant_a_prev = fields.Float(string="Montant A (N-1)", readonly=True, digits=(16, 2))
    montant_b_prev = fields.Float(string="Montant B (N-1)", readonly=True, digits=(16, 2))
    ecart_prev = fields.Float(string="Écart (N-1)", readonly=True, digits=(16, 2))
    statut_prev = fields.Selection(
        [('ok', "Conforme"),
         ('mineur', "Écart mineur"),
         ('incoherence', "Incohérence"),
         ('non_teste', "Non testé"),
         ('sans_objet', "Sans objet")],
        string="Résultat (N-1)", readonly=True, default='sans_objet',
        help="« Sans objet » quand le contrôle n'a pas d'équivalent sur "
             "l'exercice précédent : les annexes ne portent que l'exercice "
             "déclaré. Ce n'est pas un contrôle manqué, il n'y a rien à "
             "comparer.")

    # Les états d'où viennent les deux termes, déduits des codes EDI par le
    # moteur : si un code déménage, le lien suit.
    cible_a = fields.Char(string="État du premier terme", readonly=True)
    cible_b = fields.Char(string="État du second terme", readonly=True)

    company_id = fields.Many2one(
        related='declaration_id.company_id', store=True, index=True)

    @api.depends('libelle_a', 'libelle_b')
    def _compute_display_name(self):
        for ligne in self:
            ligne.display_name = '%s / %s' % (ligne.libelle_a or '', ligne.libelle_b or '')

    def action_ouvrir_a(self):
        return self._ouvrir(self.cible_a)

    def action_ouvrir_b(self):
        return self._ouvrir(self.cible_b)

    def _ouvrir(self, xmlid):
        """Ouvrir l'état d'où vient le chiffre, sur l'exercice déclaré."""
        self.ensure_one()
        if not xmlid:
            raise UserError(_(
                "Ce terme ne vient pas d'un état : il est calculé sur les "
                "lignes d'annexe saisies dans Odoo."))
        report = self.env.ref(f'dbc_liasse_fiscale.{xmlid}', raise_if_not_found=False)
        if not report:
            raise UserError(_("L'état « %s » est introuvable.", xmlid))
        return self.declaration_id._action_rapport(report)
