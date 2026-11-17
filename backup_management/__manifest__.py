# -*- coding: utf-8 -*-
{
    "name": "Database Backup Management",
    "version": "19.0.1.2.0",
    "summary": "Sauvegarde automatique de la base Odoo vers Google Drive "
               "(OAuth2 ou Service Account) ou SFTP securise",
    "category": "Tools",
    "author": "DBC",
    "license": "LGPL-3",
    "depends": ["base"],
    # Odoo 19 resout ces entrees via les metadonnees PyPI : on utilise donc
    # les noms pip. Installation dans le venv Odoo :
    #   pip install google-api-python-client google-auth google-auth-httplib2 paramiko
    "external_dependencies": {
        "python": ["google-api-python-client", "google-auth", "paramiko"],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/backup_config_views.xml",
        "views/backup_log_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": True,
}
