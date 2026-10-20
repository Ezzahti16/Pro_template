# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # ------------------------------------------------------------------
    # Champs
    # ------------------------------------------------------------------
    version_number = fields.Integer(
        string="Numéro de version",
        default=1,
        readonly=True,
        copy=False,
        help="Numéro de version dans la chaîne de révisions de ce devis.",
    )

    parent_quotation_id = fields.Many2one(
        comodel_name='sale.order',
        string="Version précédente",
        readonly=True,
        copy=False,
        index=True,
        help="Devis dont celui-ci est une nouvelle version.",
    )

    child_quotation_ids = fields.One2many(
        comodel_name='sale.order',
        inverse_name='parent_quotation_id',
        string="Versions suivantes",
    )

    root_quotation_id = fields.Many2one(
        comodel_name='sale.order',
        string="Devis d'origine",
        compute='_compute_root_quotation_id',
        store=True,
        recursive=True,
        help="Remonte à la première version (V1) de la chaîne.",
    )

    is_locked_version = fields.Boolean(
        string="Version figée",
        compute='_compute_is_locked_version',
        store=True,
        help="Vrai dès que le devis a été envoyé au client : il devient "
             "alors non modifiable et toute évolution passe par une "
             "nouvelle version.",
    )

    is_active_version = fields.Boolean(
        string="Version active",
        compute='_compute_is_active_version',
        store=True,
        help="Vrai si ce devis est la dernière version de sa chaîne "
             "(aucune version fille n'a été créée à partir de lui).",
    )

    version_diff_summary = fields.Html(
        string="Écarts avec la version précédente",
        compute='_compute_version_diff_summary',
        store=True,
    )

    version_count = fields.Integer(
        string="Nombre de versions",
        compute='_compute_version_count',
    )

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('parent_quotation_id', 'parent_quotation_id.root_quotation_id')
    def _compute_root_quotation_id(self):
        for order in self:
            if order.parent_quotation_id:
                order.root_quotation_id = (
                    order.parent_quotation_id.root_quotation_id
                    or order.parent_quotation_id
                )
            else:
                order.root_quotation_id = order.id if order.id else False

    @api.depends('state')
    def _compute_is_locked_version(self):
        for order in self:
            order.is_locked_version = order.state not in ('draft',)

    @api.depends('child_quotation_ids')
    def _compute_is_active_version(self):
        for order in self:
            order.is_active_version = not bool(order.child_quotation_ids)

    @api.depends('order_line', 'parent_quotation_id.order_line')
    def _compute_version_diff_summary(self):
        for order in self:
            if not order.parent_quotation_id:
                order.version_diff_summary = False
                continue
            order.version_diff_summary = order._build_version_diff_html(
                order.parent_quotation_id.order_line, order.order_line
            )

    @api.depends('root_quotation_id', 'root_quotation_id.child_quotation_ids')
    def _compute_version_count(self):
        for order in self:
            root = order.root_quotation_id or order
            # +1 pour compter la racine elle-même, puis tous les descendants
            order.version_count = 1 + len(root._get_all_descendant_versions())

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------
    def _get_all_descendant_versions(self):
        """Retourne l'ensemble des versions filles, petites-filles, etc."""
        self.ensure_one()
        descendants = self.env['sale.order']
        to_explore = self.child_quotation_ids
        while to_explore:
            descendants |= to_explore
            to_explore = to_explore.mapped('child_quotation_ids')
        return descendants

    def _build_version_diff_html(self, old_lines, new_lines):
        """Construit un résumé HTML des écarts entre deux jeux de lignes
        de commande, appariées par product_id.

        Catégories : ligne ajoutée, ligne supprimée, ligne modifiée
        (quantité, prix unitaire, remise).
        """
        old_by_product = {line.product_id.id: line for line in old_lines if line.product_id}
        new_by_product = {line.product_id.id: line for line in new_lines if line.product_id}

        added = []
        removed = []
        modified = []

        for product_id, new_line in new_by_product.items():
            old_line = old_by_product.get(product_id)
            if old_line is None:
                added.append(new_line)
                continue
            changes = []
            if old_line.product_uom_qty != new_line.product_uom_qty:
                changes.append(_(
                    "quantité : %(old)s → %(new)s",
                    old=old_line.product_uom_qty,
                    new=new_line.product_uom_qty,
                ))
            if old_line.price_unit != new_line.price_unit:
                changes.append(_(
                    "prix unitaire : %(old)s → %(new)s",
                    old=old_line.price_unit,
                    new=new_line.price_unit,
                ))
            if old_line.discount != new_line.discount:
                changes.append(_(
                    "remise : %(old)s%% → %(new)s%%",
                    old=old_line.discount,
                    new=new_line.discount,
                ))
            if changes:
                modified.append((new_line, changes))

        for product_id, old_line in old_by_product.items():
            if product_id not in new_by_product:
                removed.append(old_line)

        if not added and not removed and not modified:
            return "<p><em>%s</em></p>" % _("Aucun écart détecté avec la version précédente.")

        html_parts = ["<ul>"]
        for line in added:
            html_parts.append(
                "<li><strong>%s</strong> — %s</li>" % (_("Ajouté"), line.product_id.display_name)
            )
        for line in removed:
            html_parts.append(
                "<li><strong>%s</strong> — %s</li>" % (_("Supprimé"), line.product_id.display_name)
            )
        for line, changes in modified:
            html_parts.append(
                "<li><strong>%s</strong> — %s : %s</li>" % (
                    _("Modifié"), line.product_id.display_name, "; ".join(changes)
                )
            )
        html_parts.append("</ul>")
        return "".join(html_parts)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_create_new_version(self):
        """Crée une nouvelle version de ce devis, liée en tant que
        version fille. Le devis courant doit être figé et être la
        version active de sa chaîne.
        """
        self.ensure_one()

        if not self.is_locked_version:
            raise UserError(_(
                "Seul un devis figé (déjà envoyé) peut donner lieu à une "
                "nouvelle version. Ce devis est encore en brouillon : "
                "modifiez-le directement."
            ))
        if not self.is_active_version:
            raise UserError(_(
                "Ce devis a déjà été remplacé par une version plus "
                "récente. Ouvrez la dernière version pour la modifier."
            ))

        next_version_number = self.version_number + 1
        new_name = "%s-V%s" % (self._get_base_reference(), next_version_number)

        new_version = self.copy({
            'version_number': next_version_number,
            'parent_quotation_id': self.id,
            'state': 'draft',
            'name': new_name,
            'invoice_ids': [(5, 0, 0)],
        })

        new_version.message_post(
            body=_(
                "Version %(num)s créée à partir du devis %(parent)s.",
                num=next_version_number,
                parent=self.name,
            )
        )

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': new_version.id,
            'target': 'current',
        }

    def _get_base_reference(self):
        """Retourne la référence de base sans suffixe de version, pour
        construire proprement le nom de la version suivante.
        """
        self.ensure_one()
        name = self.name or ''
        if '-V' in name:
            base, _sep, suffix = name.rpartition('-V')
            if suffix.isdigit():
                return base
        return name

    def action_view_version_history(self):
        """Ouvre la vue liste de toutes les versions de la chaîne
        (racine incluse).
        """
        self.ensure_one()
        root = self.root_quotation_id or self
        all_versions = root | root._get_all_descendant_versions()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', all_versions.ids)],
            'name': _("Historique des versions — %s", self._get_base_reference()),
            'context': {'create': False},
        }

    def action_confirm(self):
        for order in self:
            if not order.is_active_version:
                raise UserError(_(
                    "Le devis %(name)s a été remplacé par une version "
                    "plus récente et ne peut plus être confirmé. "
                    "Confirmez la dernière version de la chaîne.",
                    name=order.name,
                ))
        return super().action_confirm()

    def unlink(self):
        for order in self:
            if order.child_quotation_ids:
                raise UserError(_(
                    "Le devis %(name)s possède des versions liées et ne "
                    "peut pas être supprimé, afin de préserver la "
                    "traçabilité de la chaîne de révisions.",
                    name=order.name,
                ))
        return super().unlink()

    def copy(self, default=None):
        """Empêche la copie manuelle (bouton Dupliquer standard) de
        créer une fausse chaîne de version : une duplication classique
        doit repartir à zéro (V1, sans parent), sauf lorsqu'elle est
        déclenchée par action_create_new_version qui fournit déjà ces
        valeurs explicitement dans `default`.
        """
        default = dict(default or {})
        if 'parent_quotation_id' not in default:
            default.setdefault('version_number', 1)
            default.setdefault('parent_quotation_id', False)
        return super().copy(default)

    # ------------------------------------------------------------------
    # Verrouillage en écriture
    # ------------------------------------------------------------------
    # Champs dont la modification est bloquée sur un devis figé. La liste
    # est volontairement limitée aux champs qui définissent le contenu
    # commercial du devis — les champs techniques/administratifs
    # (message_*, activity_*, etc.) restent modifiables.
    _VERSION_LOCKED_FIELDS = {
        'order_line', 'partner_id', 'pricelist_id', 'payment_term_id',
        'validity_date', 'fiscal_position_id', 'currency_id',
    }

    # Clé de contexte utilisée en interne pour autoriser une écriture
    # contrôlée sur un devis figé (ex: transitions d'état internes).
    _BYPASS_LOCK_CONTEXT_KEY = 'bypass_quotation_version_lock'

    def write(self, vals):
        if not self.env.context.get(self._BYPASS_LOCK_CONTEXT_KEY):
            locked_fields_in_vals = self._VERSION_LOCKED_FIELDS & set(vals.keys())
            if locked_fields_in_vals:
                locked_orders = self.filtered('is_locked_version')
                if locked_orders:
                    raise UserError(_(
                        "Les devis suivants sont figés et ne peuvent plus "
                        "être modifiés directement : %(names)s. "
                        "Créez une nouvelle version pour y apporter des "
                        "changements.",
                        names=", ".join(locked_orders.mapped('name')),
                    ))
        return super().write(vals)
