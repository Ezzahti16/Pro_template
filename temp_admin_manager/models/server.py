import io
import logging
import shlex

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None

SSH_TIMEOUT = 20


class TamServer(models.Model):
    _name = "tam.server"
    _description = "Docker host running Odoo instances"
    _order = "name"

    name = fields.Char(required=True)
    host = fields.Char(required=True, help="Hostname or IP reachable over SSH.")
    ssh_port = fields.Integer(default=22, required=True)
    ssh_user = fields.Char(required=True, default="root")
    auth_type = fields.Selection(
        [("password", "Password"), ("key", "Private key")],
        default="key", required=True, string="Authentication")
    ssh_password = fields.Char(string="SSH Password")
    ssh_key = fields.Text(string="Private Key", help="PEM content of the private key.")
    ssh_key_passphrase = fields.Char(string="Key Passphrase")

    host_key = fields.Char(
        readonly=True, copy=False,
        help="Pinned SSH host key, stored on the first successful connection. "
             "Later connections are refused if the server presents a different key.")

    instances_path = fields.Char(
        string="Instances Path", required=True, default="/opt/odoo",
        help="Directory holding one sub-directory per instance, each with a compose file.")
    compose_cmd = fields.Char(
        string="Compose Command", default="docker compose", required=True,
        help="Use 'docker-compose' for the legacy standalone binary.")
    # Not named `sudo`: that would shadow BaseModel.sudo().
    use_sudo = fields.Boolean(string="Prefix with sudo",
                              help="Run docker through passwordless sudo.")

    instance_ids = fields.One2many("tam.instance", "server_id", string="Instances")
    instance_count = fields.Integer(compute="_compute_instance_count")
    last_error = fields.Text(readonly=True, copy=False)

    @api.depends("instance_ids")
    def _compute_instance_count(self):
        for rec in self:
            rec.instance_count = len(rec.instance_ids)

    # ------------------------------------------------------------------
    # SSH
    # ------------------------------------------------------------------
    def _host_key_name(self):
        """The name paramiko keys host entries by: bare host, or [host]:port."""
        self.ensure_one()
        port = self.ssh_port or 22
        return self.host if port == 22 else "[%s]:%d" % (self.host, port)

    def _connect(self):
        """Open an SSH client. The caller must close it."""
        self.ensure_one()
        if paramiko is None:
            raise UserError(_("The 'paramiko' Python package is required. "
                              "Install it with: pip install paramiko"))
        client = paramiko.SSHClient()
        if self.host_key:
            # Pinned on the first connection: refuse anything else. Paramiko looks
            # the entry up under "[host]:port" whenever the port is not 22, so the
            # name it is stored under has to match or the lookup silently misses.
            name = self._host_key_name()
            entry = paramiko.hostkeys.HostKeyEntry.from_line(
                "%s %s" % (name, self.host_key))
            client.get_host_keys().add(name, entry.key.get_name(), entry.key)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = {
            "hostname": self.host,
            "port": self.ssh_port or 22,
            "username": self.ssh_user,
            "timeout": SSH_TIMEOUT,
            "banner_timeout": SSH_TIMEOUT,
            "auth_timeout": SSH_TIMEOUT,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.auth_type == "password":
            if not self.ssh_password:
                raise UserError(_("No SSH password set on %s.", self.display_name))
            kwargs["password"] = self.ssh_password
        else:
            if not self.ssh_key:
                raise UserError(_("No private key set on %s.", self.display_name))
            last = None
            for loader in (getattr(paramiko, n, None) for n in
                           ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey")):
                if loader is None:
                    continue
                try:
                    kwargs["pkey"] = loader.from_private_key(
                        io.StringIO(self.ssh_key), password=self.ssh_key_passphrase or None)
                    last = None
                    break
                except Exception as e:
                    last = e
            if last is not None:
                raise UserError(_("Could not read the private key: %s", last))

        client.connect(**kwargs)

        if not self.host_key:
            remote = client.get_transport().get_remote_server_key()
            self.sudo().host_key = "%s %s" % (remote.get_name(), remote.get_base64())
        return client

    def _run(self, command, stdin_data=None, timeout=180, check=True):
        """Run a shell command on the host. Returns (rc, stdout, stderr)."""
        self.ensure_one()
        client = self._connect()
        try:
            chan = client.get_transport().open_session(timeout=SSH_TIMEOUT)
            chan.settimeout(timeout)
            chan.exec_command(command)
            if stdin_data:
                chan.sendall(stdin_data.encode())
            chan.shutdown_write()
            out, err = b"", b""
            while True:
                while chan.recv_ready():
                    out += chan.recv(65536)
                while chan.recv_stderr_ready():
                    err += chan.recv_stderr(65536)
                if chan.exit_status_ready() and not chan.recv_ready() \
                        and not chan.recv_stderr_ready():
                    break
            rc = chan.recv_exit_status()
        finally:
            client.close()
        out = out.decode(errors="replace")
        err = err.decode(errors="replace")
        if check and rc != 0:
            raise UserError(_("Command failed on %(srv)s (exit %(rc)s):\n%(cmd)s\n\n%(err)s",
                              srv=self.display_name, rc=rc, cmd=command, err=err or out))
        return rc, out, err

    def _compose_hint(self, output):
        """Turn a known compose failure into an instruction, or return ''."""
        self.ensure_one()
        text = (output or "").lower()
        v2_missing = ("unknown shorthand flag" in text
                      or "is not a docker command" in text
                      or "'compose' is not a docker command" in text)
        # Only suggest the v1 binary when it is not already what we are running.
        if v2_missing and "docker-compose" not in (self.compose_cmd or ""):
            return _("\n\nThis server has no 'docker compose' (v2) plugin. Set the Compose "
                     "Command on %(srv)s to 'docker-compose' and scan again.",
                     srv=self.display_name)
        if "command not found" in text or "not found" in text and "docker" in text:
            return _("\n\nThe command '%(cmd)s' was not found on %(srv)s. Check the Compose "
                     "Command field.", cmd=self.compose_cmd, srv=self.display_name)
        if "permission denied" in text and "docker" in text:
            return _("\n\n%(user)s cannot reach the docker daemon on %(srv)s. Tick "
                     "'Prefix with sudo', or add the user to the docker group.",
                     user=self.ssh_user, srv=self.display_name)
        return ""

    def _compose(self, workdir, args, **kw):
        """Run a compose command inside an instance directory."""
        prefix = "sudo " if self.use_sudo else ""
        cmd = "cd %s && %s%s %s" % (shlex.quote(workdir), prefix, self.compose_cmd, args)
        return self._run(cmd, **kw)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        cmd = "%s%s version --short" % ("sudo " if self.use_sudo else "", self.compose_cmd)
        try:
            rc, out, err = self._run("echo tam-ok && " + cmd, timeout=60, check=False)
        except UserError:
            raise
        except Exception as e:
            raise UserError(_("SSH connection to %(host)s failed:\n%(err)s",
                              host=self.host, err=e))
        if rc != 0:
            raise UserError(_(
                "SSH works, but the compose command failed on %(host)s:\n%(cmd)s\n\n"
                "%(err)s%(hint)s",
                host=self.host, cmd=cmd, err=(err or out).strip()[:1500],
                hint=self._compose_hint(err or out)))
        self.last_error = False
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Connection successful"),
                "message": _("Compose version: %s", out.replace("tam-ok", "").strip()),
                "sticky": False,
            },
        }

    def action_clear_host_key(self):
        """Forget the pinned key, so the next connection pins whatever is there."""
        self.sudo().write({"host_key": False})
        return True

    def action_scan_instances(self):
        """Discover instance directories under instances_path."""
        for rec in self:
            self.env["tam.instance"]._scan_server(rec)
        return True

    def action_open_instances(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Instances"),
            "res_model": "tam.instance",
            "view_mode": "list,form",
            "domain": [("server_id", "=", self.id)],
            "context": {"default_server_id": self.id},
        }
