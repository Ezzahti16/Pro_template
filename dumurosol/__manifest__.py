{
    'name': "Dumurosol",
    'summary': "Personnalisations métier pour Dumurosol",
    'description': """
Module de personnalisations Dumurosol.

Fonctionnalités:
- Workflow d'annulation de devis avec motif obligatoire.
- Interdiction de modification des commandes confirmées (indépendant du paramètre natif "Verrouiller les commandes confirmées").
- Workflow de validation des devis (avant transformation en commande).
- Séquences séparées : devis (S00001, native Odoo) / commande confirmée (CMD00001).
- Interdiction de supprimer/annuler une facture confirmée (processus avoir obligatoire).
- Blocage de la confirmation d'un devis contenant une ligne utilisant le
  produit générique "Divers" (configurable dans les paramètres) ; sur le
  rapport imprimé, une telle ligne affiche le détail saisi plutôt que le nom du produit.
- Intégration avec Sale Quotation Versioning : bouton "Créer une nouvelle
  version" limité à l'état "Envoyé", annulation automatique du devis
  d'origine (motif configurable dans les paramètres), et blocage de
  modification d'un devis envoyé aligné sur le paramètre natif "Verrouiller
  les commandes confirmées".
- Précision obligatoire dans l'assistant d'annulation pour les motifs
  marqués comme tels (ex: "Autre").
- Les règles de validation des devis ne s'appliquent que si elles sont à
  l'état Actif (pas seulement non archivées).
""",
    'author': "Dumurosol",
    'category': 'Sales',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'account' , 'sale_quotation_versioning' , 'sale'],
    'data': [
        # Security
        'security/sale_validation_security.xml',
        'security/ir.model.access.csv',
        'security/sale_validation_record_rules.xml',

        # Data
        'data/cancel_reason_data.xml',
        'data/sale_order_sequence_data.xml',
        'data/sale_validation_cron_data.xml',

        # Wizards (chargés avant les vues qui référencent leurs actions)
        'wizard/sale_order_cancel_wizard_views.xml',
        'wizard/sale_order_approve_wizard_views.xml',
        'wizard/sale_order_reject_wizard_views.xml',
        'wizard/sale_validation_test_wizard_views.xml',

        # Views (modèles métier)
        'views/sale_order_cancel_reason_views.xml',
        'views/sale_validation_rule_views.xml',
        'views/sale_validation_level_views.xml',
        'views/sale_validation_log_views.xml',
        'views/sale_order_views.xml',
        'views/sale_order_validation_views.xml',
        'views/sale_order_report_templates.xml',
        'views/sale_order_versioning_views.xml',
        'views/res_config_settings_views.xml',

        # Report / Dashboard (doit précéder les menus)
        'report/sale_validation_dashboard_views.xml',

        # Menus (chargés en dernier)
        'views/sale_validation_menus.xml',
    ],
    'installable': True,
    'application': False,
    'post_init_hook': '_post_init_migrate_existing_orders',
}
