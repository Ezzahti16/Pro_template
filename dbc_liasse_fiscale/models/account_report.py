# -*- coding: utf-8 -*-
import json
import logging
from platform import node
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Remote formules API
# --------------------------------------------------------------------------- #
_DEFAULT_API_URL = 'http://app.ozirpaie.ma:2024/api/ma/formules'
_API_TIMEOUT = 10

# Cache structure: {url: {'fetched_at': float, 'data': dict, 'ttl': int}}
# TTL is intentionally short (default 10s) so formula changes on the remote
# instance are picked up quickly. Raise via system param if your API is slow.
_API_CACHE = {}


def _get_cache_ttl(icp):
    try:
        return int(icp.get_param('ma_formules.api_cache_ttl', '10') or '10')
    except ValueError:
        return 10


def _normalise_formule(formule):
    """Legacy formules may carry a leading '='; drop it so the parser is happy."""
    if not formule:
        return '0'
    return (formule.strip().lstrip('=').strip()) or '0'


def _invalidate_api_cache(url=None):
    """Force-expire the cache for a specific URL (or all URLs if None).
    Call this from a button action or a scheduled task after updating formulas.
    """
    if url:
        _API_CACHE.pop(url, None)
    else:
        _API_CACHE.clear()


# --------------------------------------------------------------------------- #
# Formule parsing  (unchanged from your original — keeping as-is)
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | '(?P<sq>[^']*)'
  | "(?P<dq>[^"]*)"
  | (?P<op>[()+\-*/])
)""", re.VERBOSE)


def parse_formule(text):
    tokens = []
    pos = 0
    text = (text or '').strip()
    if not text:
        return ('const', 0.0)
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ValueError(_("Unexpected character near %s", text[pos:pos + 10]))
        if m.lastgroup == 'num':
            tokens.append(('num', float(m.group('num'))))
        elif m.lastgroup == 'ident':
            tokens.append(('ident', m.group('ident')))
        elif m.lastgroup in ('sq', 'dq'):
            tokens.append(('str', m.group(m.lastgroup)))
        elif m.lastgroup == 'op':
            tokens.append(('op', m.group('op')))
        pos = m.end()

    index = [0]

    def peek():
        return tokens[index[0]] if index[0] < len(tokens) else (None, None)

    def take(expected_kind=None):
        kind, value = peek()
        if expected_kind and kind != expected_kind:
            raise ValueError(_("Expected %s token", expected_kind))
        index[0] += 1
        return kind, value

    def expr():
        node = term()
        while peek() == ('op', '+') or peek() == ('op', '-'):
            op = take()[1]
            node = ('bin', op, node, term())
        return node

    def term():
        node = factor()
        while peek() == ('op', '*') or peek() == ('op', '/'):
            op = take()[1]
            node = ('bin', op, node, factor())
        return node

    def factor():
        negations = 0
        while True:
            kind, value = peek()
            if (kind, value) == ('op', '-'):
                take()
                negations += 1
            elif (kind, value) == ('op', '+'):
                take()
            else:
                break
        node = atom()
        for _i in range(negations):
            node = negate(node)
        return node

    def negate(node):
        if node[0] == 'se':
            return ('se', node[1], -node[2])
        if node[0] == 'deb':
            return ('deb', node[1], -node[2])
        if node[0] == 'cre':
            return ('cre', node[1], -node[2])
        if node[0] == 'const':
            return ('const', -node[1])
        return ('neg', node)

    def atom():
        kind, value = take()
        if kind == 'num':
            return ('const', value)
        if kind in ('str', 'dq', 'sq'):
            return ('const', 0.0)
        if kind == 'op' and value == '(':
            node = expr()
            if take() != ('op', ')'):
                raise ValueError(_("Unbalanced parenthesis"))
            return node
        if kind == 'ident':
            func = value.upper()
            if take()[0] != 'op':
                raise ValueError(_("Malformed call to %s", func))
            if func == 'CMP':
                # CMP(x): "positive part" of x, i.e. max(x, 0). Used throughout
                # the legacy formules to split a signed amount between two
                # columns (e.g. Montant(+)/Montant(-), or Emplois/Ressources):
                # one side is CMP(x), the other CMP(-x), so exactly one of the
                # two is non-zero depending on the sign of x.
                node = expr()
                if take() != ('op', ')'):
                    raise ValueError(_("Unbalanced parenthesis in call to %s", func))
                return ('cmp', node)
            arg_kind, arg = take()
            if not str(arg).strip():
                raise ValueError(_("%s expects a quoted argument", func))
            closing = take()
            if closing != ('op', ')'):
                raise ValueError(_("Unbalanced parenthesis in call to %s", func))
            if arg_kind == 'num':
                arg = str(int(arg))
            else:
                arg = str(arg).strip()
            if func in ('EDI', 'EDIST'):
                return ('edi', arg)
            if func == 'SE':
                return ('se', arg, 1)
            if func == 'ASE':
                return ('ase', arg)
            if func == 'DEB':
                # DEB('xxx') → total DEBIT only of accounts whose code starts
                # with 'xxx' (no credit netted in, unlike SE/ASE).
                return ('deb', arg, 1)
            if func == 'CRE':
                # CRE('xxx') → total CREDIT only of accounts whose code starts
                # with 'xxx' (no debit netted in, unlike SE/ASE).
                return ('cre', arg, 1)
            if func in ('MDE', 'MCE', 'DE'):
                return ('const', 0.0)
            raise ValueError(_("Unsupported function: %s", func))
        raise ValueError(_("Unexpected token"))

    result = expr()
    if index[0] != len(tokens):
        raise ValueError(_("Trailing characters in formula"))
    return result


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #
def collect_account_prefixes(ast, formules, code, prefixes=None, stack=()):
    prefixes = set() if prefixes is None else prefixes
    if code in stack:
        return prefixes

    def walk(node):
        kind = node[0]
        if kind in ('se', 'ase' , 'deb', 'cre'):
            prefixes.add(node[1])
        elif kind == 'edi':
            ref = node[1]
            if ref not in stack:
                prefixes.update(collect_account_prefixes(
                    parse_formule(formules.get(ref, '')) if ref in formules else ('const', 0.0),
                    formules, ref, prefixes, stack + (code,),
                ))
        else:
            for part in node[1:]:
                if isinstance(part, tuple):
                    walk(part)

    walk(ast)
    return prefixes


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class AccountReportExpression_MaFiscal(models.Model):
    _inherit = 'account.report.expression'

    def _get_auditable_engines(self):
        return super()._get_auditable_engines() | {'custom'}


class AccountReport_MaFiscal(models.Model):
    _inherit = 'account.report'

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _fetch_formules_from_api(self, url, timeout=_API_TIMEOUT):
        """Attempt to fetch the formules map from the remote API.

        Returns a non-empty dict on success, or None on any failure.
        Logs a WARNING (not ERROR) so the fallback is silent in normal ops.
        """
        try:
            req = urllib.request.Request(
                url,
                headers={'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    _logger.warning(
                        "Formules API returned HTTP %s for %s", resp.status, url
                    )
                    return None
                raw = resp.read().decode('utf-8')

            data = json.loads(raw)
            raw_formules = data.get('formules') or {}

            if not raw_formules:
                _logger.warning("Formules API returned an empty formules map (%s)", url)
                return None

            formules = {
                str(k).strip(): _normalise_formule(str(v))
                for k, v in raw_formules.items()
            }
            _logger.debug(
                "Formules API: fetched %d formules from %s", len(formules), url
            )
            return formules

        except json.JSONDecodeError as exc:
            _logger.warning("Formules API returned invalid JSON (%s): %s", url, exc)
        except OSError as exc:
            # Covers URLError, socket.timeout, ConnectionRefusedError, etc.
            _logger.warning(
                "Formules API unreachable (%s): %s — using local fallback", url, exc
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Formules API unexpected error (%s): %s", url, exc, exc_info=True
            )
        return None

    def _fetch_formules_from_local(self):
        """Return the formules map from the local ma.fiscal.setting table.

        Handles both common return shapes:
          • list of (code, formule) tuples  →  _get_formules() returns a list
          • dict {code: formule}            →  _get_formules() returns a dict
        """
        try:
            result = self.env['ma.fiscal.setting']._get_formules()
        except Exception as exc:  # noqa: BLE001
            _logger.error("Cannot read local ma.fiscal.setting formules: %s", exc)
            return {}

        if isinstance(result, dict):
            return {
                str(k).strip(): _normalise_formule(str(v or '0'))
                for k, v in result.items()
            }

        # Assume iterable of (code, formule) pairs
        try:
            return {
                str(k).strip(): _normalise_formule(str(v or '0'))
                for k, v in result
            }
        except (TypeError, ValueError) as exc:
            _logger.error(
                "ma.fiscal.setting._get_formules() returned an unexpected format: %s", exc
            )
            return {}

    # ------------------------------------------------------------------ #
    # Public: get the resolved formules map (API → local fallback)
    # ------------------------------------------------------------------ #
    def _get_fiscal_year(self, options):
        """The fiscal year that selects which per-client formules snapshot
        to use: the year of the report's own selected period end date
        (``options['date']['date_to']``), the same for *every* column of
        this render regardless of its ``date_scope`` — including the N-1
        comparison column, which is computed with the active filter year's
        formulas, not year-1's (explicit product decision: formulas are
        versioned by the year the report is being filed for, not by the
        year each individual column happens to represent)."""
        date_to = options['date']['date_to']
        return int(str(date_to)[:4])

    def _get_formules_map(self, year):
        """Return the {code: formule} map used to compute the report, for
        this client's snapshot of `year`.

        Resolution order
        ----------------
        1. In-process cache (if still fresh — default TTL: 10 s).
        2. Remote API (http://app.ozirpaie.ma:2024/api/ma/formules or the URL
           stored in the ``ma_formules.api_url`` system parameter), called
           with ``client_id`` (``ma_formules.client_id`` system parameter)
           and ``year`` as query parameters so the instance returns this
           client's formulas for that specific fiscal year.
           • On success the result is cached and returned.
           • On ANY failure (network, bad JSON, empty response …) we fall
             through silently to step 3.
        3. Local ``ma.fiscal.setting`` table (CSV-imported rows).
           This is used transparently whenever the API is unreachable and its
           data are NOT cached, so the report keeps working even offline.
           NOTE: this fallback is a single global snapshot with no year
           dimension — it cannot be year-accurate, it's a last resort only.

        Cache TTL
        ---------
        Keep the TTL short (default 10 s) so that formula changes on the remote
        instance are reflected quickly.  Raise it via the system parameter
        ``ma_formules.api_cache_ttl`` only if the remote API is slow or you
        deliberately want a longer refresh window. Each `year` gets its own
        independent cache entry (the cache key is the full URL, including
        the client_id/year query string).

        To force an immediate refresh (e.g. after updating formulas), call::

            from odoo.addons.your_module.models.ma_fiscal_report import _invalidate_api_cache
            _invalidate_api_cache()   # clears all workers' caches on next restart
                                      # or use the "Vider le cache des formules" button
        """
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        client_id = icp.get_param('ma_formules.client_id')
        cache_ttl = _get_cache_ttl(icp)

        if client_id:
            query = urllib.parse.urlencode({'client_id': client_id, 'year': year})
            url = f"{base_url}?{query}"
        else:
            # No client_id configured yet (e.g. before the "Enregistrer ce
            # client" setup step has been run): fall back to the plain,
            # unscoped URL — same behaviour as before this feature existed.
            url = base_url

        # 1. Check process-level cache
        cached = _API_CACHE.get(url)
        if cached and cached['fetched_at'] + cache_ttl > time.time():
            _logger.debug("Formules: serving from cache (%s)", url)
            return cached['data']

        # 2. Try the remote API
        formules = self._fetch_formules_from_api(url)
        if formules:
            _API_CACHE[url] = {'fetched_at': time.time(), 'data': formules, 'ttl': cache_ttl}
            return formules

        # 3. Fallback: local table
        # If we have a stale cache entry (API was reachable before but is now
        # temporarily down), prefer it over the local table — it's more recent.
        if cached and cached.get('data'):
            _logger.warning(
                "Formules API down; serving STALE cache (age %.0fs) for %s",
                time.time() - cached['fetched_at'], url,
            )
            return cached['data']

        _logger.warning(
            "Formules API unavailable and no cache — falling back to local ma.fiscal.setting"
        )
        return self._fetch_formules_from_local()

    def _register_client_with_formules_api(self, company_name):
        """One-time call to the formules instance's registration route:
        creates (or, if already registered, just returns) a client_id for
        `company_name`, and triggers that instance's own cloning of the
        master formulas into a full per-year snapshot (2020 -> current
        year) for this client. Returns the client_id as a string.

        Raises UserError on any failure (missing secret, network error, the
        instance rejecting the request, ...) so the caller (the settings
        screen's button) can surface it directly to the user.
        """
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('ma_formules.api_url', _DEFAULT_API_URL) or _DEFAULT_API_URL
        secret = icp.get_param('ma_formules.register_secret')
        if not secret:
            raise UserError(_(
                "Le paramètre système 'ma_formules.register_secret' n'est pas configuré "
                "(Paramètres > Rapports fiscaux)."
            ))

        parsed = urllib.parse.urlparse(base_url)
        register_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, '/api/ma/clients/register', '', '', '')
        )

        req = urllib.request.Request(
            register_url,
            data=json.dumps({'name': company_name}).encode('utf-8'),
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Register-Secret': secret,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise UserError(_(
                "L'instance des formules a refusé l'enregistrement (HTTP %s) : %s"
            ) % (exc.code, body)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise UserError(_(
                "Impossible de contacter l'instance des formules pour l'enregistrement : %s"
            ) % exc) from exc

        client_id = data.get('client_id')
        if not client_id:
            raise UserError(_("Réponse inattendue de l'instance des formules : %s") % data)
        return str(client_id)

    def action_clear_formules_cache(self):
        """Button action: wipe the in-process formules cache for this worker.

        Add a button on the report form view that calls this action so that you
        can force a fresh fetch from the API without restarting Odoo:

            <button name="action_clear_formules_cache" type="object"
                    string="Vider le cache des formules" class="btn-secondary"/>
        """
        _invalidate_api_cache()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Cache vidé"),
                'message': _("Le cache des formules a été vidé. La prochaine génération du rapport "
                             "interrogera l'API distante."),
                'sticky': False,
                'type': 'success',
            },
        }

    # ------------------------------------------------------------------ #
    # Audit domain
    # ------------------------------------------------------------------ #
    def _get_expression_audit_aml_domain(self, expression_to_audit, options):
        if (expression_to_audit.engine == 'custom'
                and expression_to_audit.formula == '_report_custom_engine_ma_fiscal'):
            subformula = (expression_to_audit.subformula or '').strip()
            match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
            if match:
                formules = self._get_formules_map(self._get_fiscal_year(options))
                code = match.group(1)
                try:
                    ast = parse_formule(formules.get(code, ''))
                except ValueError:
                    return None
                prefixes = collect_account_prefixes(ast, formules, code)
                if prefixes:
                    return Domain.OR([
                        [('account_id.code', '=like', f"{prefix}%")]
                        for prefix in prefixes
                    ])
            return None
        return super()._get_expression_audit_aml_domain(expression_to_audit, options)

    # ------------------------------------------------------------------ #
    # Static line rendering — bold (not grey) on ESG computed/total rows
    # ------------------------------------------------------------------ #
    # The E.S.G report needs three visual levels: table-separator titles
    # (grey background, level 0), computed/total rows (plain bold, like the
    # actif report's section headers) and detail rows (regular).
    # The framework bolds a static line only if it is a level-0 header
    # (which also paints a grey background) or an unfoldable group header.
    # A flat bold line is therefore expressed through the standard
    # ``o_bold_tr`` CSS class (same mechanism as the bank reconciliation
    # report), injected here for the explicit list of total-line codes.
    _BOLD_TOTAL_CODES = frozenset({
        'ESG_TFR_MARGE_BRUTE',
        'ESG_TFR_PRODUCTION',
        'ESG_TFR_CONSOMMATION',
        'ESG_TFR_VALEUR_AJOUTEE',
        'ESG_TFR_EBE',
        'ESG_TFR_RESULTAT_EXPLOITATION',
        'ESG_TFR_RESULTAT_FINANCIER',
        'ESG_TFR_RESULTAT_COURANT',
        'ESG_TFR_RESULTAT_NON_COURANT',
        'ESG_TFR_RESULTAT_NET',
        'ESG_CAF_CAF',
        'ESG_CAF_AUTOFINANCEMENT',
        'AMORT_NON_VALEURS',
        'AMORT_INCORPORELLES',
        'AMORT_CORPORELLES',
        'AMORT_TOTAL',
        'TVA15_A_FACTUREE',
        'TVA15_B_RECUPERABLE',
        'TVA15_C_DUE_CREDIT',
        'AFFECT_TOTAL_A',
        'AFFECT_TOTAL_B',
        'FUSION_TOTAL',
        'STOCK_SECTION_APPRO',
        'STOCK_SECTION_ENCQ',
        'STOCK_SECTION_PRODUITS_FINIS',
        'STOCK_SECTION_RESIDUELS',
        'STOCK_TOTAL_APPRO',
        'STOCK_TOTAL_ENCQ',
        'STOCK_TOTAL_PRODUITS_FINIS',
        'STOCK_TOTAL_RESIDUELS',
        'STOCK_TOTAL_GENERAL',
        'DEVISES_TOTAL_ENTREES',
        'DEVISES_TOTAL_SORTIES',
        'DEVISES_BALANCE',
        'DEVISES_TOTAL',
        'FIN_SYNTH_3',
        'FIN_SYNTH_6',
        'FIN_SYNTH_7',
        'FIN_EMPL_TOTAL_1',
        'FIN_EMPL_TOTAL_2',
        'FIN_EMPL_TOTAL_GENERAL',
        'IS_AUTRES_PRODUITS',
        'IS_AUTRES_PRODUITS_TOTAL',
        'IS_DENOMINATEUR',
        'IS_MONTANT_DU',
    })

    # Deficit/loss EDI codes whose cell text must always render red,
    # regardless of the value's sign (text color only, via Bootstrap's
    # text-danger utility class — see _build_column_dict below).
    _ALWAYS_RED_EDI_CODES = frozenset({
        '820',   # Perte nette (bilan_fisc)
        '840',   # Déficit brut fiscal si T2 > T1 (B) (bilan_fisc)
        '6875',  # ou déficit net fiscal (B) (bilan_fisc)
    })

    # ---- Dynamic Saisie detection (derives the editable set from the live
    #      formules map so that formula changes are picked up immediately) ----

    def _get_saisie_codes(self, options):
        """Return a frozenset of EDI codes whose current formule is a manual
        entry marker: ``"Saisie"``, or ``"Somme"`` (used by the legacy system
        for a few rows - e.g. "CA soumis au taux réduit" in the IS report -
        that have no deeper formula either, just a manually-entered total).

        The result is cached in *options* (a fresh dict per render) so the
        formules map is read at most once per report load.
        """
        codes = options.get('ma_saisie_codes')
        if codes is None:
            formules = self._get_formules_map(self._get_fiscal_year(options))
            codes = frozenset(
                c for c, f in formules.items()
                if f.strip().strip('"').strip().lower() in ('saisie', 'somme')
            )
            options['ma_saisie_codes'] = codes
        return codes

    def _get_lines(self, options, all_column_groups_expression_totals=None, warnings=None):
        """Prime the Saisie-codes cache so every sub-call can use it without
        repeating the formules-map lookup."""
        self._get_saisie_codes(options)
        return super()._get_lines(
            options,
            all_column_groups_expression_totals=all_column_groups_expression_totals,
            warnings=warnings,
        )

    def _get_static_line_dict(self, options, line, all_column_groups_expression_totals, parent_id=None):
        rslt = super()._get_static_line_dict(
            options, line, all_column_groups_expression_totals, parent_id=parent_id,
        )
        code = line.code or ''
        if code in self._BOLD_TOTAL_CODES or (code.startswith('DETAIL_CPC_') and code.endswith('_TOTAL')):
            rslt['class'] = 'o_bold_tr'
        return rslt

    def _build_column_dict(
        self, col_value, col_data, options=None, currency=False, digits=1,
        column_expression=None, has_sublines=False, report_line_id=None,
    ):
        # Highlight every cell whose EDI code is currently a manual ("Saisie")
        # entry point; the web client gets the pencil via edit_popup_data.
        rslt = super()._build_column_dict(
            col_value, col_data, options=options, currency=currency, digits=digits,
            column_expression=column_expression, has_sublines=has_sublines,
            report_line_id=report_line_id,
        )
        if column_expression:
            subformula = column_expression.subformula or ''
            match = re.fullmatch(r'\s*-?\s*edi[_ ](\S+)\s*', subformula)
            if match:
                code = match.group(1)
                classes = []
                saisie_codes = self._get_saisie_codes(options or {})
                if code in saisie_codes:
                    classes.append('ma-editable-cell')
                if code in self._ALWAYS_RED_EDI_CODES:
                    classes.append('text-danger')
                if classes:
                    rslt['class'] = ' '.join(classes)
        return rslt

    def _build_static_line_columns(self, line, options, all_column_groups_expression_totals, groupby_model=None):
        # The framework only creates edit_popup_data (the pencil button) for
        # engine='external' expressions in the XML.  Our expressions are all
        # 'custom'; instead of hardcoding which columns are editable we derive
        # it here from the current formules map, so changing a formule to or
        # from "Saisie" is picked up without touching the XML.
        columns = super()._build_static_line_columns(
            line, options, all_column_groups_expression_totals, groupby_model=groupby_model,
        )
        if len(options.get('companies', [])) == 1:
            saisie_codes = self._get_saisie_codes(options)
            line_expr_map = {expr.label: expr for expr in line.expression_ids}
            for col_dict, col_options in zip(columns, options['columns']):
                column_expression = line_expr_map.get(col_options['expression_label'])
                if not column_expression:
                    continue
                subformula = column_expression.subformula or ''
                match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
                if not match or match.group(1) not in saisie_codes:
                    continue
                figure_type = column_expression.figure_type or col_options.get('figure_type', 'monetary')
                rounding = None
                if figure_type == 'integer':
                    rounding = 0
                elif figure_type == 'monetary':
                    rounding = self.env.company.currency_id.decimal_places
                col_dict['edit_popup_data'] = json.dumps({
                    'column_group_key': col_options['column_group_key'],
                    'target_expression_id': column_expression.id,
                    'rounding': rounding,
                    'figure_type': figure_type,
                })
        return columns

    def action_modify_manual_value(self, line_id, options, column_group_key, new_value_str, target_expression_id, rounding, json_friendly_column_group_totals):
        """Same contract as the stock account_reports method, but:
        * the recompute set also includes the 'custom' engine expressions: our
          EDI totals reference the manually-edited "Saisie" codes in their
          formules, so they must refresh right after a manual value is saved;
        * clearing the input of a "Saisie" cell deletes the saved value instead
          of writing 0, so the cell goes back to its blank "not entered" state.
        """
        self.ensure_one()

        target_column_group_options = self._get_column_group_options(options, column_group_key)
        self._init_currency_table(target_column_group_options)

        target_expression = self.env['account.report.expression'].browse(target_expression_id)
        subformula = target_expression.subformula or ''
        match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
        is_ma_saisie = bool(match) and match.group(1) in self._get_saisie_codes(target_column_group_options)

        if target_column_group_options.get('compute_budget'):
            expressions_to_recompute = self.env['account.report.expression'].browse(target_expression_id) \
                                       | self.line_ids.expression_ids.filtered(lambda x: x.engine == 'aggregation')
            self._action_modify_manual_budget_value(line_id, target_column_group_options, new_value_str, target_expression_id, rounding)
        elif is_ma_saisie and new_value_str == '':
            # Clear the manual value: remove every saved amount of the target
            # expression within the date bounds, for the current company.
            if len(target_column_group_options['companies']) > 1:
                raise UserError(_("Editing a manual report line is not allowed when multiple companies are selected."))
            date_from, date_to = self._get_date_bounds_info(target_column_group_options, target_expression.date_scope)
            clear_domain = [
                ('target_report_expression_id', '=', target_expression.id),
                ('company_id', '=', self.env.company.id),
                ('date', '<=', date_to),
            ]
            if date_from:
                clear_domain.append(('date', '>=', date_from))
            self.env['account.report.external.value'].search(clear_domain).unlink()
            expressions_to_recompute = self.line_ids.expression_ids.filtered(
                lambda x: x.engine in ('external', 'custom', 'aggregation'))
        else:
            expressions_to_recompute = self.line_ids.expression_ids.filtered(
                lambda x: x.engine in ('external', 'custom', 'aggregation'))
            self._action_modify_manual_external_value(target_column_group_options, new_value_str, target_expression_id, rounding)

        all_column_groups_expression_totals = self._convert_json_friendly_column_group_totals(
            json_friendly_column_group_totals,
            expressions_to_exclude=expressions_to_recompute,
        )
        recomputed_expression_totals = self._compute_expression_totals_for_each_column_group(
            expressions_to_recompute, options, forced_all_column_groups_expression_totals=all_column_groups_expression_totals)

        return {
            'lines': self._get_lines(options, all_column_groups_expression_totals=recomputed_expression_totals),
            'column_groups_totals': self._get_json_friendly_column_group_totals(recomputed_expression_totals),
        }


# --------------------------------------------------------------------------- #
# Custom report handler
# --------------------------------------------------------------------------- #
class AccountReportHandlerMaFiscal(models.AbstractModel):
    _name = 'account.report.handler.ma_fiscal'
    _inherit = 'account.report.custom.handler'
    _description = 'Moroccan Bilan Report Custom Handler'

    def _report_custom_engine_ma_fiscal(
        self, expressions, options, date_scope,
        current_groupby, next_groupby,
        offset=0, limit=None, warnings=None,
    ):
        report = self.env['account.report'].browse(options['report_id'])
        evaluator = MaFiscalEvaluator(report, self.env, options, date_scope)
        results = {'has_sublines': False}

        for expression in expressions:
            subformula = expression.subformula or ''
            match = re.fullmatch(r'\s*edi[_ ](\S+)\s*', subformula)
            if not match:
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=expression.display_name, sub=subformula,
                ))
            code = match.group(1)
            if evaluator.is_saisie_code(code) and not evaluator.has_manual_value(code):
                # Not entered yet → blank cell (instead of showing 0).
                results[subformula] = None
            else:
                results[subformula] = evaluator.evaluate_code(code)

        return results

    def _report_custom_engine_ma_amort_cumul_fin(
        self, expressions, options, date_scope,
        current_groupby, next_groupby,
        offset=0, limit=None, warnings=None,
    ):
        """"Cumul d'amortissement fin exercice" = cumul début + dotations -
        amortissements sortis, each evaluated with ITS OWN date_scope (read
        from that line's own cumul_debut / dotations / amort_sorties cells -
        see below), instead of one shared scope for the whole formula.

        A plain ``EDI('debut_code')+EDI('dot_code')-EDI('sorties_code')`` formula run
        through ``_report_custom_engine_ma_fiscal`` can't do this: that engine uses a
        single date_scope for the whole formula tree, so the nested EDI(debut_code)
        would get re-evaluated at from_beginning too - silently becoming the
        period-END balance (which already nets in this year's sorties), on top of
        the formula's own explicit "-sorties" term, i.e. sorties gets counted twice
        (see bilan_amort_report.xml for the bug this fixes).

        Using Odoo's native 'aggregation' engine to sum the sibling cells instead
        isn't safe either: an unfilled "Saisie" (manual-entry) cell like dotations
        is deliberately None (blank) as a *cell* value, and 'aggregation' can't do
        arithmetic on None. evaluate_code() below always resolves a Saisie code to
        a real float (0.0 default), same as every other nested EDI() reference,
        avoiding that trap.

        subformula format: "cf_<debut_code>_<dotations_code>_<sorties_code>"
        (plain EDI codes, no 'edi_' prefix, underscore-separated).

        It MUST be a valid Python identifier: the framework extracts each
        expression's value out of this function's returned dict by calling
        ``safe_eval(expression.subformula, result_dict)`` - i.e. it evaluates
        the subformula *as a Python expression* using the returned dict as
        the namespace. That's how "edi_1236" works elsewhere (a bare name
        lookup). A comma-separated subformula like "1236,1237,1238" is not a
        name - Python parses it as the *tuple literal* (1236, 1237, 1238),
        ignoring the returned dict entirely and returning that tuple of raw
        EDI-code numbers as the "value", which then crashes trying to format
        it as a monetary amount.
        Each term's date_scope is read from that SAME line's own cumul_debut /
        dotations / amort_sorties expressions (not hardcoded here), so this
        function can never drift out of sync with whatever scope those cells
        are configured with in the XML - exactly what happened when Dotations
        was switched to strict_range but this function still hardcoded
        from_beginning for it, producing a cumul_fin that no longer matched
        debut + dotations - sorties as displayed on screen.
        """
        report = self.env['account.report'].browse(options['report_id'])
        evaluators_by_scope = {}

        def get_evaluator(scope):
            if scope not in evaluators_by_scope:
                evaluators_by_scope[scope] = MaFiscalEvaluator(report, self.env, options, scope)
            return evaluators_by_scope[scope]

        results = {'has_sublines': False}

        for expression in expressions:
            subformula = expression.subformula or ''
            match = re.fullmatch(r'\s*cf_(\d+)_(\d+)_(\d+)\s*', subformula)
            if not match:
                raise UserError(_(
                    "Invalid subformula on expression %(expr)s: %(sub)s",
                    expr=expression.display_name, sub=subformula,
                ))
            debut_code, dotations_code, sorties_code = match.groups()

            siblings = {s.label: s for s in expression.report_line_id.expression_ids}
            debut_scope = siblings['cumul_debut'].date_scope if 'cumul_debut' in siblings else 'to_beginning_of_period'
            dotations_scope = siblings['dotations'].date_scope if 'dotations' in siblings else 'from_beginning'
            sorties_scope = siblings['amort_sorties'].date_scope if 'amort_sorties' in siblings else 'from_beginning'

            results[subformula] = (
                get_evaluator(debut_scope).evaluate_code(debut_code)
                + get_evaluator(dotations_scope).evaluate_code(dotations_code)
                - get_evaluator(sorties_scope).evaluate_code(sorties_code)
            )

        return results


# --------------------------------------------------------------------------- #
# Evaluator
# --------------------------------------------------------------------------- #
class MaFiscalEvaluator:

    # Reports where every EDI code whose OWN formula directly calls SE(...)
    # or ASE(...) must display as an absolute (always-positive) value,
    # regardless of the account's natural debit/credit sign. Requested
    # specifically for these two reports (not module-wide): "Tableau des
    # Provisions" and "Tableau des Amortissements" both have columns (Cumul
    # début, Reprises, ...) that are plain account-balance snapshots whose
    # sign flips depending on how the live formula happens to be written
    # (SE vs -SE / ASE vs -ASE), instead of being deliberately signed.
    _ABS_SE_ASE_REPORT_XMLIDS = (
        'dbc_liasse_fiscale.bilan_prov_report',
        'dbc_liasse_fiscale.bilan_amort_report',
    )

    def __init__(self, report, env, options, date_scope):
        self.report = report
        self.env = env
        self.options = options
        self.date_scope = date_scope
        self.abs_se_ase = any(
            report == env.ref(xmlid, raise_if_not_found=False)
            for xmlid in self._ABS_SE_ASE_REPORT_XMLIDS
        )
        # Formules map is fetched ONCE per evaluator instance (= once per
        # report render), so all EDI codes on the same report page use the
        # exact same snapshot of the formules. The fiscal year is derived
        # from the report's own selected period (not from `date_scope`), so
        # an N-1 comparison column still uses the active filter year's
        # formulas — see AccountReport_MaFiscal._get_fiscal_year.
        self.formules = report._get_formules_map(report._get_fiscal_year(options))
        self._code_cache = {}       # memo: code → float (value from evaluate_code)
        self._manual_cache = {}     # memo: code → float (sum of saved manual values)
        self._manual_exists = {}    # memo: code → bool (any saved value in scope?)
        self._prefix_sums = {}      # memo: prefix → (debit, credit)

        # Map every expression whose subformula is ``edi_<code>`` (with or
        # without the "editable " prefix) to the EDI code, so that
        # _manual_value / has_manual_value can resolve the DB ids of
        # account.report.external.value to search.
        code_to_expressions = {}
        for expression in report.line_ids.expression_ids:
            # (\w+) not (\d+): some EDI codes are alphanumeric (e.g. "ET_18A"
            # in the IS report), not just numeric ids like "1704".
            m = re.fullmatch(r'(?:editable\s+)?edi[_ ](\w+)', expression.subformula or '')
            if m:
                code_to_expressions.setdefault(m.group(1), set()).add(expression.id)
        self._code_to_expressions = {code: list(ids) for code, ids in code_to_expressions.items()}

    # ------------------------------------------------ Saisie helpers

    def is_saisie_code(self, code):
        """True if the code's current formule is a manual entry marker:
        the literal ``"Saisie"`` or ``"Somme"`` (see _get_saisie_codes)."""
        return self.formules.get(code, '').strip().strip('"').strip().lower() in ('saisie', 'somme')

    def _manual_domain(self, expression_ids):
        """Domain that scopes account.report.external.value to the current
        date range + companies of this evaluator instance."""
        date_from, date_to = self.report._get_date_bounds_info(self.options, self.date_scope)
        domain = [
            ('target_report_expression_id', 'in', expression_ids),
            ('date', '<=', date_to),
            ('company_id', 'in', self.report.get_report_company_ids(self.options)),
        ]
        if date_from:
            domain.append(('date', '>=', date_from))
        return domain

    def has_manual_value(self, code):
        """True if at least one manual value record exists for *code* in the
        current period/company scope (used to decide blank vs 0 display)."""
        if code in self._manual_exists:
            return self._manual_exists[code]
        expression_ids = self._code_to_expressions.get(code)
        if not expression_ids:
            self._manual_exists[code] = False
            return False
        domain = self._manual_domain(expression_ids)
        exists = bool(self.env['account.report.external.value'].search_count(domain))
        self._manual_exists[code] = exists
        return exists

    def _manual_value(self, code):
        """Sum of the saved manual amounts (account.report.external.value)
        stored for the expression(s) bound to *code*, within the date bounds
        and companies of the current evaluator scope.  Returns 0 when nothing
        was entered yet (arithmetic-safe default).
        """
        if code in self._manual_cache:
            return self._manual_cache[code]
        expression_ids = self._code_to_expressions.get(code)
        if not expression_ids:
            self._manual_cache[code] = 0.0
            return 0.0
        domain = self._manual_domain(expression_ids)
        value = sum(self.env['account.report.external.value'].search(domain).mapped('value') or [0.0])
        self._manual_cache[code] = value
        return value

    # ------------------------------------------------------------------ EDI
    def evaluate_code(self, code, stack=()):
        code = str(code).strip()
        if code in self._code_cache:
            return self._code_cache[code]
        if code in stack:
            raise UserError(_(
                "Circular EDI reference detected: %(chain)s → %(code)s",
                chain=' → '.join(stack), code=code,
            ))
        raw_formule = self.formules.get(code, '')
        if not raw_formule:
            # Code not found in either source → treat as zero, log once
            _logger.warning("EDI code %r not found in formules map — returning 0", code)
            self._code_cache[code] = 0.0
            return 0.0

        # Manually entered ("Saisie" or "Somme") codes resolve to the saved
        # manual value (account.report.external.value), not to a formule.
        if raw_formule.strip().strip('"').strip().lower() in ('saisie', 'somme'):
            value = self._manual_value(code)
            self._code_cache[code] = value
            return value

        try:
            ast = parse_formule(raw_formule)
        except ValueError as exc:
            raise UserError(_(
                "Cannot parse formule of EDI code %(code)s (%(formule)r): %(error)s",
                code=code, formule=raw_formule, error=exc,
            ))

        value = self._eval_node(ast, stack + (code,))
        if (
            self.abs_se_ase
            and re.search(r"A?SE\(", raw_formule)
            and not raw_formule.strip().startswith('-')
        ):
            # Take the abs of the fully-combined value (not of each SE/ASE
            # term before combining them), so a formula like
            # SE('613')+SE('614')-SE('6148') still nets its terms correctly
            # before the sign gets normalized. A formula written with an
            # explicit leading '-' (e.g. -ASE('2834')) is left alone: that
            # minus is the formula author's deliberate sign choice, not
            # convention drift - only a *bare* SE/ASE (no leading '-') gets
            # auto-corrected to positive.
            value = abs(value)
        self._code_cache[code] = value
        return value

    def _eval_node(self, node, stack):
        kind = node[0]
        if kind == 'const':
            return node[1]
        if kind == 'neg':
            return -self._eval_node(node[1], stack)
        if kind == 'cmp':
            value = self._eval_node(node[1], stack)
            return value if value > 0 else 0.0
        if kind == 'bin':
            left = self._eval_node(node[2], stack)
            right = self._eval_node(node[3], stack)
            op = node[1]
            if op == '+':
                return left + right
            if op == '-':
                return left - right
            if op == '*':
                return left * right
            if right == 0:
                raise UserError(_("Division by zero while evaluating an EDI formule"))
            return left / right
        if kind == 'edi':
            return self.evaluate_code(node[1], stack)
        if kind == 'se':
            debit, credit = self._get_prefix_sums(node[1])
            # # SE('xxx') → absolute balance (always positive)
            # return abs(debit - credit)
            # SE('xxx') → signed balance; -SE('xxx') → its negation
            return (debit - credit) * node[2]
        if kind == 'ase':
            debit, credit = self._get_prefix_sums(node[1])
            # ASE('xxx') → signed balance
            return debit - credit
        if kind == 'deb':
            debit, _credit = self._get_prefix_sums(node[1])
            # DEB('xxx') → total DEBIT only (never nets in the credit side);
            # -DEB('xxx') → its negation.
            return debit * node[2]
        if kind == 'cre':
            _debit, credit = self._get_prefix_sums(node[1])
            # CRE('xxx') → total CREDIT only (never nets in the debit side);
            # -CRE('xxx') → its negation.
            return credit * node[2]
        raise UserError(_("Unknown formule node kind: %s", kind))

    # ----------------------------------------------- account prefix totals
    def _get_prefix_sums(self, prefix):
        """Return (total_debit, total_credit) for all journal items posted on
        accounts whose code starts with *prefix*, within the date scope of this
        evaluator.  Results are memoised for the duration of the render.
        """
        if prefix in self._prefix_sums:
            return self._prefix_sums[prefix]

        if not re.fullmatch(r'[0-9A-Za-z]+', prefix):
            raise UserError(_("Invalid account code prefix: %r") % prefix)

        query = self.report._get_report_query(
            self.options,
            self.date_scope,
            domain=[('account_id.code', '=like', f"{prefix}%")],
        )
        self.env.cr.execute(SQL(
            """
                SELECT COALESCE(SUM(%(debit)s), 0.0),
                       COALESCE(SUM(%(credit)s), 0.0)
                  FROM %(table_references)s
                       %(currency_table_join)s
                 WHERE %(search_condition)s
            """,
            debit=self.report._currency_table_apply_rate(SQL("account_move_line.debit")),
            credit=self.report._currency_table_apply_rate(SQL("account_move_line.credit")),
            table_references=query.from_clause,
            currency_table_join=self.report._currency_table_aml_join(self.options),
            search_condition=query.where_clause,
        ))
        debit, credit = self.env.cr.fetchone()
        result = (debit or 0.0, credit or 0.0)
        self._prefix_sums[prefix] = result
        return result