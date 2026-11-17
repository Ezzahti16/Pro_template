# -*- coding: utf-8 -*-
"""The liasse as a typeset document.

This is not the workbook photographed. The figures and the wording are the
ones the Odoo reports show, and they are laid out here as a report meant to be
read: one tableau to a page, a banded heading carrying its number and title, a
column header that repeats when a tableau runs on, and nil lines left blank as
a liasse leaves them.

Nothing here imports Odoo - it turns plain dicts into HTML - so the layout can
be built and looked at without a database.
"""

PAGE_MARGIN_MM = 10.0

# the deep slate the headings are banded in, and the greys around it
INK = '#17304a'
BAND_TEXT = '#ffffff'
BAND_FAINT = '#b9c7d6'
HEAD_BG = '#e9eef3'
RULE = '#d7dee5'
RULE_FAINT = '#e8edf1'
RULE_STRONG = '#8fa0b0'
TOTAL_BG = '#f3f6f9'
MUTED = '#6d7a86'

# a tableau with this many columns is printed across the page
LANDSCAPE_FROM = 7

CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
         font-size: 8pt; color: #1b1b1b; -webkit-print-color-adjust: exact; }

  /* ---------------------------------------------------------- cover page */
  .cover { padding-top: 20mm; text-align: center; }
  .cover .kicker { font-size: 15pt; letter-spacing: 3px; color: %(ink)s;
                   font-weight: bold; line-height: 1.5; }
  .cover .main { font-size: 24pt; letter-spacing: 1px; color: %(ink)s;
                 font-weight: bold; margin-top: 4mm; }
  .cover .sub { font-size: 11pt; color: %(muted)s; margin-top: 2mm; }
  .cover .rule { width: 60mm; height: 2px; background: %(ink)s;
                 margin: 8mm auto 0 auto; }
  .cover .identity { margin: 20mm auto 0 auto; width: 128mm; text-align: left; }
  .cover .identity table { width: 100%%; border-collapse: collapse; }
  .cover .identity td { padding: 2.6mm 0; border-bottom: 1px solid %(rule)s;
                        font-size: 10pt; vertical-align: bottom; }
  .cover .identity td.key { width: 46mm; color: %(muted)s; font-size: 8.5pt;
                            text-transform: uppercase; letter-spacing: 1px; }
  .cover .identity td.val { font-weight: bold; color: #111; }
  .cover .periode { margin-top: 14mm; font-size: 11pt; color: %(ink)s;
                    font-weight: bold; }
  .cover .sign { margin: 26mm 16mm 0 auto; width: 70mm; text-align: center; }
  .cover .sign .when { font-size: 10pt; }
  .cover .sign .who { margin-top: 2mm; font-size: 9pt; color: %(muted)s;
                      border-top: 1px solid %(rule)s; padding-top: 2mm; }

  /* ------------------------------------------------------------ tableaux */
  /* One piece to a page, said as a break AFTER each rather than before the
     next: a break before the first piece of a run opens it on a blank sheet,
     and two forced breaks meeting between the cover and tableau 1 is not
     something every renderer collapses. */
  div.page { page-break-after: always; }
  div.page:last-child { page-break-after: auto; }
  table.liasse { width: 100%%; border-collapse: collapse; }
  table.liasse thead { display: table-header-group; }
  table.liasse tr { page-break-inside: avoid; }

  /* the band is laid out with a nested table rather than a float: the
     renderer this is printed with drops a floated span inside a cell */
  tr.band th { background: %(ink)s; color: %(band)s; text-align: left;
               padding: 2mm 3mm; font-size: 9pt; font-weight: normal; }
  table.bandline { width: 100%%; border-collapse: collapse; }
  table.bandline td { border: none; padding: 0; color: %(band)s;
                      vertical-align: middle; }
  table.bandline td.no { font-weight: bold; letter-spacing: 1.5px;
                         text-transform: uppercase; font-size: 7.5pt;
                         color: %(bandfaint)s; padding-bottom: 0.8mm; }
  table.bandline td.ti { font-weight: bold; font-size: 10.5pt; }
  table.bandline td.ident { text-align: right; font-size: 7.5pt;
                            color: %(bandfaint)s; white-space: nowrap;
                            padding-left: 6mm; }

  /* the heading of one table of a tableau the form prints as several:
     under the band, over its own column header */
  tr.part th { text-align: left; color: %(ink)s; font-weight: bold;
               font-size: 9.5pt; padding: 2.4mm 0 1.6mm 0;
               border-bottom: 1px solid %(ink)s; }
  tr.part.gap th { padding-top: 8mm; }

  tr.cols th { background: %(headbg)s; color: %(ink)s; font-size: 7pt;
               font-weight: bold; text-transform: uppercase;
               letter-spacing: 0.3px; padding: 1.4mm 2mm; vertical-align: bottom;
               border-bottom: 1px solid %(rulestrong)s; }
  tr.cols th.lbl { text-align: left; }
  tr.cols th.num { text-align: right; }

  td { padding: 0.65mm 2mm; border-bottom: 1px solid %(rule)s;
       vertical-align: top; }
  /* a hairline between the columns: it holds a sixteen-column tableau
     together, and turns a tableau with nothing on it into a form that can be
     written on */
  td + td, tr.cols th + th { border-left: 1px solid %(rulefaint)s; }
  td.lbl { text-align: left; }
  td.num { text-align: right; white-space: nowrap; }
  td.mid, th.mid { text-align: center; white-space: nowrap; }
  tr.lvl1 td.lbl { padding-left: 2mm; }
  tr.lvl2 td.lbl { padding-left: 6mm; }
  tr.lvl3 td.lbl { padding-left: 10mm; }
  tr.lvl4 td.lbl { padding-left: 14mm; }

  tr.total td { font-weight: bold; background: %(totalbg)s;
                border-top: 1px solid %(rulestrong)s; }
  tr.head td { font-weight: bold; color: %(ink)s; text-transform: uppercase;
               font-size: 8pt; letter-spacing: 0.6px; padding-top: 2.6mm;
               border-bottom: 1px solid %(rulestrong)s; }
  tr.empty td { color: %(muted)s; font-style: italic; text-align: center;
                padding: 9mm 2mm; border-bottom: none; }
  /* a row of the form with nothing written on it yet: ruled, and the height
     of a row that does carry something */
  tr.blank td { height: 3.8mm; }
  p.note { margin: 2.5mm 0 0 0; font-size: 7.5pt; color: %(muted)s; }
""" % {'ink': INK, 'band': BAND_TEXT, 'bandfaint': BAND_FAINT,
       'headbg': HEAD_BG, 'rule': RULE, 'rulefaint': RULE_FAINT,
       'rulestrong': RULE_STRONG,
       'totalbg': TOTAL_BG, 'muted': MUTED}


def escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def money(value):
    """An amount as a liasse prints it: a no-break space every three digits
    and a comma before the two decimals."""
    text = '{:,.2f}'.format(float(value))
    whole, _dot, frac = text.partition('.')
    return '%s,%s' % (whole.replace(',', ' '), frac)


def _label(text):
    """The workbook writes a line break inside a cell as a pipe; it is a break
    in the wording, not a character the form prints."""
    text = escape(text).replace('|', ' ')
    return ' '.join(text.split())


def document(body):
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"/>'
            '<style>%s</style></head><body>%s</body></html>' % (CSS, body))


def cover_html(cover):
    rows = []
    for key, value in (('Raison sociale', cover.get('raison_sociale')),
                       ('Taxe professionnelle', cover.get('taxe_professionnelle')),
                       ('Identifiant fiscal', cover.get('identifiant')),
                       ('Adresse', cover.get('adresse'))):
        rows.append('<tr><td class="key">%s</td><td class="val">%s</td></tr>'
                    % (escape(key), escape(value or '')))
    return (
        '<div class="cover">'
        '<div class="kicker">PIÈCES ANNEXES À LA<br/>DÉCLARATION FISCALE</div>'
        '<div class="main">IMPÔT SUR LES SOCIÉTÉS</div>'
        '<div class="sub">(Modèle Comptable Normal)</div>'
        '<div class="rule"></div>'
        '<div class="identity"><table>%s</table></div>'
        '<div class="periode">%s</div>'
        '<div class="sign"><div class="when">%s</div>'
        '<div class="who">Signature</div></div>'
        '</div>' % (''.join(rows), escape(cover.get('exercice') or ''),
                    escape(cover.get('lieu_date') or '')))


def _band_row(tableau, identifiant, width):
    """The dark heading: the number of the tableau on the form, its title, and
    the identifiant fiscal the DGI asks for on every page."""
    number = 'Tableau n° %s%s' % (
        escape(tableau['number']),
        ' &mdash; %s' % escape(tableau['part']) if tableau.get('part') else '')
    ident = ('<td class="ident" rowspan="2">Identifiant fiscal&nbsp;: %s</td>'
             % escape(identifiant)) if identifiant else ''
    return ('<tr class="band"><th colspan="%d">'
            '<table class="bandline">'
            '<tr><td class="no">%s</td>%s</tr>'
            '<tr><td class="ti">%s</td></tr>'
            '</table></th></tr>'
            % (width, number, ident, escape(tableau['title'])))


def _table_html(block, width, stub, aligns, head):
    """One table: the header rows given to it, then the lines of the block."""
    head = list(head)
    head.append('<tr class="cols">')
    if stub is not None:
        head.append('<th class="lbl">%s</th>' % escape(stub))
    for index, name in enumerate(block['columns']):
        head.append('<th class="%s">%s</th>' % (aligns[index], _label(name)))
    head.append('</tr></thead>')

    rows = ['<tbody>']
    if not block['lines']:
        rows.append('<tr class="empty"><td colspan="%d">%s</td></tr>'
                    % (width, escape(block.get('empty')
                                     or "Aucun élément à déclarer au titre de cet exercice.")))
    for line in block['lines']:
        classes = ['lvl%d' % min(line.get('level') or 0, 4)]
        if line.get('heading'):
            classes.append('head')
        elif line.get('total'):
            classes.append('total')
        if line.get('blank'):
            classes.append('blank')
        rows.append('<tr class="%s">' % ' '.join(classes))
        if line.get('heading') and stub is None:
            # with no column for the wording, a heading runs the whole width
            rows.append('<td class="lbl" colspan="%d">%s</td>'
                        % (width, _label(line['name'])))
            rows.append('</tr>')
            continue
        if stub is not None:
            rows.append('<td class="lbl">%s</td>' % _label(line['name']))
        for index, cell in enumerate(line['cells']):
            rows.append('<td class="%s">%s</td>'
                        % (aligns[index], _label(cell or '')))
        rows.append('</tr>')
    rows.append('</tbody>')
    return '<table class="liasse">%s%s</table>' % (''.join(head), ''.join(rows))


def tableau_html(tableau, identifiant):
    """One tableau: the banded heading, the column header that repeats when it
    runs on, and its lines.

    A tableau the form draws as several tables with columns of their own - the
    Tableau de financement, the synthèse des masses and then the emplois et
    ressources - carries them as `blocks`, and they are set one under the
    other, each with its own heading and its own column header, as the screen
    and the workbook keep them. Running them into one grid would leave half of
    every line empty.
    """
    blocks = tableau.get('blocks') or [tableau]
    out = []
    for index, block in enumerate(blocks):
        stub = block.get('stub')
        width = len(block['columns']) + (1 if stub is not None else 0)
        aligns = block.get('aligns') or ['num'] * len(block['columns'])
        head = ['<thead>']
        if not index:
            # in the header of the first table, so that it is said again at
            # the top of the page when a tableau runs over
            head.append(_band_row(tableau, identifiant, width))
        if block.get('subtitle'):
            head.append('<tr class="part%s"><th colspan="%d">%s</th></tr>'
                        % ('' if not index else ' gap', width,
                           escape(block['subtitle'])))
        out.append(_table_html(block, width, stub, aligns, head))
    if tableau.get('note'):
        out.append('<p class="note">%s</p>' % escape(tableau['note']))
    return ''.join(out)


def is_landscape(tableau):
    """Wide enough to be printed across the page - judged on the widest of
    the tables a tableau is made of."""
    blocks = tableau.get('blocks') or [tableau]
    return max(len(block['columns']) for block in blocks) >= LANDSCAPE_FROM


def runs(cover, tableaux, identifiant):
    """The document split into the fewest stretches of one page orientation:
    wkhtmltopdf takes a single page setup per run, and the tableaux have to
    keep the order of the liasse."""
    pieces = [('portrait', cover_html(cover))]
    for tableau in tableaux:
        pieces.append(('landscape' if is_landscape(tableau) else 'portrait',
                       tableau_html(tableau, identifiant)))
    out = []
    for orientation, html in pieces:
        page = '<div class="page">%s</div>' % html
        if out and out[-1][0] == orientation:
            out[-1][1].append(page)
        else:
            out.append((orientation, [page]))
    return [(orientation, document(''.join(parts))) for orientation, parts in out]
