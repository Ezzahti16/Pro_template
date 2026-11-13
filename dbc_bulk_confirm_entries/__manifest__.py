# -*- coding: utf-8 -*-
{
    'name': "Confirmation groupee des ecritures brouillon",
    'version': '19.0.1.1.0',
    'summary': """
        Action serveur permettant de confirmer (comptabiliser) plusieurs
        ecritures comptables en brouillon en une seule fois depuis la vue
        liste des ecritures comptables (Journal Items).
    """,
    'description': """
Confirmation groupee des ecritures comptables en brouillon
==========================================================

Ce module ajoute une action serveur "Confirmer les ecritures brouillon"
disponible dans le menu *Actions* de la vue liste des ecritures comptables
(account.move.line / account.view_move_line_tree), et non dans la vue des
pieces comptables.

Fonctionnement
--------------
* L'action remonte des lignes selectionnees vers leurs pieces comptables
  (``move_id``) : plusieurs lignes d'une meme piece ne la confirment qu'une
  seule fois, et le resume compte des pieces, pas des lignes.
* Seules les pieces a l'etat *Brouillon* sont traitees, les autres sont
  ignorees et comptabilisees dans le resume.
* Chaque piece est comptabilisee dans son propre point de sauvegarde
  (savepoint) : une piece en erreur (desequilibree, periode verrouillee,
  donnees manquantes, ...) n'annule pas la confirmation des autres.
* Le motif de l'echec est journalise dans le chatter de la piece concernee.
* Une notification resume le nombre de pieces confirmees, ignorees et en
  echec.

L'action n'est visible que pour les utilisateurs du groupe
*Comptabilite / Facturation*.
    """,
    'author': 'D-Business Consulting',
    'company': 'D-Business Consulting',
    'maintainer': 'D-Business Consulting',
    'website': 'https://d-business-consulting.ma',
    'category': 'Accounting/Accounting',
    'depends': ['account'],
    'data': [
        'data/ir_actions_server.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'auto_install': False,
    'application': False,
}
