# -*- coding: utf-8 -*-
{
    'name': 'DBC - Liasse Fiscale EDI (Maroc)',
    'version': '19.0.5.1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Generate the DGI Simpl-IS "EDI Liasse" XML file from the Moroccan liasse reports',
    'description': """
Liasse fiscale - EDI export (Simpl-IS)
======================================
Adds an "EDI XML" export button to the 25 Moroccan liasse reports provided by
dbc_liasse_fiscale, producing the XML file expected by the DGI's Simpl-IS
teleservice ("Envoi des fichiers EDI"), packaged in a ZIP.

The file follows the "Cahier des charges du mode EDI du teleservice Simpl-IS"
v1.0, for the Modele Comptable Normal (modele id 1):

* every cell is a <ValeurCellule> pairing a <codeEdi> with its <valeur>,
  grouped per <tableau>; the cell order is irrelevant, matching is done on
  the codeEdi alone;
* the values themselves are computed by dbc_liasse_fiscale's own formula
  engine, so the file always agrees with what the reports display on screen;
* the annex tables whose row count is not known in advance ("sections
  illimitees": capital social, emprunts, credit-bail, locations, dotations
  aux amortissements, titres de participation) additionally carry a
  <numeroLigne>, and are filled from the editable rows captured in Odoo.
""",
    'author': 'DBC',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        'dbc_liasse_fiscale',
        # L'identite fiscale du declarant - IF, ICE, taxe professionnelle -
        # est portee par ce module, sur la societe comme sur le client. La
        # liasse la lit, elle ne la redeclare pas : deux champs IF cote a cote
        # finissent par porter deux nombres differents, et Simpl-IS rejette
        # un fichier dont l'identifiantFiscal n'est pas celui du deposant.
        'identifiants_fiscaux_maroc',
    ],
    'data': [
        'data/incoherences_report.xml',
        # Pas de vue société : les quatre renseignements que la liasse lit sur
        # elle sont déjà affichés par identifiants_fiscaux_maroc, dans son
        # onglet « Identifiants Fiscaux ». Rien à ajouter au formulaire.
        'views/res_partner_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dbc_liasse_edi/static/src/incoherences.scss',
            'dbc_liasse_edi/static/src/js/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
