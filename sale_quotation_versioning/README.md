# Sale Quotation Versioning

Module custom Odoo 19 — gestion du versionning des devis.

## Objectif

Odoo standard ne conserve aucun historique lorsqu'un devis envoyé est
modifié : les changements écrasent les données existantes. Ce module
introduit un mécanisme de versions liées, avec figeage automatique des
devis envoyés.

Basé sur l'étude fonctionnelle *« Gestion du versionning des devis »* v1.0
et sa spécification technique associée.

## Fonctionnement

- Un devis en brouillon reste librement modifiable.
- Dès qu'il est envoyé (état `sent`), il devient **figé** : ses lignes,
  partenaire, liste de prix, etc. ne sont plus modifiables directement.
- Le bouton **Créer une nouvelle version** duplique le devis figé en un
  nouveau devis brouillon, lié au parent (`parent_quotation_id`).
- Le devis parent passe automatiquement en version **non active** dès
  qu'une version fille existe.
- Seule la dernière version active d'une chaîne peut être confirmée en
  commande.
- Un différentiel des changements (produit ajouté/supprimé/modifié)
  est calculé automatiquement entre deux versions consécutives.
- La suppression d'un devis parent ayant des versions liées est bloquée.

## Installation

1. Copier le dossier `sale_quotation_versioning` dans le répertoire
   `addons` de l'instance Odoo 19 (ou tout chemin déclaré dans
   `addons_path`).
2. Redémarrer le serveur Odoo.
3. Activer le mode développeur, aller dans **Apps**, cliquer sur
   **Mettre à jour la liste des applications**.
4. Rechercher « Sale Quotation Versioning » et cliquer sur **Activer**.

## Dépendances

- `sale`
- `mail`

## Structure

```
sale_quotation_versioning/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   └── sale_order.py
├── security/
│   ├── ir.model.access.csv
│   └── sale_quotation_versioning_security.xml
├── views/
│   └── sale_order_views.xml
├── data/
│   └── mail_template_data.xml
├── tests/
│   ├── __init__.py
│   └── test_quotation_versioning.py
└── static/description/
    └── icon.png
```

## Tests

```bash
odoo-bin -d <votre_base> -u sale_quotation_versioning --test-enable \
  --test-tags sale_quotation_versioning --stop-after-init
```

## Points de vigilance connus

- Les rapports QWeb standard (devis PDF) n'affichent pas encore le
  numéro de version dans l'en-tête — prévoir une surcharge du template
  `sale.report_saleorder_document` si le besoin est confirmé.
- Les automatisations tierces déclenchées sur écriture de `sale.order`
  (webhooks, synchronisations externes) doivent filtrer sur
  `is_active_version` pour ignorer les versions remplacées.
- **À valider à l'installation** : les xpaths de `views/sale_order_views.xml`
  ciblent des structures standard (`//header`, `//field[@name='name']`,
  `//notebook`, `sale.sale_order_view_search_inherit_quotation`,
  `sale.sale_order_tree`) qui sont stables historiquement dans le module
  `sale`, mais n'ont pas été testées contre une instance Odoo 19 réelle.
  Si l'installation échoue sur un xpath, ouvrir la vue `sale.view_order_form`
  en mode développeur pour ajuster l'expression exacte à la structure
  effective de cette version.
