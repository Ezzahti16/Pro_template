# -*- coding: utf-8 -*-
"""L'état des ventes : les chiffres, et le fichier EDI qui les porte.

C'est une déclaration à part entière, pas un des 25 tableaux de la liasse.
Elle a son propre fichier (racine <LesEtatDesVentes>), elle ne porte aucun
code EDI de la liasse, et elle se dépose séparément - d'où deux boutons à
elle sur la déclaration, et deux fichiers à elle, et non une feuille de plus
dans le classeur de la liasse.

Les chiffres ne viennent pas du moteur de la liasse mais des **factures
clients** : c'est la seule source qui sache à qui on a vendu. Un grand-livre
importé en vrac - comme ceux des bases de reprise - ne porte pas le client
sur ses lignes de produits, donc sur une telle base la déclaration sort vide,
et c'est la vérité, pas une panne.

Trois blocs, exclusifs l'un de l'autre, tels que le fichier de référence les
écrit : la liste des clients nommés avec leur ICE, le cumul anonyme des
divers clients locaux, le cumul anonyme des clients à l'export.

Ce qui range une vente dans l'un ou dans l'autre :

* **l'export est décidé par la devise de la facture** - toute facture libellée
  dans une autre devise que celle de la société part dans le cumul export.
  Rien d'autre n'intervient : ni le pays du client, ni une case à cocher. Le
  pays l'a décidé un temps, et il le faisait en silence : sur une base réelle,
  des factures en dirhams d'un client dont la fiche portait un pays étranger
  se retrouvaient à l'export sans que personne l'ait demandé, et il fallait
  remonter aux pièces pour comprendre. La devise, elle, se lit sur la facture ;
* **les divers clients locaux** se déclarent sur la fiche du client, par la
  case « Client divers ». Les comptes ne peuvent pas le dire : le compte 3421
  ne dit pas si le client a communiqué son ICE.

Conséquence à connaître : le classement ne porte plus sur le client mais sur
la facture. Un même client facturé tantôt en dirhams tantôt en devise apparaît
nominativement pour sa part en dirhams et dans le cumul export pour l'autre.
C'est voulu - c'est ce que la règle de la devise veut dire.
"""
import io
import logging
import zipfile
from xml.sax.saxutils import escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from .account_report import _clean_cell_text, _clean_text

_logger = logging.getLogger(__name__)

# L'en-tête du fichier de référence, au caractère près. Il ne dit pas la même
# chose que celui de la liasse (« utf-8 », sans standalone) : ce sont deux
# déclarations différentes, produites par deux générateurs différents, et
# chacune est comparable à son propre fichier de référence.
_EV_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_EV_RACINE = 'LesEtatDesVentes'
_EV_XMLNS = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'

# Tabulation et CRLF, sans saut de ligne final : la disposition du fichier de
# référence. L'indentation est cosmétique et ne peut changer aucune valeur
# déclarée - rien n'est jamais inséré à l'intérieur d'un élément qui porte du
# texte.
_EV_INDENT = '\t'
_EV_LINE_END = '\r\n'
_EV_ENCODING = 'utf-8'

