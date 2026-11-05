{
    'name': "Database Access Guard",
    'summary': "Hide the database manager and selector behind a secret, "
               "configurable URL and answer 404 on /web/database/*",
    'description': """
Database Access Guard
=====================

Protects the database management endpoints of an Odoo instance:

* ``/web/database/manager``, ``/web/database/selector``, ``/web/database/create``,
  ``/web/database/drop``, ``/web/database/duplicate``, ``/web/database/backup``,
  ``/web/database/restore``, ``/web/database/change_password`` and
  ``/web/database/list`` answer a plain **404 Not Found**;
* the whole database manager stays available to the technical team on a secret
  base path, ``/database/technique-acces`` by default, changeable from
  **Settings > General Settings > Database Manager Protection**;
* the ``db`` RPC service (database listing, creation, drop, dump, restore over
  ``/jsonrpc`` and ``/xmlrpc``) is refused as well, optionally except from
  allow-listed IP addresses.

For the guard to cover *every* request, including those served before any
database registry is loaded, add the module to ``server_wide_modules``::

    server_wide_modules = base,rpc,web,dbc_db_access_guard
""",
    'version': '19.0.1.0.0',
    'author': "DBC",
    'website': "https://www.dbc.ma",
    'category': 'Technical Settings',
    'license': 'LGPL-3',
    'depends': ['base_setup'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
