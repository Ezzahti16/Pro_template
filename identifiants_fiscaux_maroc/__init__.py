from . import models

from odoo import fields

def set_ice(env):
    """Set the nextcall of the cron job to the current time."""
    
    partners =  env['res.partner'].search([])
    for par in partners :
        if par.company_registry :
            par.write({
                'ice' : par.company_registry
            })
    
    companys = env['res.company'].search([])
    for com in companys :
        if com.company_registry :
            com.write({
                'ice' : com.company_registry
            })