from . import models
from . import wizard


def _post_init_migrate_existing_orders(env):
    """Les devis déjà confirmés (ou annulés) avant l'installation du module
    n'ont jamais eu de circuit de validation : on les marque explicitement
    comme 'validated' pour éviter toute ambiguïté dans les filtres/tableaux
    de bord, et pour qu'un futur re-déclenchement de action_confirm() sur ces
    commandes reste sans effet sur le workflow de validation."""
    SaleOrder = env['sale.order']
    existing_orders = SaleOrder.search([
        ('state', 'in', ['sale', 'cancel']),
        ('validation_state', '=', 'none'),
    ])
    if existing_orders:
        existing_orders.write({'validation_state': 'validated'})