# Deux décimales toujours, point décimal. La liasse écrit « 200000 » pour un
# montant rond (_format_value) ; l'état des ventes écrit « 200000.00 ». Deux
# fichiers, deux conventions, et c'est celle du fichier de référence qui
# s'applique ici.
_EV_MONTANT = '%.2f'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    ma_client_divers = fields.Boolean(
        string="Client divers",
        help="Ce client part dans le bloc « Divers clients locaux » de l'état "
             "des ventes : son nom et son ICE ne sont pas déclarés, seul le "
             "cumul de ses ventes l'est. C'est la case des clients de "
             "passage et de ceux qui n'ont pas communiqué d'ICE.",
    )

    # Pas de case « Client à l'export » : l'export se lit sur la devise de la
    # facture, pas sur la fiche du client. Une case de plus ne ferait que
    # permettre de contredire la facture.

    # ------------------------------------------------------- état des ventes

    def _dbc_ventes_ice(self):
        """L'ICE du client.

        C'est `company_registry`, le champ que l'onglet « Identifiants
        Fiscaux » de la fiche affiche sous l'étiquette ICE. Rendu tel quel,
        sans contrôle de longueur : un ICE à 14 chiffres au lieu de 15 est une
        erreur à voir, pas une valeur à masquer.
        """
        self.ensure_one()
        return (self.company_registry or '').strip()

    def _dbc_ventes_bloc(self, currency, company):
        """Dans lequel des trois blocs une vente à ce client tombe.

        La devise est celle de la facture, pas celle du client : c'est la
        facture qui dit si la vente est à l'export.

        « Divers » passe avant « export ». Un client coché divers ne doit pas
        être nommé, et le cumul export est tout aussi anonyme, donc les deux
        cases conviendraient - mais une vente ne peut être comptée qu'une
        fois, et c'est la déclaration explicite du comptable qui l'emporte sur
        la déduction faite de la devise.
        """
        self.ensure_one()
        if self.ma_client_divers:
            return 'divers'
        if currency and company.currency_id and currency != company.currency_id:
            return 'export'
        return 'detail'


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ------------------------------------------------------------- les chiffres

    def _dbc_etat_ventes(self, date_from, date_to, all_entries=False):
        """Les ventes de l'exercice, réparties dans les trois blocs déclarés.

        {'detail': [{'nom', 'ice', 'ht', 'ttc'}, ...],   un client par ligne
         'divers': {'ht', 'ttc'},                        cumul, sans nom
         'export': {'ht', 'ttc'},                        cumul, sans nom
         'total':  {'ht', 'ttc'},                        les trois ensemble
         'pieces': nombre de factures et avoirs retenus}

        Les avoirs ne font pas de bloc à part : ils portent des montants
        signés négatifs et viennent donc en déduction du client qu'ils
        rectifient, ce qui est bien ce que la déclaration attend - le chiffre
        déclaré est la vente nette, pas la facturation brute.

        La date retenue est celle de la facture, pas celle de l'écriture
        comptable : c'est la date que porte la pièce envoyée au client, et
        celle sur laquelle la DGI rapproche deux déclarations entre elles.

        Les montants, eux, sont toujours en devise de la société : les deux
        champs signés d'Odoo sont déjà convertis, donc une facture en euros se
        déclare en dirhams, ce qui est bien ce que la DGI attend.
        """
        self.ensure_one()
        moves = self.env['account.move']
        domain = [
            ('company_id', '=', self.id),
            ('move_type', 'in', moves.get_sale_types(include_receipts=True)),
            ('state', 'in', ['posted', 'draft'] if all_entries else ['posted']),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ]
        # La devise fait partie de la clé : c'est elle qui décide de l'export,
        # et un même client peut avoir des factures dans les deux. Sans elle
        # dans le regroupement, il faudrait relire les factures une par une.
        groups = moves._read_group(
            domain,
            groupby=['commercial_partner_id', 'currency_id'],
            aggregates=['amount_untaxed_signed:sum',
                        'amount_total_signed:sum', '__count'],
        )

        # Tous les arrondis se font dans la devise de la SOCIÉTÉ, jamais dans
        # celle de la facture : les montants agrégés sont déjà convertis, et
        # `devise` ci-dessous ne sert qu'à dire si la vente est à l'export.
        # Les deux portaient des noms trop proches, et la fin de la méthode a
        # fini par arrondir avec la devise du dernier groupe lu - ce qui
        # plantait net sur un exercice sans aucune vente, la boucle n'ayant
        # alors rien laissé derrière elle.
        devise_societe = self.currency_id
        nommes = {}
        cumuls = {'divers': [0.0, 0.0], 'export': [0.0, 0.0]}
        pieces = 0
        for partner, devise, ht, ttc, count in groups:
            ht = devise_societe.round(ht or 0.0)
            ttc = devise_societe.round(ttc or 0.0)
            pieces += count
            # Une facture sans client ne peut pas être nommée ; elle rejoint
            # les divers, qui est exactement la case des ventes qu'on ne sait
            # pas attribuer.
            bloc = partner._dbc_ventes_bloc(devise, self) if partner else 'divers'
            if bloc == 'detail':
                ligne = nommes.setdefault(partner, {
                    'nom': partner.display_name,
                    'ice': partner._dbc_ventes_ice(),
                    'ht': 0.0,
                    'ttc': 0.0,
                })
                ligne['ht'] += ht
                ligne['ttc'] += ttc
            else:
                cumuls[bloc][0] += ht
                cumuls[bloc][1] += ttc

        # Les plus gros clients en tête : la déclaration sert d'abord à relire
        # ce qu'on déclare, et c'est en haut qu'on relit.
        detail = sorted(nommes.values(),
                        key=lambda ligne: (-ligne['ht'], ligne['nom']))

        divers = {'ht': devise_societe.round(cumuls['divers'][0]),
                  'ttc': devise_societe.round(cumuls['divers'][1])}
        export = {'ht': devise_societe.round(cumuls['export'][0]),
                  'ttc': devise_societe.round(cumuls['export'][1])}
        total = {
            'ht': devise_societe.round(
                sum(l['ht'] for l in detail) + divers['ht'] + export['ht']),
            'ttc': devise_societe.round(
                sum(l['ttc'] for l in detail) + divers['ttc'] + export['ttc']),
        }
        return {'detail': detail, 'divers': divers, 'export': export,
                'total': total, 'pieces': pieces}

    def _dbc_etat_ventes_exiger(self, data, date_from, date_to, all_entries):
        """Refuser de produire un état des ventes qui ne déclare rien.

        Un fichier à zéro client et deux cumuls à 0,00 n'est pas une
        déclaration, c'est le signe qu'on cherche au mauvais endroit : mauvais
        exercice, factures restées en brouillon, ou ventes saisies comme
        simples écritures comptables. Rendu sans un mot, il se déposerait tel
        quel. Le message dit d'où viennent les chiffres et ce qui manque, et
        s'arrête là : à qui connaît son dossier, cela suffit pour savoir où
        regarder.

        Le classeur comme le fichier EDI passent par ici : les deux boutons
        doivent dire la même chose, et le même jour.
        """
        self.ensure_one()
        if data['pieces']:
            return
        raise UserError(_(
            "Aucune vente à déclarer pour l'exercice du %(debut)s au "
            "%(fin)s.\n\n"
            "L'état des ventes se tire des factures clients, et aucune facture "
            "ni avoir client ne porte une date de facture dans cet "
            "exercice%(brouillons)s.",
            debut=date_from.strftime('%d/%m/%Y'),
            fin=date_to.strftime('%d/%m/%Y'),
            brouillons="" if all_entries else " (les pièces en brouillon sont "
                                              "exclues)"))

    def _dbc_etat_ventes_basename(self, date_to):
        """Le nom de fichier, bâti comme celui de la liasse."""
        self.ensure_one()
        name = _clean_text(self.name).replace(' ', '_')
        name = ''.join(ch for ch in name if ch.isalnum() or ch in '_-')
        return 'Etat_Ventes_%s_Au_%s' % (name, date_to.strftime('%d_%m_%Y'))

    # ----------------------------------------------------------- le fichier EDI

    def _dbc_etat_ventes_xml(self, date_from, date_to, all_entries=False):
        """Le fichier <LesEtatDesVentes> de l'exercice, en octets."""
        self.ensure_one()
        identifiant = self._dbc_liasse_identifiant_fiscal()
        if not identifiant:
            raise UserError(_(
                "Aucun Identifiant Fiscal n'est renseigné sur la société "
                "%(company)s.\n\nSimpl-IS rejette un fichier dont "
                "l'<identifiantFiscal> ne correspond pas au déposant, donc il "
                "ne peut pas être produit sans lui. Il se saisit dans "
                "Configuration > Sociétés, onglet « Identifiants Fiscaux », "
                "champ IF.",
                company=self.display_name))

        data = self._dbc_etat_ventes(date_from, date_to, all_entries)
        self._dbc_etat_ventes_exiger(data, date_from, date_to, all_entries)

        # Un client nommé sans ICE n'a rien à faire dans la déclaration : tout
        # l'intérêt du bloc nominatif est que la DGI rapproche les ICE entre
        # deux contribuables, et la case prévue pour un client qui n'a pas
        # communiqué le sien existe déjà, c'est « Divers clients locaux ».
        # Mieux vaut refuser ici, en nommant les fiches à compléter, que
        # déposer un fichier que Simpl-IS renverra sans dire lequel.
        sans_ice = [ligne['nom'] for ligne in data['detail'] if not ligne['ice']]
        if sans_ice:
            raise UserError(_(
                "%(count)s client(s) facturé(s) sur l'exercice n'ont pas "
                "d'ICE :\n\n%(noms)s\n\nPour chacun, soit renseignez l'ICE "
                "sur sa fiche (onglet « Identifiants Fiscaux »), soit cochez "
                "« Client divers » - ses ventes rejoindront alors le cumul "
                "des divers clients locaux, qui est la case prévue pour un "
                "client sans ICE.\n\nLe classeur Excel de l'état des ventes "
                "se produit sans cette condition : il montre les cases vides.",
                count=len(sans_ice),
                noms='\n'.join('  - %s' % nom for nom in sans_ice[:20])
                     + ('\n  - ...' if len(sans_ice) > 20 else '')))

        lignes = [_EV_DECLARATION, '<%s %s>' % (_EV_RACINE, _EV_XMLNS)]

        def balise(niveau, nom, texte):
            lignes.append('%s<%s>%s</%s>' % (
                _EV_INDENT * niveau, nom, escape(texte), nom))

        def ouvrir(niveau, nom):
            lignes.append('%s<%s>' % (_EV_INDENT * niveau, nom))

        def fermer(niveau, nom):
            lignes.append('%s</%s>' % (_EV_INDENT * niveau, nom))

        balise(1, 'identifiantFiscal', identifiant)
        balise(1, 'exerciceFiscalDu', date_from.strftime('%Y-%m-%d'))
        balise(1, 'exerciceFiscalAu', date_to.strftime('%Y-%m-%d'))

        ouvrir(1, 'ListEtatDesVentes')
        for ligne in data['detail']:
            ouvrir(2, 'EtatDesVentes')
            # Les accents restent : le fichier se déclare en UTF-8 et la
            # raison sociale d'un client est ce qu'elle est. Seuls les
            # caractères que le parseur de la DGI ne connaît pas sont pliés,
            # par _clean_cell_text, comme pour tout texte de la liasse.
            balise(3, 'nomClientOrRaisonSocial', _clean_cell_text(ligne['nom']))
            balise(3, 'iceClient', ligne['ice'])
            balise(3, 'montantHTFacturation', _EV_MONTANT % ligne['ht'])
            balise(3, 'montantTTCFacturation', _EV_MONTANT % ligne['ttc'])
            fermer(2, 'EtatDesVentes')
        fermer(1, 'ListEtatDesVentes')

        # Les deux cumuls sont écrits même à zéro. Ce ne sont pas des éléments
        # d'une liste qu'on pourrait laisser vide, ce sont deux blocs uniques
        # de la racine, et deux montants à 0.00 disent « rien à déclarer dans
        # cette case » - ce qui est une information, pas un vide.
        for nom, cumul in (('DiversClientsLocaux', data['divers']),
                           ('ClientsExport', data['export'])):
            ouvrir(1, nom)
            balise(2, 'montantHTFacturation', _EV_MONTANT % cumul['ht'])
            balise(2, 'montantTTCFacturation', _EV_MONTANT % cumul['ttc'])
            fermer(1, nom)

        lignes.append('</%s>' % _EV_RACINE)

        _logger.info(
            "Etat des ventes XML %s (%s..%s): %d client(s) nomme(s), "
            "divers %.2f HT, export %.2f HT, total %.2f HT",
            self.display_name, date_from, date_to, len(data['detail']),
            data['divers']['ht'], data['export']['ht'], data['total']['ht'])

        return _EV_LINE_END.join(lignes).encode(
            _EV_ENCODING, errors='xmlcharrefreplace')


class AccountReportCustomHandlerEtatVentes(models.AbstractModel):
    # Pas de bouton ajouté aux écrans des états : l'état des ventes n'est pas
    # un tableau de la liasse, et sa place est sur la déclaration, qui porte
    # déjà la société et l'exercice. Ces méthodes sont là pour que la
    # déclaration les appelle comme elle appelle les trois autres.
    _inherit = 'account.report.custom.handler'

    def dbc_etat_ventes_xml_export(self, options):
        """Le fichier EDI de l'état des ventes, dans un ZIP d'une pièce -
        comme la liasse, et comme le fichier de référence."""
        report = self.env['account.report'].browse(options['report_id'])
        company = self._dbc_edi_company(report, options)
        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        xml_bytes = company._dbc_etat_ventes_xml(
            date_from, date_to, bool(options.get('all_entries')))

        basename = company._dbc_etat_ventes_basename(date_to)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('%s.xml' % basename, xml_bytes)
        return {
            'file_name': '%s.zip' % basename,
            'file_content': buffer.getvalue(),
            'file_type': 'zip',
        }
