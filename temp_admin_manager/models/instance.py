import logging
import re
import shlex

import yaml

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

COMPOSE_FILES = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]

# Identification works off the compose *images* first, which is far more reliable
# than service names, and falls back to name fragments when an image is custom.
ODOO_IMAGES = ("odoo",)
DB_IMAGES = ("postgres", "postgresql", "timescale", "pgvector", "citus")
ODOO_HINTS = ("odoo", "erp", "web", "app")
DB_HINTS = ("db", "postgres", "pgsql", "psql", "database")

# Environment variables that may carry the PostgreSQL role, best first.
PG_USER_VARS = ("POSTGRES_USER", "PGUSER", "POSTGRESQL_USERNAME", "DB_USER", "USER")
PG_DB_VARS = ("POSTGRES_DB", "POSTGRESQL_DATABASE", "PGDATABASE")
PG_PASSWORD_VARS = ("POSTGRES_PASSWORD", "PGPASSWORD", "POSTGRESQL_PASSWORD",
                    "DB_PASSWORD", "PASSWORD")

SYSTEM_DATABASES = ("postgres", "template0", "template1")

# Emitted by the scan script before each instance's `compose config` output.
SCAN_MARKER = "===TAM_INSTANCE "

# Where the real YAML starts, so warnings compose prints first can be skipped.
CONFIG_START = re.compile(r"^(?:name|services|version):", re.M)

# How much access the temporary admin gets. Copying the client's own
# administrator is the default: every group would also switch on features they
# never enabled, so the instance would not look the way their admin sees it.
ACCESS_MODELS = [
    ("admin", "Same as administrator"),
    ("every", "Every group"),
    ("user", "Copy a specific user"),
]


