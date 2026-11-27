
from odoo import models, fields, api ,_
from markupsafe import Markup

import logging
_logger = logging.getLogger(__name__)

WARNING_MARKER = '__ICE_DUPLICATE_WARNING__'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    rc = fields.Char(string='RC')
    tp = fields.Char(string='TP')
    iff = fields.Char(string='IF')
    cnss = fields.Char(string='CNSS')
    ice = fields.Char(string='ICE' )
    capital = fields.Char(string='Capital')
    cin = fields.Char(string='CIN')

    # @api.model
    # def create(self, vals):
    #     result = super().create(vals)
    #     self._check_ice_warning(result)
    #     return result

    # def write(self, values):
    #     res = super().write(values)
    #     if 'ice' in values:
    #         self._check_ice_warning(self)
    #     return res

    # def _check_ice_warning(self, records):
    #     for record in records:
    #         if record.ice:
    #             record._remove_ice_warnings()
    #             copies = self.env['res.partner'].search([
    #                 ('ice', '=', record.ice),
    #                 ('id', '!=', record.id)
    #             ])
    #             if copies:
    #                 names = ", ".join(copies.mapped('name'))
                    
    #                 message_body = Markup(f"""
    #                 <div style='padding: 10px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;'>
    #                 <span style='font-size: 18px;'>⚠️</span> 
    #                 <strong style='color: #856404;'>Attention !</strong><br/>
    #                 <span style='color: #856404;'>L'ICE <strong>%s</strong> existe déjà pour : <strong>%s</strong></span>
    #                 <span style='display:none'>{WARNING_MARKER}</span>
    #                 </div> """
    #                 ) % (record.ice, names)
                    
    #                 record.message_post(
    #                     body=message_body,
    #                     message_type='comment',
    #                     subtype_xmlid='mail.mt_note',
    #                 )

    # def _remove_ice_warnings(self):
    #     """Remove previous ICE warnings from chatter"""
    #     messages = self.env['mail.message'].search([
    #         ('model', '=', self._name),
    #         ('res_id', 'in', self.ids),
    #         ('body', 'ilike', WARNING_MARKER),
    #     ])
    #     _logger.info(f"///////////////////{messages}")
    #     if messages:
    #         messages.unlink()
                
                

    # @api.model
    # def create(self, vals):
    #     ice = vals.get('ice')
    #     _logger.info(f"IIIIIIIIIIIIIIIIIIICE : {ice}")
    #     if ice :
    #         copies = self.env['res.partner'].search([('ice' , '=' , ice)])
    #         if copies :
    #             names = ", ".join(copies.mapped('name'))
    #             self.env['bus.bus']._sendone(
    #                 self.env.user.partner_id,
    #                 'simple_notification',
    #                 {
    #                     'title': _("Faites attention !"),
    #                     'message': _("L'ICE %s existe déjà pour : %s") % (ice, names),
    #                     'type': 'warning',
    #                     'sticky': False,
    #                 }
    #             )
    #     return super().create(vals)
    
    
    # def write(self, values):
    #     ice = values.get('ice')
    #     _logger.info(f"IIIIIIIIIIIIIIIIIIICE : {ice}")
    #     if ice:
    #         copies = self.env['res.partner'].search([('ice', '=', ice)])
    #         if copies:
    #             names = ", ".join(copies.mapped('name'))
    #             self.env['bus.bus']._sendone(
    #                 self.env.user.partner_id,
    #                 'simple_notification',
    #                 {
    #                     'title': _("Faites attention !"),
    #                     'message': _("L'ICE %s existe déjà pour : %s") % (ice, names),
    #                     'type': 'warning',
    #                     'sticky': False,
    #                 }
    #             )
    #     res = super().write(values)
    #     return res

  
   
        

class ResCompany(models.Model):
    _inherit = 'res.company'

    # Dénomination légale, telle qu'elle doit figurer sur une déclaration
    # fiscale. Séparée du nom de la société parce que le nom sous lequel Odoo
    # tourne est souvent un nom commercial ou abrégé, alors qu'une liasse ou
    # un état des ventes doit porter le nom enregistré. Laissée vide, c'est le
    # nom de la société qui sert.
    raison_sociale = fields.Char(string='Raison sociale')
    rc = fields.Char(string='RC')
    tp = fields.Char(string='TP')
    iff = fields.Char(string='IF')
    cnss = fields.Char(string='CNSS')
    ice = fields.Char(string='ICE')
    capital = fields.Char(string='Capital')
    rib = fields.Char(string='RIB')

