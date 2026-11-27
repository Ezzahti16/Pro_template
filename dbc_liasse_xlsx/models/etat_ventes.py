# -*- coding: utf-8 -*-
"""Le classeur de l'état des ventes - un fichier à lui, d'une seule feuille.

L'état des ventes n'est pas un des 25 tableaux de la liasse : c'est une
déclaration séparée, qui se dépose séparément. Elle a donc son propre
classeur, produit par son propre bouton, et non une feuille de plus dans
celui de la liasse. Les chiffres, eux, sont ceux de
`res.company._dbc_etat_ventes()` dans dbc_liasse_edi - le même calcul que le
fichier EDI de cette déclaration, donc le classeur et le XML ne peuvent pas
se contredire.

La feuille est dessinée ici et non posée dans un gabarit, contrairement aux
25 autres : le détail des ventes a autant de lignes qu'il y a de clients, ce
qu'aucun gabarit à lignes fixes ne peut porter.

La maquette suit la feuille « Etat ventes » du classeur BILAN2, à un détail
près et c'est voulu : BILAN2 pose ses quatre tableaux côte à côte (colonnes
A-D, F-G, I-J, L-M), ici ils sont empilés. Les montants tombent alors tous
dans les deux mêmes colonnes, les trois blocs s'additionnent à la verticale
sous les yeux, et la feuille s'imprime sur une page de large au lieu de se
couper au milieu.
"""
import io
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Ce qui sépare deux blocs : une ligne vide.
_GAP = 1

# Largeur des colonnes, en caractères. La première porte des raisons
# sociales entières, les deux dernières des montants à neuf chiffres.
_WIDTHS = {1: 48, 2: 22, 3: 18, 4: 18}

_MONEY = '#,##0.00'

_FEUILLE = 'EtatVentes'