class TamInstance(models.Model):
    _name = "tam.instance"
    _description = "Dockerized Odoo instance"
    _order = "server_id, name"

    _path_uniq = models.Constraint(
        "UNIQUE(server_id, path)",
        "This instance path is already registered for that server.",
    )

    server_id = fields.Many2one("tam.server", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True, help="Instance directory name.")
    path = fields.Char(required=True, help="Absolute path of the instance directory.")
    compose_file = fields.Char(default="docker-compose.yml")

    odoo_service = fields.Char(string="Odoo Service", help="Compose service running Odoo.")
    db_service = fields.Char(string="DB Service", help="Compose service running PostgreSQL.")
    pg_user = fields.Char(string="PostgreSQL User", default="odoo",
                          help="Read from POSTGRES_USER on the DB service when scanning.")
    pg_password = fields.Char(
        string="PostgreSQL Password",
        help="Only needed if psql inside the DB container asks for one. The official "
             "postgres image trusts local socket connections, so this is usually blank.")
    pg_maintenance_db = fields.Char(
        string="Maintenance Database", default="postgres",
        help="The database psql connects to in order to list the others. Without it "
             "psql would default to a database named after the user. Read from "
             "POSTGRES_DB when scanning.")
    odoo_shell_args = fields.Char(string="Extra Odoo Shell Args", default="--no-http")
    restart_after_delete = fields.Boolean(
        string="Restart after deletion", default=True,
        help="Restart the Odoo service once the temporary admin is removed, so the "
             "running server drops anything it had cached about that user.")
    pass_db_args = fields.Boolean(
        string="Pass DB args to odoo shell", copy=False,
        help="`docker compose exec` skips the image entrypoint, so an instance with "
             "no mounted odoo.conf gets no database settings. This passes them "
             "explicitly. Set automatically the first time a shell fails to connect.")
    restart_after_create = fields.Boolean(
        string="Restart after creation", default=False,
        help="Also restart right after creating the temporary admin. Off by default: "
             "it briefly interrupts the instance and is rarely needed.")
    base_url = fields.Char(string="Base URL", help="Public URL, shown for convenience.")

    access_model = fields.Selection(
        ACCESS_MODELS, string="Access Model", default="admin", required=True,
        help="What the temporary admin gets on every database of this instance. "
             "The administrator is found by its Settings group, not by name or id, "
             "so a renamed or renumbered one is still picked up. Each database can "
             "override this.")
    copy_admin_language = fields.Boolean(
        string="Copy language and timezone", default=True,
        help="Also take the language and timezone of the copied user, so the "
             "instance looks the way its own administrator sees it.")
    include_technical = fields.Boolean(
        string="Add technical features", default=False,
        help="Grant Technical Features on top of the copied access, for the "
             "developer menus the client's administrator usually does not have.")

    # The client's "Publisher: Update Notification" cron reports the database to
    # Odoo, users included — as counts, which a temporary admin still inflates.
    # The temp admin is therefore given a life that ends before it runs.
    guard_publisher_cron = fields.Boolean(
        string="Stay ahead of the publisher notification", default=True,
        help="Read the client's Publisher: Update Notification schedule and end the "
             "temporary admin before it runs, instead of after the full lifetime. "
             "That report counts the users of the database.")
    guard_margin = fields.Integer(
        string="Margin (minutes)", default=15,
        help="How long before the notification the temporary admin is deleted. "
             "Keep it above the five minutes of the cleanup schedule.")
    guard_min_life = fields.Integer(
        string="Shortest useful life (minutes)", default=10,
        help="If the notification leaves less than this, nothing is created and "
             "the message says when to try again.")
    arm_client_guard = fields.Boolean(
        string="Arm a guard on the client", default=True,
        help="Also create a one-shot scheduled action on the client database that "
             "retires the temporary admin at the same moment, so it happens even "
             "if this manager cannot be reached. It is removed on deletion.")

    odoo_image = fields.Char(readonly=True, copy=False,
                             help="Image of the detected Odoo service, so you can see "
                                  "which version this instance runs.")
    db_image = fields.Char(readonly=True, copy=False)
    detected_services = fields.Char(readonly=True, copy=False,
                                    string="All Compose Services")

    database_ids = fields.One2many("tam.database", "instance_id", string="Databases")
    database_count = fields.Integer(compute="_compute_database_count")
    active_temp_count = fields.Integer(compute="_compute_database_count",
                                       string="Active Temp Admins")
    last_error = fields.Text(readonly=True, copy=False)

    @api.depends("database_ids.state")
    def _compute_database_count(self):
        for rec in self:
            rec.database_count = len(rec.database_ids)
            rec.active_temp_count = len(rec.database_ids.filtered(lambda d: d.state == "active"))

    def _compose(self, args, **kw):
        self.ensure_one()
        file_arg = "-f %s " % shlex.quote(self.compose_file) if self.compose_file else ""
        return self.server_id._compose(self.path, file_arg + args, **kw)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @api.model
    def _scan_script(self, server):
        """One shell command that lists the instances *and* dumps each compose config.

        Doing it in a single SSH round-trip keeps a 20-instance server to one
        connection instead of twenty-one.
        """
        root = (server.instances_path or "").rstrip("/")
        names = " ".join(shlex.quote(f) for f in COMPOSE_FILES)
        prefix = "sudo " if server.use_sudo else ""
        # Plain `config` (YAML) is understood by both docker-compose v1 and
        # docker compose v2, and stderr is kept so a failure explains itself.
        return (
            'for d in %s/*/; do for f in %s; do if [ -f "$d$f" ]; then '
            'printf "\\n%s%%s|%%s===\\n" "${d%%/}" "$f"; '
            '(cd "$d" && %s%s -f "$f" config 2>&1); '
            'break; fi; done; done'
            % (shlex.quote(root), names, SCAN_MARKER, prefix, server.compose_cmd)
        )

    @api.model
    def _scan_server(self, server):
        """Create/refresh instance records for every compose project under instances_path."""
        if not (server.instances_path or "").strip():
            raise UserError(_("Set the instances path on %s first.", server.display_name))
        _rc, out, _err = server._run(self._scan_script(server), timeout=600, check=False)

        found = self.browse()
        for chunk in out.split(SCAN_MARKER)[1:]:
            header, _sep, config = chunk.partition("===")
            if "|" not in header:
                continue
            path, compose_file = header.rsplit("|", 1)
            path, compose_file = path.strip(), compose_file.strip()
            existing = self.search([("server_id", "=", server.id), ("path", "=", path)], limit=1)
            if existing:
                existing.compose_file = compose_file
                inst = existing
            else:
                inst = self.create({
                    "server_id": server.id,
                    "name": path.rstrip("/").rsplit("/", 1)[-1],
                    "path": path,
                    "compose_file": compose_file,
                })
            found |= inst
            inst._apply_compose_config(config)

        if not found:
            raise UserError(_(
                "No instance found under %(root)s on %(srv)s. Expected one sub-directory "
                "per instance, each containing one of: %(files)s.\n\n%(out)s",
                root=server.instances_path, srv=server.display_name,
                files=", ".join(COMPOSE_FILES), out=(out or "").strip()[:1000]))
        server.last_error = False
        return found

    # ------------------------------------------------------------------
    # Reading the compose config
    # ------------------------------------------------------------------
    @staticmethod
    def _image_repo(image):
        """'odoo:19.0-20260324' -> 'odoo'; 'bitnami/postgresql:16' -> 'postgresql'."""
        return (image or "").split("@")[0].rsplit(":", 1)[0].rsplit("/", 1)[-1].lower()

    @staticmethod
    def _service_env(service):
        """compose gives `environment` as a mapping or as a list of K=V strings."""
        env = service.get("environment")
        if isinstance(env, dict):
            return {k: v for k, v in env.items() if v is not None}
        if isinstance(env, list):
            pairs = (str(item).split("=", 1) for item in env)
            return {p[0]: p[1] for p in pairs if len(p) == 2}
        return {}

    @staticmethod
    def _published_port(service):
        """The host port of a service. v2 gives dicts, v1 gives "8071:8069" strings."""
        ports = service.get("ports") or []

        def target_of(port):
            if isinstance(port, dict):
                try:
                    return int(port.get("target") or 0)
                except (TypeError, ValueError):
                    return 0
            bits = str(port).split(":")
            try:
                return int(bits[-1].split("/")[0])
            except ValueError:
                return 0

        # Prefer Odoo's own port when the stack publishes several.
        for port in sorted(ports, key=lambda p: target_of(p) != 8069):
            if isinstance(port, dict):
                published = port.get("published")
            else:
                bits = str(port).split("/")[0].split(":")
                published = bits[-2] if len(bits) >= 2 else None
            if published:
                # Drop any bind address: "127.0.0.1:8071" -> "8071".
                return str(published).rsplit(":", 1)[-1]
        return False

    def _parse_compose_config(self, config):
        """YAML (or JSON, which YAML is a superset of) -> dict, or False."""
        text = (config or "").strip()
        if not text:
            return False
        candidates = [text]
        match = CONFIG_START.search(text)
        if match and match.start():
            # Skip warnings compose printed before the document.
            candidates.append(text[match.start():])
        for candidate in candidates:
            try:
                data = yaml.safe_load(candidate)
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and isinstance(data.get("services"), dict):
                return data
        return False

    def _apply_compose_config(self, config, force=False):
        """Fill the services, the PostgreSQL user and the images from compose config.

        Only blank fields are filled unless `force`, so manual corrections survive
        a re-scan.
        """
        self.ensure_one()
        data = self._parse_compose_config(config)
        if not data:
            # The output was not a compose document, so it is the reason it failed.
            detail = (config or "").strip()
            self.last_error = _(
                "Could not read the compose configuration of this instance.\n\n%(detail)s"
                "%(hint)s",
                detail=detail[:1500] or _("No output from the compose command."),
                hint=self.server_id._compose_hint(detail))
            return False

        services = data["services"]
        if not services:
            self.last_error = _("No compose services found in %s.", self.path)
            return False

        def by_image(repos, exclude=()):
            for name, svc in services.items():
                if name in exclude:
                    continue
                repo = self._image_repo(svc.get("image"))
                if any(r in repo for r in repos):
                    return name
            return False

        def by_name(hints, exclude=()):
            for hint in hints:
                for name in services:
                    if name not in exclude and hint in name.lower():
                        return name
            return False

        db = by_image(DB_IMAGES) or by_name(DB_HINTS)
        odoo = (by_image(ODOO_IMAGES, exclude=(db,) if db else ())
                or by_name(ODOO_HINTS, exclude=(db,) if db else ()))

        vals = {
            "detected_services": ", ".join(services),
            "odoo_image": (services.get(odoo) or {}).get("image") or False,
            "db_image": (services.get(db) or {}).get("image") or False,
            "last_error": False,
        }
        if odoo and (force or not self.odoo_service):
            vals["odoo_service"] = odoo
        if db and (force or not self.db_service):
            vals["db_service"] = db

        # The PostgreSQL role: from the DB service, which is authoritative, and
        # only then from the Odoo service, whose env may be a shared env_file.
        pg_user = False
        for svc_name in (db, odoo):
            env = self._service_env(services.get(svc_name) or {})
            for var in PG_USER_VARS:
                if env.get(var):
                    pg_user = str(env[var])
                    break
            if pg_user:
                break
        if pg_user and (force or not self.pg_user or self.pg_user == "odoo"):
            vals["pg_user"] = pg_user

        # The database psql should connect to in order to list the others.
        maintenance = False
        for svc_name in (db, odoo):
            env = self._service_env(services.get(svc_name) or {})
            for var in PG_DB_VARS:
                if env.get(var):
                    maintenance = str(env[var])
                    break
            if maintenance:
                break
        if maintenance and (force or not self.pg_maintenance_db
                            or self.pg_maintenance_db == "postgres"):
            vals["pg_maintenance_db"] = maintenance

        pg_password = False
        for svc_name in (db, odoo):
            env = self._service_env(services.get(svc_name) or {})
            for var in PG_PASSWORD_VARS:
                if env.get(var):
                    pg_password = str(env[var])
                    break
            if pg_password:
                break
        if pg_password and (force or not self.pg_password):
            vals["pg_password"] = pg_password

        # A starting point for the URL, from the Odoo service's published port.
        if odoo and not self.base_url:
            published = self._published_port(services.get(odoo) or {})
            if published:
                vals["base_url"] = "http://%s:%s" % (self.server_id.host, published)

        self.write(vals)
        return True

    def _detect_services(self, force=True):
        """Re-read one instance's compose config on its own."""
        self.ensure_one()
        _rc, out, err = self._compose("config", timeout=120, check=False)
        if self._apply_compose_config(out or err, force=force):
            return True
        raise UserError(self.last_error or
                        _("Could not read the compose configuration of %s.", self.path))

    def action_detect_services(self):
        for rec in self:
            rec._detect_services(force=True)
        return True

    def action_scan_databases(self):
        for rec in self:
            rec._scan_databases()
        return True

    def _scan_databases(self):
        """List the PostgreSQL databases of this instance."""
        self.ensure_one()
        if not self.db_service:
            raise UserError(_("Set the DB service on %s first (or run Detect Services).",
                              self.display_name))
        sql = ("SELECT datname FROM pg_database "
               "WHERE datistemplate = false AND datname <> 'postgres' ORDER BY datname")
        env = "-e PGPASSWORD=%s " % shlex.quote(self.pg_password) if self.pg_password else ""
        user = self.pg_user or "odoo"

        # psql without -d would connect to a database named after the user, which
        # usually does not exist. Try the detected one, then the usual suspects.
        # template1 is last: it always exists, so it is a dependable fallback.
        candidates = list(dict.fromkeys(
            db for db in (self.pg_maintenance_db, "postgres", user, "template1") if db))

        out = failure = None
        for candidate in candidates:
            args = "exec -T %s%s psql -U %s -d %s -At -c %s" % (
                env, shlex.quote(self.db_service), shlex.quote(user),
                shlex.quote(candidate), shlex.quote(sql))
            rc, out, err = self._compose(args, timeout=120, check=False)
            if rc == 0:
                if candidate != self.pg_maintenance_db:
                    self.pg_maintenance_db = candidate
                failure = None
                break
            failure = (err or out or "").strip()

        if failure is not None:
            # Not stored: raising rolls the write back, so the dialog carries it.
            raise UserError(_(
                "Could not list the databases of %(inst)s. Tried connecting through "
                "%(tried)s as %(user)s.\n\n%(err)s",
                inst=self.display_name, tried=", ".join(candidates), user=user,
                err=failure[:1500]))

        names = [n.strip() for n in out.splitlines()
                 if n.strip() and n.strip() not in SYSTEM_DATABASES]

        Database = self.env["tam.database"]
        for name in names:
            if not Database.search_count([("instance_id", "=", self.id), ("name", "=", name)]):
                Database.create({"instance_id": self.id, "name": name})
        self.last_error = False
        return self.database_ids

    def _shell_db_args(self):
        """Database settings for `odoo shell`, since exec skips the entrypoint.

        The DB service name is the hostname on the compose network, which is what
        a mounted odoo.conf would say too.
        """
        self.ensure_one()
        args = []
        if self.db_service:
            args.append("--db_host=%s" % shlex.quote(self.db_service))
        if self.pg_user:
            args.append("--db_user=%s" % shlex.quote(self.pg_user))
        if self.pg_password:
            args.append("--db_password=%s" % shlex.quote(self.pg_password))
        return " ".join(args)

    def restart_odoo(self):
        """Restart just the Odoo service. Returns '' or the reason it failed.

        Never raises: a restart that fails must not undo a deletion that worked.
        """
        self.ensure_one()
        if not self.odoo_service:
            return _("No Odoo service is set on %s.", self.display_name)
        try:
            rc, out, err = self._compose(
                "restart %s" % shlex.quote(self.odoo_service), timeout=300, check=False)
        except Exception as e:
            _logger.warning("Restart failed for %s: %s", self.path, e)
            return str(e)
        if rc != 0:
            return (err or out or "").strip()[:500]
        return ""

    def action_restart_odoo(self):
        failures = []
        for rec in self:
            problem = rec.restart_odoo()
            if problem:
                failures.append("%s: %s" % (rec.display_name, problem))
        if failures:
            raise UserError(_("Could not restart:\n%s", "\n".join(failures)))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "title": _("Restarted"),
                       "message": _("The Odoo service was restarted."), "sticky": False},
        }

    def action_open_databases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Databases"),
            "res_model": "tam.database",
            "view_mode": "list,form",
            "domain": [("instance_id", "=", self.id)],
            "context": {"default_instance_id": self.id},
        }
