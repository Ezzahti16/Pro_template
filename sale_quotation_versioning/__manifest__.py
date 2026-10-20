# -*- coding: utf-8 -*-
{
    'name': "Sale Quotation Versioning",
    'summary': "Gestion du versionning des devis — traçabilité complète des révisions",
    'description': """
Module de versionning des devis
================================

Ce module ajoute une gestion de versions aux devis (sale.order) :

* Un devis envoyé au client est automatiquement figé (verrouillé en écriture).
* Toute modification ultérieure se fait via la création d'une nouvelle version,
  liée au devis d'origine (traçabilité parent-enfant).
* Le devis parent reste consultable et passe au statut "remplacé".
* Seule la version la plus récente d'une chaîne peut être confirmée en commande.
* Un différentiel des changements est calculé automatiquement entre deux
  versions consécutives (produit, quantité, prix, remise).

Basé sur l'étude fonctionnelle "Gestion du versionning des devis" v1.0.
""",
    'author': "DBC",
    'website': "",
    'category': 'Sales/Sales',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['sale', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/sale_quotation_versioning_security.xml',
        'views/sale_order_views.xml',
        'data/mail_template_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
