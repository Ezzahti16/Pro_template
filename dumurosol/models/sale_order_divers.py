# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _check_no_divers_product_line(self):
        """Un devis contenant une ligne dont le produit est le produit
        générique « Divers » configuré dans les paramètres (Ventes >
        Configuration > Paramètres) ne peut pas être confirmé : ce produit
        sert uniquement de placeholder temporaire pendant la rédaction du
        devis. Il faut créer le produit réel approprié au catalogue, puis
        créer une nouvelle version du devis (module Sale Quotation
        Versioning) qui l'utilise, avant de pouvoir confirmer.

        Si aucun produit « Divers » n'est configuré, ce contrôle est
        désactivé.
        """
        for order in self:
            divers_product = order.company_id.dumurosol_divers_product_id
            if not divers_product:
                continue
            divers_lines = order.order_line.filtered(
                lambda l: l.product_id == divers_product)
            if divers_lines:
                raise UserError(_(
                    "Impossible de confirmer le devis %(name)s : la/les ligne(s) "
                    "suivante(s) utilisent encore le produit générique "
                    "« %(product)s » :\n%(lines)s\n\n"
                    "Vous devez créer le produit approprié et créer une "
                    "autre version de devis avant de confirmer."
                ) % {
                    'name': order.name,
                    'product': divers_product.name,
                    'lines': "\n".join(
                        "- %s" % (line.name or divers_product.name)
                        for line in divers_lines
                    ),
                })


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _get_report_description(self):
        """Texte à afficher dans la colonne Description du rapport PDF.

        Pour une ligne dont le produit est le produit générique « Divers »
        configuré dans les paramètres, la description saisie par le vendeur
        démarre par défaut par le nom du produit lui-même (comportement
        standard d'Odoo lors de la sélection du produit). On retire cette
        première ligne pour n'afficher que le détail réellement saisi par
        le vendeur (le nom générique seul n'étant pas informatif pour le
        client). S'il n'y a pas de détail supplémentaire, ou si aucun
        produit « Divers » n'est configuré, on retombe sur le texte
        complet."""
        self.ensure_one()
        divers_product = self.company_id.dumurosol_divers_product_id
        if divers_product and self.product_id == divers_product and self.name:
            lines = self.name.split('\n')
            if lines and lines[0].strip() == divers_product.name:
                rest = '\n'.join(lines[1:]).strip()
                if rest:
                    return rest
        return self.name
