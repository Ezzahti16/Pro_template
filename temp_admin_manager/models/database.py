import json
import logging
import shlex
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .instance import ACCESS_MODELS

_logger = logging.getLogger(__name__)

MARKER = "TAM_RESULT:"

# Runs inside `odoo shell` on the TARGET instance, which may be Odoo 14 to 19, so
# it stays on Python 3.6 syntax and discovers every version-specific name at
# runtime. odoo shell rolls back on exit, so every write commits explicitly.
# Fed as a single exec() line to avoid the REPL's blank-line rules.
SCRIPT = r'''
import datetime
import json
import odoo
import time
import uuid
def _tam_transient(message):
    """Would retrying this plausibly succeed? PostgreSQL says so for these."""
    low = (message or "").lower()
    return ("could not serialize access" in low or "concurrent update" in low
            or "deadlock detected" in low)
def _tam_groups_field(Users):
    # Odoo 19 renamed res.users.groups_id to group_ids.
    return "group_ids" if "group_ids" in Users._fields else "groups_id"
def _tam_flush(env):
    # Python constraints fire on flush, which moved from the model to the
    # environment in Odoo 17. Absent either name the writes are already
    # validated, so doing nothing is correct rather than fatal.
    if hasattr(env, "flush_all"):
        env.flush_all()
    elif hasattr(env["res.users"], "flush"):
        env["res.users"].flush()
def _tam_invalidate(env):
    # After a savepoint rollback the cache still holds what was undone.
    if hasattr(env, "invalidate_all"):
        env.invalidate_all()
    elif hasattr(env["res.users"], "invalidate_cache"):
        env["res.users"].invalidate_cache()
def _tam_group_label(group):
    for fname in ("full_name", "display_name"):
        if fname in group._fields:
            return group[fname]
    return group.name
PUBLISHER_CRON = "mail.ir_cron_module_update_notification"
def _tam_cron_field(env):
    # Odoo 17 moved the cron's name onto ir.actions.server and left a stored
    # mirror behind; before that it was a column on ir.cron itself.
    return "cron_name" if "cron_name" in env["ir.cron"]._fields else "name"
def _tam_now(env):
    """The client's own clock, as naive UTC, which is what ir.cron is stored in."""
    return odoo.fields.Datetime.now()
def _tam_publisher(env):
    """When the client next reports itself to Odoo.

    That report counts the database's users, so a temporary admin still active
    when it fires is counted with them. Read-only: this cron belongs to the
    client and is never written to.
    """
    cron = env.ref(PUBLISHER_CRON, raise_if_not_found=False)
    if not cron:
        return {"found": False, "active": False, "seconds": 0.0}
    cron = cron.with_context(active_test=False)
    info = {"found": True, "active": bool(cron.active),
            "nextcall": cron.nextcall and str(cron.nextcall) or "",
            "lastcall": cron.lastcall and str(cron.lastcall) or ""}
    # Measured as a distance from the client's clock, never as a timestamp to be
    # compared against ours: the two machines need not agree on the time.
    info["seconds"] = ((cron.nextcall - _tam_now(env)).total_seconds()
                       if cron.nextcall else 0.0)
    return info
def _tam_guard_name(params):
    return "TAM: retire temporary admin (%s)" % params["login"]
GUARD_CODE = """
user = env["res.users"].with_context(active_test=False).search(
    [("id", "=", %(uid)d), ("login", "=", %(login)s)], limit=1)
if user:
    # Archived, not deleted: it leaves every user count the publisher
    # notification reports, it cannot fail on res_users_log the way unlinking
    # does, and the manager still deletes it properly afterwards.
    user.write({"active": False, "password": %(password)s})
"""
def _tam_arm_guard(env, params, user, seconds):
    """Arm a one-shot cron inside the client that retires the user on time.

    The manager deletes the user itself; this only covers the manager being
    unreachable when the moment comes. It lives in the client's own scheduler,
    so if that scheduler is not running then neither is the notification it
    guards against.
    """
    Cron = env["ir.cron"]
    root = env.ref("base.user_root")
    vals = {
        "name": _tam_guard_name(params),
        "model_id": env.ref("base.model_res_users").id,
        "state": "code",
        "user_id": root.id,
        "active": True,
        # Ahead of the notification's 1000, for the case where a stopped client
        # comes back up with both of them already due.
        "priority": 0,
        "interval_number": 1,
        "interval_type": "days",
        "nextcall": _tam_now(env) + datetime.timedelta(seconds=seconds),
    }
    # Up to Odoo 16 a one-shot cron is expressed with numbercall; it is gone in 17.
    if "numbercall" in Cron._fields:
        vals["numbercall"] = 1
    if "doall" in Cron._fields:
        vals["doall"] = False
    vals["code"] = GUARD_CODE % {
        "uid": user.id, "login": json.dumps(params["login"]),
        "password": json.dumps(uuid.uuid4().hex + "Aa1!")}
    cron = Cron.create(vals)
    if hasattr(cron, "_trigger"):
        # A trigger fires the job once at that moment and is consumed with the
        # run, so pushing nextcall out of reach leaves a cron that cannot come
        # round again. It cannot switch itself off instead: from Odoo 19 a cron
        # may not be written to while it is the one running.
        cron.write({"nextcall": vals["nextcall"] + datetime.timedelta(days=3650)})
        cron._trigger(at=vals["nextcall"])
    _tam_flush(env)
    return cron.id
def _tam_disarm_guard(env, params, cron_id):
    """Remove the guard cron, by id or by the name it was given."""
    Cron = env["ir.cron"].with_context(active_test=False)
    cron = Cron.browse(cron_id).exists() if cron_id else Cron.browse()
    if not cron:
        cron = Cron.search([(_tam_cron_field(env), "=", _tam_guard_name(params))])
    if not cron:
        return ""
    try:
        with env.cr.savepoint():
            cron.unlink()
            _tam_flush(env)
        return "removed"
    except Exception:
        _tam_invalidate(env)
    try:
        with env.cr.savepoint():
            cron.write({"active": False})
            _tam_flush(env)
        return "deactivated"
    except Exception:
        _tam_invalidate(env)
    return "left behind"
def _tam_add_groups(env, user, field, groups):
    """Add as many of `groups` as this database accepts; return the refusals.

    All at once first, then by halves. A single group some module refuses then
    costs only itself, instead of costing the user every other group.
    """
    if not groups:
        return groups
    try:
        with env.cr.savepoint():
            user.write({field: [(4, gid) for gid in groups.ids]})
            _tam_flush(env)
        return groups.browse()
    except Exception:
        _tam_invalidate(env)
    if len(groups) == 1:
        return groups
    half = len(groups) // 2
    return (_tam_add_groups(env, user, field, groups[:half])
            | _tam_add_groups(env, user, field, groups[half:]))
def _tam_user_label(user):
    return "%s (#%d, %s)" % (user.name or "?", user.id, user.login or "?")
def _tam_granted(env, user, field, wanted, refused, label):
    """What the user ended up with, in one line for the Access Granted field."""
    if refused:
        groups = "%d of %d groups" % (len(user[field]), len(wanted))
    else:
        groups = "%d groups" % len(wanted)
    total = env["res.company"].sudo().search_count([])
    mine = len(user.company_ids)
    companies = "%d of %d companies" % (mine, total) if total > 1 else "1 company"
    text = "%s: %s, %s" % (label, groups, companies)
    if refused:
        names = sorted(_tam_group_label(g) for g in refused)
        text += ". Refused by this database: %s" % ", ".join(names)[:300]
    return text
def _tam_user_types(env):
    """Internal / portal / public, which res.users keeps mutually exclusive.

    res.users enforces exactly one of the three, so a set holding more than one
    raises. Callers drop these and put internal back.
    """
    Groups = env["res.groups"]
    drop = Groups.browse()
    for xmlid in ("base.group_portal", "base.group_public"):
        grp = env.ref(xmlid, raise_if_not_found=False)
        if grp:
            drop |= grp
    # Up to Odoo 18 the user types share a category; 19 dropped category_id.
    if "category_id" in Groups._fields:
        cat = env.ref("base.module_category_user_type", raise_if_not_found=False)
        if cat:
            drop |= Groups.search([("category_id", "=", cat.id)])
    return drop
def _tam_every_group(env):
    """Every group on the database except the user types."""
    Groups = env["res.groups"]
    return (Groups.search([]) - _tam_user_types(env)) | env.ref("base.group_user")
def _tam_source_user(env, params, field):
    """The user whose access is to be copied, and how it was chosen.

    Keyed on base.group_system, not on a name or a fixed id: client databases
    rename, archive and renumber their administrator, but that group is what
    actually makes one. uid 1 is excluded, since the superuser bypasses access
    checks in code and holds barely any groups to copy.
    """
    Users = env["res.users"].with_context(active_test=False)
    wanted = params.get("source_user_id") or 0
    if params.get("access_model") == "user":
        if not wanted:
            raise ValueError("No user id was given to copy access from.")
        source = Users.browse(wanted).exists()
        if not source:
            raise ValueError("No user with id %d exists on this database." % wanted)
        return source, "requested"
    system = env.ref("base.group_system", raise_if_not_found=False)
    if not system:
        return Users.browse(), "no settings group"
    candidates = Users.search([("active", "=", True), ("id", "!=", 1),
                               ("login", "!=", params["login"]),
                               (field, "in", system.ids)])
    candidates = candidates.filtered(lambda u: not u.share)
    if not candidates:
        return Users.browse(), "none found"
    stock = env.ref("base.user_admin", raise_if_not_found=False)
    if stock and stock in candidates:
        return stock, "stock administrator"
    best = sorted(candidates, key=lambda u: (-len(u[field]), u.id))[0]
    return best, "most groups"
def _tam_max_access(env):
    """Every group and every company: the escape hatch, so the widest there is."""
    companies = env["res.company"].sudo().search([], order="id")
    vals = {}
    if companies:
        vals["company_id"] = companies[0].id
        vals["company_ids"] = [(6, 0, companies.ids)]
    return _tam_every_group(env), vals
def _tam_access_plan(env, params, field):
    """(groups, values, label, cloned_from) for the configured access model.

    Copying the administrator is the default: it is what the client's own admin
    sees, where every group would also switch on features they never enabled.
    """
    internal = env.ref("base.group_user")
    if (params.get("access_model") or "admin") == "every":
        groups, vals = _tam_max_access(env)
        return groups, vals, "every group", ""
    source, how = _tam_source_user(env, params, field)
    if not source:
        # Over-granting beats handing over a user who cannot open Settings,
        # but the record has to say that is what happened.
        groups, vals = _tam_max_access(env)
        return (groups, vals,
                "every group (no administrator to copy: %s)" % how, "")
    # Verbatim, so the clone really is the same: Odoo 19's administrator does
    # not hold base.group_user at all, and adding it would widen the copy.
    groups = source[field] - _tam_user_types(env)
    if source.share:
        # Copying a portal or public user would make the temp admin one too.
        groups |= internal
    if params.get("include_technical"):
        tech = env.ref("base.group_no_one", raise_if_not_found=False)
        if tech:
            groups |= tech
    vals = {}
    if source.company_id:
        vals["company_id"] = source.company_id.id
    if source.company_ids:
        vals["company_ids"] = [(6, 0, source.company_ids.ids)]
    if params.get("copy_language"):
        for fname in ("lang", "tz"):
            if fname in source._fields and source[fname]:
                vals[fname] = source[fname]
    label = _tam_user_label(source)
    return groups, vals, "same as %s" % label, label
def _tam_apply(env, user, field, groups):
    """Make the user hold exactly `groups`; return what was refused."""
    try:
        with env.cr.savepoint():
            user.write({field: [(6, 0, groups.ids)]})
            _tam_flush(env)
        return groups.browse()
    except Exception:
        _tam_invalidate(env)
    # Back to a floor that is certainly valid, then add the rest in halves.
    # Only groups that are wanted anyway, so narrowing cannot overshoot.
    floor = env.ref("base.group_user") & groups
    settings = env.ref("base.group_system", raise_if_not_found=False)
    if settings and settings in groups:
        floor |= settings
    if floor:
        try:
            with env.cr.savepoint():
                user.write({field: [(6, 0, floor.ids)]})
                _tam_flush(env)
        except Exception:
            _tam_invalidate(env)
    return _tam_add_groups(env, user, field, groups - user[field])
def _tam_deadline(env, params):
    """How long the temporary admin may live here: (seconds, publisher, verdict).

    "full" is the configured lifetime, "short" stops before the notification,
    and "too_late" means there is no usable life left before it.
    """
    full = float(params.get("duration_hours") or 6) * 3600.0
    if not params.get("guard"):
        return full, {"found": False, "active": False, "guarded": False}, "full"
    pub = _tam_publisher(env)
    pub["guarded"] = True
    if not pub["found"] or not pub["active"]:
        return full, pub, "full"
    deadline = pub["seconds"] - float(params.get("guard_margin") or 15) * 60.0
    if deadline >= full:
        # It fires after this user would have expired anyway: nothing to do, and
        # nothing is written to the client.
        return full, pub, "full"
    if deadline < float(params.get("guard_min_life") or 10) * 60.0:
        return deadline, pub, "too_late"
    return deadline, pub, "short"
def _tam(env, params):
    login = params["login"]
    Users = env["res.users"].with_context(active_test=False, no_reset_password=True)
    found = Users.search([("login", "=", login)], limit=1)
    action = params["action"]
    if action == "create":
        # Never touch a user this module did not create.
        if found:
            return {"status": "exists", "id": found.id, "active": found.active}
        # Before anything is created, so a refusal leaves the database as it was.
        life, pub, verdict = _tam_deadline(env, params)
        if verdict == "too_late":
            return {"status": "too_late", "publisher": pub}
        field = _tam_groups_field(Users)
        groups, extra, label, cloned_from = _tam_access_plan(env, params, field)
        # Born as a plain internal user, then given the planned access: one code
        # path for creating and for re-applying, and nothing half-granted.
        vals = {
            "name": params["name"],
            "login": login,
            "email": login,
            "password": params["password"],
            field: [(6, 0, env.ref("base.group_user").ids)],
        }
        vals.update(extra)
        try:
            with env.cr.savepoint():
                user = Users.create(dict(vals))
                _tam_flush(env)
        except Exception:
            # The copied company scope or language was refused; keep identity.
            _tam_invalidate(env)
            for key in ("company_ids", "company_id", "lang", "tz"):
                vals.pop(key, None)
            user = Users.create(dict(vals))
            _tam_flush(env)
        refused = _tam_apply(env, user, field, groups)
        guard_id, guard_error = 0, ""
        if verdict == "short" and params.get("arm_guard"):
            try:
                with env.cr.savepoint():
                    guard_id = _tam_arm_guard(env, params, user, life)
            except Exception as e:
                # The manager still deletes on time; only the second guard is
                # missing, and the record has to say so.
                _tam_invalidate(env)
                guard_error = str(e)[:300]
        env.cr.commit()
        return {"status": "created", "id": user.id, "cloned_from": cloned_from,
                "granted": _tam_granted(env, user, field, groups, refused, label),
                "life_seconds": life, "publisher": pub,
                "guard_cron_id": guard_id, "guard_error": guard_error}
    if action == "grant":
        # Re-apply the access model to the user already there, without
        # deleting and recreating it.
        target = Users.search([("id", "=", params["user_id"]),
                               ("login", "=", login)], limit=1)
        if not target:
            return {"status": "gone"}
        field = _tam_groups_field(Users)
        groups, extra, label, cloned_from = _tam_access_plan(env, params, field)
        if extra:
            try:
                with env.cr.savepoint():
                    target.write(dict(extra))
                    _tam_flush(env)
            except Exception:
                _tam_invalidate(env)
        refused = _tam_apply(env, target, field, groups)
        env.cr.commit()
        return {"status": "granted", "id": target.id, "cloned_from": cloned_from,
                "granted": _tam_granted(env, target, field, groups, refused, label)}
    if action == "delete":
        target = Users.search([("id", "=", params["user_id"]), ("login", "=", login)], limit=1)
        # The guard goes first either way: it exists only to retire this user,
        # and leaving it behind would archive whoever later holds that id.
        guard = _tam_disarm_guard(env, params, params.get("guard_cron_id") or 0)
        env.cr.commit()
        if not target:
            return {"status": "gone", "guard": guard}
        reason = ""
        for attempt in range(3):
            try:
                target.unlink()
                env.cr.commit()
                return {"status": "deleted", "attempts": attempt + 1, "guard": guard}
            except Exception as e:
                # A serialization failure needs the whole transaction rolled back
                # for a fresh snapshot; a savepoint would keep failing. It happens
                # when the temp user is logged in, since Odoo writes its login log.
                env.cr.rollback()
                _tam_invalidate(env)
                reason = str(e)
                if not _tam_transient(reason):
                    break
                time.sleep(1 + attempt)
        # Retire it instead, one step per savepoint so that a step the database
        # refuses does not cost the others. A random password rather than an
        # empty one: res.users hashes whatever it is given, and False raises.
        field = _tam_groups_field(Users)
        steps = [("password", {"password": uuid.uuid4().hex + "Aa1!"}),
                 ("archived", {"active": False}),
                 ("groups", {field: [(5, 0, 0)]})]
        revoked = []
        for label, vals in steps:
            try:
                with env.cr.savepoint():
                    target.write(dict(vals))
                    _tam_flush(env)
                revoked.append(label)
            except Exception:
                _tam_invalidate(env)
        env.cr.commit()
        return {"status": "archived", "revoked": revoked, "reason": reason[:300],
                "guard": guard}
    if action == "check":
        # The notification travels with every check, so a refresh re-tightens a
        # deadline that has moved since the user was created.
        _life, pub, _verdict = _tam_deadline(env, params)
        if not found:
            return {"status": "absent", "publisher": pub}
        return {"status": "present", "id": found.id, "active": found.active,
                "publisher": pub}
    return {"status": "error", "error": "unknown action"}
try:
    print("TAM_RESULT:" + json.dumps(_tam(env, __tam_params)))
except Exception as e:
    print("TAM_RESULT:" + json.dumps({"status": "error", "error": str(e)}))
'''


