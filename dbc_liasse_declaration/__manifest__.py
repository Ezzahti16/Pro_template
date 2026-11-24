# -*- coding: utf-8 -*-
{
    'name': 'DBC - Déclaration de la Liasse Fiscale (Maroc)',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': "Piloter le dépôt de la liasse fiscale : états, contrôles et génération des fichiers",
    'description': """
Déclaration de la liasse fiscale
================================
Les 25 états de la liasse, le fichier EDI, le PDF et le classeur Excel
existent déjà, chacun derrière son propre écran. Ce module ajoute ce qui
manquait entre eux : **un document de déclaration**, un par société et par
exercice, qui porte le cycle de dépôt du début à la fin.

Il tient quatre choses que les états, pris un par un, ne peuvent pas tenir :

* **Un état d'avancement.** Brouillon -> En contrôle -> Confirmé -> Déclaré.
  Chaque passage est tracé dans le fil de discussion, avec qui et quand.

* **Un suivi des tableaux.** Les 25 états de la liasse, numérotés comme la
  DGI les numérote, chacun avec sa case « Vérifié », qui l'a cochée et quand,
  et le nombre de cellules qu'il livrerait au fichier EDI - un tableau à zéro
  est une annexe oubliée. Un clic ouvre l'état sur l'exercice déclaré.

* **Les incohérences, figées.** Les 28 contrôles croisés sont relevés au
  moment du contrôle et conservés sur la déclaration. Une déclaration doit
  garder la trace de ce qui a été vérifié le jour du dépôt, pas seulement de
  ce que les comptes disent aujourd'hui.

* **Les trois fichiers.** PDF, Excel et XML EDI se génèrent depuis l'en-tête,
  sur la période de la déclaration, et restent attachés à elle.

Le module n'ajoute aucun calcul : les chiffres sont ceux des 25 états, lus
par le même moteur que l'export EDI, donc l'écran, le fichier et le papier
racontent toujours la même chose.
""",
    'author': 'DBC',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        # 'mail' pour le fil de discussion et les activités de la déclaration ;
        # il arrive de toute façon par 'account', mais on déclare ce qu'on emploie.
        'mail',
        # 'accountant' porte la racine du menu Comptabilité, sous laquelle se
        # range « Déclarations ». Elle arrive par account_reports, mais sans
        # la déclarer rien ne garantit qu'elle soit chargée avant nous.
        'accountant',
        'dbc_liasse_fiscale',
        'dbc_liasse_edi',
        'dbc_liasse_pdf',
        'dbc_liasse_xlsx',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/liasse_declaration_views.xml',
        'views/liasse_declaration_menus.xml',
    ],
    'installable': True,
    'application': False,
}