class AccountReportCustomHandlerEtatVentesXlsx(models.AbstractModel):
    # Pas de bouton ajouté aux écrans des états : l'état des ventes n'est pas
    # un tableau de la liasse, et sa place est sur la déclaration, qui porte
    # déjà la société et l'exercice.
    _inherit = 'account.report.custom.handler'

    # --------------------------------------------------------------- générateur

    def dbc_etat_ventes_xlsx_export(self, options):
        """Le classeur de l'état des ventes pour la période sélectionnée."""
        report = self.env['account.report'].browse(options['report_id'])
        company = self._dbc_edi_company(report, options)
        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        content = self._dbc_etat_ventes_classeur(
            company, date_from, date_to, bool(options.get('all_entries')))
        return {
            'file_name': '%s.xlsx' % company._dbc_etat_ventes_basename(date_to),
            'file_content': content,
            'file_type': 'xlsx',
        }

    def _dbc_etat_ventes_classeur(self, company, date_from, date_to, all_entries):
        """Un classeur neuf, d'une feuille, et les octets du fichier."""
        try:
            import openpyxl
        except ImportError:
            raise UserError(_(
                "The Python library 'openpyxl' is required to export the etat "
                "des ventes as an Excel workbook, and is not installed on this "
                "server."))

        workbook = openpyxl.Workbook()
        # Workbook() naît avec une feuille « Sheet » ; on la renomme plutôt que
        # d'en créer une seconde, sinon le classeur en porterait une vide.
        workbook.active.title = _FEUILLE
        self._dbc_etat_ventes_feuille(
            workbook, company, date_from, date_to, all_entries)

        stream = io.BytesIO()
        workbook.save(stream)
        return stream.getvalue()

    # ------------------------------------------------------------- la feuille

    def _dbc_etat_ventes_feuille(self, workbook, company, date_from, date_to,
                                 all_entries):
        """Dessiner les quatre tableaux, empilés, dans la feuille EtatVentes."""
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.properties import PageSetupProperties

        data = company._dbc_etat_ventes(date_from, date_to, all_entries)
        # Le même refus que le fichier EDI, et pour la même raison : un
        # classeur sans une seule pièce n'apprend rien, alors que le message
        # dit où chercher. La ligne « Aucune vente nominative » de la feuille
        # garde son emploi - elle sert quand il y a bien des ventes, mais
        # toutes dans les deux cumuls anonymes.
        company._dbc_etat_ventes_exiger(data, date_from, date_to, all_entries)

        if _FEUILLE in workbook.sheetnames:
            sheet = workbook[_FEUILLE]
        else:
            sheet = workbook.create_sheet(_FEUILLE)

        thin = Side(style='thin', color='FF000000')
        box = Border(left=thin, right=thin, top=thin, bottom=thin)
        grey = PatternFill('solid', fgColor='FFD9D9D9')
        titre = Font(bold=True, size=12)
        gras = Font(bold=True)
        note = Font(italic=True, size=8, color='FF808080')
        droite = Alignment(horizontal='right')
        centre = Alignment(horizontal='center', vertical='center')
        gauche = Alignment(horizontal='left', vertical='center')

        for col, width in _WIDTHS.items():
            sheet.column_dimensions[get_column_letter(col)].width = width

        def merge(row, first, last):
            if last > first:
                sheet.merge_cells(start_row=row, start_column=first,
                                  end_row=row, end_column=last)

        def put(row, col, value, font=None, fill=None, fmt=None, align=None,
                border=False):
            cell = sheet.cell(row=row, column=col)
            cell.value = value
            if font:
                cell.font = font
            if fill:
                cell.fill = fill
            if fmt:
                cell.number_format = fmt
            if align:
                cell.alignment = align
            if border:
                cell.border = box
            return cell

        def encadrer(row, first=1, last=4):
            """Le trait fait le tour de toute la ligne, cellules fusionnées
            comprises : openpyxl ne propage pas la bordure de l'ancre."""
            for col in range(first, last + 1):
                sheet.cell(row=row, column=col).border = box

        def bloc(row, numero, intitule):
            """Le bandeau de titre d'un des quatre tableaux."""
            merge(row, 1, 4)
            put(row, 1, '%s - %s' % (numero, intitule), font=gras, fill=grey,
                align=gauche)
            encadrer(row)
            return row + 1

        def montants(row, intitule, ht, ttc, font=None):
            """Une ligne de cumul : le libellé sur A-B, les deux montants."""
            merge(row, 1, 2)
            put(row, 1, intitule, font=font)
            put(row, 3, ht, font=font, fmt=_MONEY, border=True)
            put(row, 4, ttc, font=font, fmt=_MONEY, border=True)
            encadrer(row)
            return row + 1

        # ------------------------------------------------------------- en-tête
        merge(1, 1, 4)
        put(1, 1, "DECLARATION - ETAT DES VENTES -EDI-", font=titre,
            fill=grey, align=centre)
        sheet.row_dimensions[1].height = 24

        put(3, 1, company._dbc_liasse_raison_sociale(), font=gras)
        merge(3, 3, 4)
        put(3, 3, _("Identifiant fiscal: %s",
                    company._dbc_liasse_identifiant_fiscal()), align=droite)
        merge(4, 1, 4)
        put(4, 1, _("Exercice du %(start)s au %(end)s",
                    start=date_from.strftime('%d/%m/%Y'),
                    end=date_to.strftime('%d/%m/%Y')))

        # ------------------------------------- 1. détail des ventes par client
        row = entete_row = bloc(6, '1', "Détail des ventes par client")
        for col, texte in enumerate(["Nom client ou raison sociale",
                                     "ICE client", "Montant HT",
                                     "Montant TTC"], start=1):
            put(row, col, texte, font=gras, fill=grey,
                align=centre if col > 2 else None, border=True)
        sheet.freeze_panes = sheet.cell(row=row + 1, column=1).coordinate
        row += 1

        if data['detail']:
            for ligne in data['detail']:
                put(row, 1, ligne['nom'], border=True)
                # L'ICE part en texte : il commence parfois par un zéro, et
                # Excel le mangerait s'il le prenait pour un nombre. Une case
                # vide se voit, et c'est le but : le fichier EDI refuse de
                # déclarer un client nommé sans ICE.
                put(row, 2, ligne['ice'], border=True).number_format = '@'
                put(row, 3, ligne['ht'], fmt=_MONEY, border=True)
                put(row, 4, ligne['ttc'], fmt=_MONEY, border=True)
                row += 1
        else:
            merge(row, 1, 4)
            put(row, 1, "Aucune vente nominative sur l'exercice.", font=note)
            encadrer(row)
            row += 1

        row = montants(row, "Total du détail des ventes par client",
                       sum(l['ht'] for l in data['detail']),
                       sum(l['ttc'] for l in data['detail']), font=gras)

        # --------------------------------------------- 2. divers clients locaux
        row = bloc(row + _GAP, '2', "Divers clients locaux")
        row = montants(row, "Montant total des ventes aux divers clients locaux",
                       data['divers']['ht'], data['divers']['ttc'])

        # ------------------------------------------------ 3. clients à l'export
        row = bloc(row + _GAP, '3', "Clients à l'export")
        row = montants(row, "Montant total des ventes à l'export",
                       data['export']['ht'], data['export']['ttc'])

        # ------------------------------------------------------------ 4. totaux
        row = bloc(row + _GAP, '4', "Total de l'état des ventes")
        merge(row, 1, 2)
        put(row, 1, None, fill=grey)
        put(row, 3, "TOTAL HT", font=gras, fill=grey, align=centre, border=True)
        put(row, 4, "TOTAL TTC", font=gras, fill=grey, align=centre, border=True)
        encadrer(row)
        row = montants(row + 1, "Ventes déclarées (1 + 2 + 3)",
                       data['total']['ht'], data['total']['ttc'], font=gras)

        # --------------------------------------------------------- provenance
        merge(row + _GAP, 1, 4)
        put(row + _GAP, 1,
            "Montants en %s, pris sur %s pièce(s) : factures et avoirs clients "
            "dont la date de facture tombe dans l'exercice%s. Les avoirs "
            "viennent en déduction du client qu'ils rectifient."
            % (company.currency_id.name, data['pieces'],
               "" if all_entries else ", comptabilisées"),
            font=note)

        # Une page de large, autant de pages de haut qu'il faut de clients.
        sheet.page_setup.orientation = 'portrait'
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        sheet.print_title_rows = '%d:%d' % (entete_row, entete_row)

        _logger.info(
            "Etat des ventes XLSX %s (%s..%s): %d client(s) nomme(s), "
            "divers %.2f HT, export %.2f HT, total %.2f HT",
            company.display_name, date_from, date_to, len(data['detail']),
            data['divers']['ht'], data['export']['ht'], data['total']['ht'])
        return sheet
