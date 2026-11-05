{
    'name': "Database Guard Console",
    'summary': "Control the hidden database-manager URL of several Odoo instances "
               "from one place, over JSON-RPC",
    'description': """
Database Guard Console
======================

Central console for the ``dbc_db_access_guard`` agent. Register every managed
instance (URL, database, login, API key) and, from one screen:

* **Test connection** — reachability, Odoo version, agent version, and whether
  the agent is loaded server-wide on that instance;
* **Fetch configuration** — read what the instance *really* enforces, not what
  this console believes;
* **Apply & Protect** — push the secret database-manager path, the selector and
  RPC switches and the optional IP allow-list, then **verify over plain HTTP**
  that the new URL answers 200 while ``/web/database/manager`` answers 404,
  **rolling back automatically** when it does not;
* **Deactivate** — give the stock URLs back on that instance.

Transport: ``POST /json/2/<model>/<method>`` with a global-scope API key
(Odoo 19), falling back to ``/jsonrpc`` for older instances.

The agent module is never modified: everything goes through the standard
``ir.module.module`` and ``res.config.settings`` models.
""",
    'version': '19.0.1.0.0',
    'author': "DBC",
    'website': "https://www.dbc.ma",
    'category': 'Technical Settings',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [
        'security/guard_console_groups.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/dbc_guard_instance_views.xml',
        'views/dbc_guard_instance_log_views.xml',
        'views/menus.xml',
    ],
    'external_dependencies': {'python': ['requests']},
    'installable': True,
    'application': True,
    'auto_install': False,
}