class TamDatabase(models.Model):
    _name = "tam.database"
    _description = "Odoo database with a temporary admin"
    _order = "instance_id, name"

    _name_uniq = models.Constraint(
        "UNIQUE(instance_id, name)",
        "This database is already registered for that instance.",
    )

    instance_id = fields.Many2one("tam.instance", required=True, ondelete="cascade", index=True)
    server_id = fields.Many2one(related="instance_id.server_id", store=True, index=True)
    name = fields.Char(string="Database", required=True)
    url = fields.Char(compute="_compute_url", string="URL")

    # Not required: a stored computed column is written after the INSERT, so a
    # NOT NULL on it would reject every create. The compute fills it at once.
    access_model = fields.Selection(
        ACCESS_MODELS, string="Access Model",
        compute="_compute_access_model", store=True, readonly=False,
        help="Taken from the instance, and changeable here for this database alone. "
             "Changing it on the instance resets this.")
    source_user_id = fields.Integer(
        string="Copy User ID",
        help="Only for 'Copy a specific user': the res.users ID on the client "
             "database whose access should be copied.")
    cloned_from = fields.Char(
        string="Cloned From", readonly=True, copy=False,
        help="The user whose access was copied, as found on the client database.")

    temp_login = fields.Char(string="Temp Admin Email", required=True, default="dbc@gmail.com")
    temp_password = fields.Char(string="Temp Admin Password", required=True, default="Y@s123")
    temp_name = fields.Char(string="Temp Admin Name", default="Temporary Admin")
    duration_hours = fields.Integer(string="Lifetime (hours)", default=6, required=True)

    state = fields.Selection(
        [("not_created", "Not created"),
         ("active", "Active"),
         ("deleted", "Deleted"),
         ("conflict", "Already exists")],
        default="not_created", required=True, readonly=True, copy=False, index=True)
    remote_user_id = fields.Integer(string="Remote User ID", readonly=True, copy=False)
    granted_access = fields.Char(readonly=True, copy=False, string="Access Granted")
    created_at = fields.Datetime(readonly=True, copy=False)
    expires_at = fields.Datetime(readonly=True, copy=False)
    deleted_at = fields.Datetime(readonly=True, copy=False)

    publisher_state = fields.Char(
        string="Publisher Notification", readonly=True, copy=False,
        help="When this database next reports itself to Odoo. That report counts "
             "the users, so the temporary admin is deleted before it runs.")
    publisher_nextcall = fields.Datetime(
        string="Notification At", readonly=True, copy=False,
        help="As read from the client database, in UTC.")
    shortened = fields.Boolean(
        string="Cut Short", readonly=True, copy=False,
        help="The lifetime was reduced to stay ahead of the publisher notification.")
    guard_cron_id = fields.Integer(
        string="Guard Cron ID", readonly=True, copy=False,
        help="The one-shot scheduled action armed on the client database as a "
             "second guard, removed when the temporary admin is deleted.")
    last_error = fields.Text(readonly=True, copy=False)

    @api.depends("instance_id.access_model")
    def _compute_access_model(self):
        for rec in self:
            rec.access_model = rec.instance_id.access_model or "admin"

    @api.depends("instance_id.base_url", "name")
    def _compute_url(self):
        for rec in self:
            base = (rec.instance_id.base_url or "").strip().rstrip("/")
            if base and "://" not in base:
                # A bare "host:1029" is not a usable link: an explicit port means a
                # direct Odoo port, anything else is most likely behind TLS.
                host = base.split("/")[0]
                base = "%s%s" % ("http://" if ":" in host else "https://", base)
            rec.url = "%s/web?db=%s" % (base, rec.name) if base else False

    def _display_url(self):
        return self.url or "%s / %s" % (self.instance_id.display_name, self.name)

    # ------------------------------------------------------------------
    # odoo shell
    # ------------------------------------------------------------------
    def _shell(self, params, timeout=300):
        """Run SCRIPT inside this database's odoo shell and return its result dict."""
        self.ensure_one()
        inst = self.instance_id
        if not inst.odoo_service:
            raise UserError(_("Set the Odoo service on %s first (or run Detect Services).",
                              inst.display_name))
        # The params travel as a JSON string and are decoded on the far side:
        # JSON is not Python, so a boolean would arrive as the undefined name
        # `true`. It also keeps the line pure ASCII, whatever the container's
        # locale, since json.dumps escapes everything else.
        stdin = "__tam_params = __import__('json').loads(%s)\nexec(%s)\n" % (
            json.dumps(json.dumps(params)), json.dumps(SCRIPT))

        # Try as configured, then once more with explicit database settings: an
        # instance with no mounted odoo.conf gets none, because `exec` skips the
        # image entrypoint that would have supplied them.
        attempts = [inst.pass_db_args]
        if not inst.pass_db_args:
            attempts.append(True)

        last = ("", "")
        for with_db_args in attempts:
            db_args = inst._shell_db_args() if with_db_args else ""
            args = "exec -T %s odoo shell -d %s %s %s" % (
                shlex.quote(inst.odoo_service), shlex.quote(self.name),
                inst.odoo_shell_args or "", db_args)
            _rc, out, err = inst._compose(args.rstrip(), stdin_data=stdin,
                                          timeout=timeout, check=False)
            for line in (out + "\n" + err).splitlines():
                line = line.strip()
                if line.startswith(MARKER):
                    if with_db_args and not inst.pass_db_args:
                        inst.pass_db_args = True
                    return json.loads(line[len(MARKER):])
            last = (out, err)
            if not self._is_db_connection_failure(out + err):
                break

        out, err = last
        raise UserError(_(
            "No result from odoo shell on %(db)s. Output:\n%(out)s\n%(err)s",
            db=self.name, out=out[-2000:], err=err[-2000:]))

    @staticmethod
    def _is_db_connection_failure(text):
        """Did odoo shell fail to reach PostgreSQL, rather than fail at our script?"""
        low = (text or "").lower()
        return any(sig in low for sig in (
            "operationalerror", "could not connect to server", "connection refused",
            "could not translate host name", ".s.pgsql", "no such file or directory",
            "password authentication failed"))

    def _params(self, action):
        inst = self.instance_id
        return {
            "action": action,
            "login": self.temp_login,
            "password": self.temp_password,
            "name": self.temp_name or "Temporary Admin",
            "user_id": self.remote_user_id or 0,
            "access_model": self.access_model or inst.access_model or "admin",
            "source_user_id": self.source_user_id or 0,
            "copy_language": inst.copy_admin_language,
            "include_technical": inst.include_technical,
            "duration_hours": self.duration_hours or 6,
            "guard": inst.guard_publisher_cron,
            "guard_margin": inst.guard_margin,
            "guard_min_life": inst.guard_min_life,
            "arm_guard": inst.arm_client_guard,
            "guard_cron_id": self.guard_cron_id or 0,
        }

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_create_temp_user(self):
        conflicts = []
        for rec in self:
            if rec.state == "active":
                raise UserError(_("A temporary admin is already active on %s.",
                                  rec._display_url()))
            res = rec._run("create")
            if res["status"] == "too_late":
                raise UserError(rec._too_late_message(res.get("publisher") or {}))
            if res["status"] == "exists":
                # The login is taken by a user this module did not create: leave it alone.
                rec.write({
                    "state": "conflict",
                    "remote_user_id": 0,
                    "last_error": _(
                        "A user with the login %(login)s already exists on %(db)s "
                        "(ID %(id)s) and was not created by this module, so it was left "
                        "untouched. Use a different email, or remove that user manually.",
                        login=rec.temp_login, db=rec.name, id=res["id"]),
                })
                conflicts.append(rec.name)
                continue
            now = fields.Datetime.now()
            full = (rec.duration_hours or 6) * 3600.0
            # The client measured this against its own clock, so it is used as a
            # distance from now rather than as a time of day.
            life = res.get("life_seconds") or full
            vals = {
                "state": "active",
                "remote_user_id": res["id"],
                "granted_access": res.get("granted") or False,
                "cloned_from": res.get("cloned_from") or False,
                "created_at": now,
                "expires_at": now + timedelta(seconds=life),
                "deleted_at": False,
                "last_error": False,
                "guard_cron_id": res.get("guard_cron_id") or 0,
            }
            vals.update(rec._publisher_vals(res.get("publisher") or {}, life < full))
            rec.write(vals)
            notes = []
            if res.get("guard_error"):
                notes.append(_(
                    "The temporary admin was created and will be deleted on time by "
                    "this manager, but the second guard could not be armed on the "
                    "client database:\n%s", res["guard_error"]))
            if rec.instance_id.restart_after_create:
                problem = rec.instance_id.restart_odoo()
                if problem:
                    notes.append(_("The user was created, but the Odoo service "
                                   "could not be restarted:\n%s", problem))
            if notes:
                rec.last_error = "\n\n".join(notes)
        if conflicts:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "warning",
                    "title": _("User already exists"),
                    "message": _("Nothing was created on: %s. The existing user was left "
                                 "untouched.", ", ".join(conflicts)),
                    "sticky": True,
                },
            }
        return True

    # ------------------------------------------------------------------
    # The client's publisher notification
    # ------------------------------------------------------------------
    @staticmethod
    def _spell_delay(seconds):
        """A rough distance, for a message a human reads once."""
        seconds = int(abs(seconds))
        if seconds >= 86400:
            return _("%s days", round(seconds / 86400.0, 1))
        if seconds >= 3600:
            return _("%s hours", round(seconds / 3600.0, 1))
        return _("%s minutes", max(1, int(round(seconds / 60.0))))

    def _publisher_vals(self, pub, shortened):
        """The three fields that say what was found on the client, for the form."""
        if not pub.get("guarded"):
            state = _("not checked")
        elif not pub.get("found"):
            state = _("no publisher cron on this database")
        elif not pub.get("active"):
            state = _("the publisher cron is switched off")
        else:
            seconds = pub.get("seconds") or 0.0
            state = (_("runs %(at)s UTC, in %(delay)s", at=pub.get("nextcall"),
                       delay=self._spell_delay(seconds)) if seconds >= 0 else
                     _("overdue since %(at)s UTC, by %(delay)s", at=pub.get("nextcall"),
                       delay=self._spell_delay(seconds)))
        return {
            "publisher_state": state,
            "publisher_nextcall": pub.get("nextcall") or False,
            "shortened": bool(shortened),
        }

    def _too_late_message(self, pub):
        seconds = pub.get("seconds") or 0.0
        if seconds < 0:
            when = _(
                "Its publisher notification was due at %(at)s UTC and has not run "
                "for %(delay)s, so it may fire at any moment.",
                at=pub.get("nextcall"), delay=self._spell_delay(seconds))
            if not pub.get("lastcall"):
                when += _(" It has never run on this database, which usually means "
                          "the client's scheduler is not running.")
        else:
            when = _("Its publisher notification runs at %(at)s UTC, in %(delay)s.",
                     at=pub.get("nextcall"), delay=self._spell_delay(seconds))
        return _(
            "No temporary admin was created on %(db)s.\n\n%(when)s\n\nThat report "
            "counts the users of the database, so the temporary admin has to be gone "
            "%(margin)s minutes before it, which leaves too little time to be useful. "
            "Create it once the notification has run, or untick \"Stay ahead of the "
            "publisher notification\" on %(inst)s to create it anyway.",
            db=self.name, when=when, margin=self.instance_id.guard_margin,
            inst=self.instance_id.display_name)

    def action_delete_temp_user(self):
        for rec in self:
            rec._delete_temp_user()
        return True

    def _delete_temp_user(self):
        self.ensure_one()
        if not self.remote_user_id:
            raise UserError(_("No temporary admin is tracked on %s.", self._display_url()))
        res = self._run("delete")
        notes = []
        if res["status"] == "archived":
            notes.append(_(
                "The user owned records and could not be deleted, so it was retired "
                "instead (%(revoked)s). It can no longer log in.\n\nWhat refused the "
                "deletion:\n%(reason)s",
                revoked=", ".join(res.get("revoked") or []) or _("nothing could be revoked"),
                reason=res.get("reason") or _("unknown")))

        # Restart last: the user is already gone, so a failed restart is a note,
        # not a failure of the deletion itself.
        if self.instance_id.restart_after_delete:
            problem = self.instance_id.restart_odoo()
            if problem:
                notes.append(_("The Odoo service could not be restarted:\n%s", problem))

        if res.get("guard") == "left behind":
            notes.append(_(
                "The guard scheduled action could not be removed from %s. Remove it "
                "by hand: it is named after this login under Scheduled Actions.",
                self.name))

        self.write({
            "state": "deleted",
            "granted_access": False,
            "cloned_from": False,
            "guard_cron_id": 0,
            "shortened": False,
            "deleted_at": fields.Datetime.now(),
            "last_error": "\n\n".join(notes) if notes else False,
        })

    def action_regrant_access(self):
        """Re-apply the access model to the temp admin already there.

        Odoo has no single group that covers everything, so access is the sum of
        the groups held. Switching the access model and pressing this is the way
        to widen or narrow a user without deleting and recreating it.
        """
        for rec in self:
            if rec.state != "active" or not rec.remote_user_id:
                raise UserError(_("No temporary admin is active on %s.",
                                  rec._display_url()))
            res = rec._run("grant")
            if res["status"] == "gone":
                rec.write({"state": "deleted", "granted_access": False,
                           "cloned_from": False,
                           "deleted_at": fields.Datetime.now()})
                raise UserError(_("The temporary admin no longer exists on %s.", rec.name))
            rec.write({"granted_access": res.get("granted") or False,
                       "cloned_from": res.get("cloned_from") or False})
        return True

    def action_refresh_state(self):
        for rec in self:
            res = rec._run("check")
            if res["status"] == "absent":
                if rec.state == "active":
                    rec.write({"state": "deleted", "deleted_at": fields.Datetime.now()})
                elif rec.state == "conflict":
                    rec.write({"state": "not_created", "last_error": False})
            elif rec.state == "not_created" or (
                    rec.state == "active" and res["id"] != rec.remote_user_id):
                # Present but not the user we track.
                rec.write({"state": "conflict", "last_error": _(
                    "A user with the login %(login)s exists on %(db)s (ID %(id)s) and was "
                    "not created by this module.", login=rec.temp_login, db=rec.name,
                    id=res["id"])})
            rec._retime(res.get("publisher") or {})
        return True

    def _retime(self, pub):
        """Move the deadline to wherever the notification is now.

        The client's cron can be rescheduled, run early or be switched off between
        two refreshes, so the expiry follows it — upwards only as far as the full
        lifetime this database was created with.
        """
        self.ensure_one()
        if self.state != "active" or not self.created_at:
            self.write(self._publisher_vals(pub, self.shortened))
            return
        ceiling = self.created_at + timedelta(hours=self.duration_hours or 6)
        expiry = ceiling
        if pub.get("guarded") and pub.get("found") and pub.get("active"):
            margin = (self.instance_id.guard_margin or 15) * 60.0
            deadline = fields.Datetime.now() + timedelta(
                seconds=(pub.get("seconds") or 0.0) - margin)
            expiry = min(ceiling, deadline)
        vals = self._publisher_vals(pub, expiry < ceiling)
        vals["expires_at"] = expiry
        self.write(vals)

    def _run(self, action):
        """_shell, with every failure turned into a UserError.

        Nothing is written here: raising rolls the transaction back anyway, so the
        message goes to the dialog instead. The cron catches these and stores them.
        """
        self.ensure_one()
        try:
            res = self._shell(self._params(action))
        except UserError:
            raise
        except Exception as e:
            _logger.exception("Temp admin %s failed on %s/%s", action,
                              self.instance_id.path, self.name)
            raise UserError(_("Temp admin %(action)s failed on %(db)s:\n%(err)s",
                              action=action, db=self.name, err=e))
        if res.get("status") == "error":
            raise UserError(_("Odoo shell error on %(db)s:\n%(err)s",
                              db=self.name, err=res.get("error")))
        return res

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_cleanup_expired(self):
        expired = self.search([("state", "=", "active"),
                               ("expires_at", "<=", fields.Datetime.now())])
        for rec in expired:
            try:
                rec._delete_temp_user()
                self.env.cr.commit()
            except Exception as e:
                self.env.cr.rollback()
                _logger.error("Temp admin cleanup failed on %s/%s: %s",
                              rec.instance_id.path, rec.name, e)
                rec.write({"last_error": str(e)})
                self.env.cr.commit()
