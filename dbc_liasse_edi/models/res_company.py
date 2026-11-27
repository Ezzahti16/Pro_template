# -*- coding: utf-8 -*-
"""L'identité fiscale du déclarant, lue là où elle est saisie.

Aucun champ n'est déclaré ici. Les quatre valeurs qu'une déclaration doit
porter - raison sociale, Identifiant Fiscal, ICE, taxe professionnelle -
vivent toutes dans l'onglet « Identifiants Fiscaux » de la fiche société,
qu'apporte identifiants_fiscaux_maroc. En redéclarer une reviendrait à avoir
deux champs pour un même renseignement, et deux champs finissent toujours par
porter deux valeurs : sur SCAC, l'ancien champ propre à la liasse portait
4567890 quand l'onglet portait 40290455, pour un seul et même contribuable.

Ce module ne fait donc que nommer, une fois, lequel des champs de l'onglet
répond à quoi. Un seul trait à savoir : l'ICE n'est pas le champ `ice`, qui
n'est rempli qu'une fois à l'installation et reste vide ensuite, mais
`company_registry`, que le formulaire de l'onglet affiche sous l'étiquette
ICE - c'est là que le comptable le saisit.
"""
from odoo import models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ------------------------------------------------- identité du déclarant

    def _dbc_liasse_raison_sociale(self):
        """La dénomination imprimée sur la liasse et l'état des ventes.

        Le nom enregistré s'il est saisi, le nom de la société sinon : Odoo
        tourne souvent sous un nom commercial ou abrégé, et une déclaration
        fiscale doit porter le nom déposé.
        """
        self.ensure_one()
        return (self.raison_sociale or '').strip() or self.display_name

    def _dbc_liasse_identifiant_fiscal(self):
        """L'Identifiant Fiscal écrit dans <identifiantFiscal>.

        Simpl-IS rejette un fichier dont l'IF ne correspond pas au déposant,
        donc une seule source peut faire foi, et c'est le champ IF de l'onglet
        « Identifiants Fiscaux ».
        """
        self.ensure_one()
        return (self.iff or '').strip()

    def _dbc_liasse_ice(self):
        """L'ICE de la société déclarante.

        `company_registry` sur une société est un champ relié à sa fiche
        partenaire ; c'est bien là que l'ICE est saisi, l'onglet
        « Identifiants Fiscaux » l'affiche sous ce nom.
        """
        self.ensure_one()
        return (self.company_registry or '').strip()

    def _dbc_liasse_taxe_professionnelle(self):
        """Le numéro de taxe professionnelle imprimé sur la page de garde."""
        self.ensure_one()
        return (self.tp or '').strip()
