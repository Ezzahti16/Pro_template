# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQuotationVersioning(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Client Test Versionning'})
        cls.product = cls.env['product.product'].create({
            'name': 'Produit Test',
            'type': 'consu',
            'list_price': 100.0,
        })
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'order_line': [(0, 0, {
                'product_id': cls.product.id,
                'product_uom_qty': 2,
                'price_unit': 100.0,
            })],
        })

    def test_01_initial_version_is_v1(self):
        """Un devis nouvellement créé est en V1, sans parent, actif."""
        self.assertEqual(self.order.version_number, 1)
        self.assertFalse(self.order.parent_quotation_id)
        self.assertTrue(self.order.is_active_version)
        self.assertFalse(self.order.is_locked_version)

    def test_02_sending_locks_the_version(self):
        """Le passage à l'état 'sent' fige le devis."""
        self.order.state = 'sent'
        self.assertTrue(self.order.is_locked_version)

    def test_03_write_on_locked_order_raises(self):
        """Toute écriture sur un champ sensible d'un devis figé est bloquée."""
        self.order.state = 'sent'
        with self.assertRaises(UserError):
            self.order.write({
                'order_line': [(0, 0, {
                    'product_id': self.product.id,
                    'product_uom_qty': 5,
                    'price_unit': 90.0,
                })],
            })

    def test_04_create_new_version_success(self):
        """Créer une nouvelle version à partir d'un devis figé et actif
        produit un V2 correctement lié.
        """
        self.order.state = 'sent'
        action = self.order.action_create_new_version()
        new_version = self.env['sale.order'].browse(action['res_id'])

        self.assertEqual(new_version.version_number, 2)
        self.assertEqual(new_version.parent_quotation_id, self.order)
        self.assertEqual(new_version.state, 'draft')

    def test_05_parent_becomes_inactive_after_new_version(self):
        """Le devis parent n'est plus la version active une fois la
        version fille créée.
        """
        self.order.state = 'sent'
        self.order.action_create_new_version()
        self.assertFalse(self.order.is_active_version)

    def test_06_confirm_on_replaced_version_raises(self):
        """La confirmation d'un devis remplacé par une version plus
        récente est interdite.
        """
        self.order.state = 'sent'
        self.order.action_create_new_version()
        with self.assertRaises(UserError):
            self.order.action_confirm()

    def test_07_unlink_parent_with_children_raises(self):
        """La suppression d'un devis parent ayant des versions filles
        est bloquée.
        """
        self.order.state = 'sent'
        self.order.action_create_new_version()
        with self.assertRaises(UserError):
            self.order.unlink()

    def test_08_root_quotation_id_across_chain(self):
        """Sur une chaîne V1 -> V2 -> V3, toutes les versions doivent
        pointer vers V1 comme racine.
        """
        self.order.state = 'sent'
        action_v2 = self.order.action_create_new_version()
        v2 = self.env['sale.order'].browse(action_v2['res_id'])

        v2.state = 'sent'
        action_v3 = v2.action_create_new_version()
        v3 = self.env['sale.order'].browse(action_v3['res_id'])

        self.assertEqual(v2.root_quotation_id, self.order)
        self.assertEqual(v3.root_quotation_id, self.order)

    def test_09_version_diff_summary_detects_changes(self):
        """Le différentiel détecte une variation de quantité et de prix
        entre deux versions consécutives.
        """
        self.order.state = 'sent'
        action = self.order.action_create_new_version()
        new_version = self.env['sale.order'].browse(action['res_id'])

        new_version.write({
            'order_line': [(1, new_version.order_line[0].id, {
                'product_uom_qty': 5,
                'price_unit': 90.0,
            })],
        })

        self.assertIn('Modifié', new_version.version_diff_summary)

    def test_10_new_version_number_matches_chain_length(self):
        """Le compteur version_count reflète le nombre total de
        versions dans la chaîne.
        """
        self.order.state = 'sent'
        action_v2 = self.order.action_create_new_version()
        v2 = self.env['sale.order'].browse(action_v2['res_id'])
        self.assertEqual(self.order.version_count, 2)
        self.assertEqual(v2.version_count, 2)
