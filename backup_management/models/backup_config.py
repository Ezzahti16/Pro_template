# -*- coding: utf-8 -*-
import base64
import hashlib
import io
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlencode

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.service import db as db_service

_logger = logging.getLogger(__name__)

# Imports Google isoles : si la lib manque, le module reste installable mais
# leve une erreur claire au moment de l'execution (pas a l'import).
try:
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.oauth2.service_account import (
        Credentials as ServiceAccountCredentials,
    )
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:  # pragma: no cover
    UserCredentials = ServiceAccountCredentials = None
    GoogleAuthRequest = build = MediaFileUpload = None
    _logger.debug("Librairies Google non installees.")

try:
    import paramiko
    from paramiko import ECDSAKey, Ed25519Key, RSAKey
except ImportError:  # pragma: no cover
    paramiko = ECDSAKey = Ed25519Key = RSAKey = None
    _logger.debug("Librairie paramiko non installee.")

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# drive.file = moindre privilege : l'app ne voit que les fichiers qu'elle cree.
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class BackupConfig(models.Model):
    _name = "backup.config"
    _description = "Configuration de sauvegarde"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    destination_type = fields.Selection(
        [
            ("google_drive", "Google Drive"),
            ("sftp", "SFTP securise"),
        ],
        string="Destination",
        default="google_drive",
        required=True,
    )
    database_name = fields.Char(
        string="Base de donnees",
        required=True,
        default=lambda self: self.env.cr.dbname,
    )
    backup_format = fields.Selection(
        [("zip", "Zip (SQL + filestore)"), ("dump", "Dump SQL seul")],
        string="Format",
        default="zip",
        required=True,
    )
    auth_type = fields.Selection(
        [
            ("oauth", "OAuth2 - Gmail / compte personnel"),
            ("service_account", "Service Account - Google Workspace"),
        ],
        string="Mode d'authentification",
        default="oauth",
        required=True,
    )

    drive_folder_id = fields.Char(
        string="ID du dossier Drive",
        help="La partie de l'URL apres /folders/ dans Google Drive.",
    )
    retention_count = fields.Integer(
        string="Backups a conserver",
        default=7,
        help="Nombre de fichiers gardes dans le dossier. 0 = illimite.",
    )

    # --- OAuth2 ---
    oauth_client_id = fields.Char(string="Client ID")
    oauth_client_secret = fields.Char(string="Client Secret")
    oauth_refresh_token = fields.Char(
        string="Refresh Token",
        copy=False,
        help="Rempli automatiquement par le bouton 'Autoriser' (si tu as un "
             "domaine HTTPS), OU colle ici un refresh token obtenu via Google "
             "OAuth Playground (cas sans domaine).",
    )
    oauth_redirect_uri = fields.Char(
        string="Redirect URI",
        compute="_compute_oauth_redirect_uri",
        help="A enregistrer A L'IDENTIQUE dans la Google Cloud Console.",
    )
    oauth_authorized = fields.Boolean(
        string="Autorise", compute="_compute_oauth_authorized"
    )

    # --- Service account ---
    sa_key_file = fields.Binary(string="Cle JSON Service Account", attachment=False)
    sa_key_filename = fields.Char(string="Nom du fichier")
    sa_shared_drive_id = fields.Char(
        string="Shared Drive ID",
        help="Optionnel. Renseignez si le dossier cible est dans un Drive partage "
             "(impose par les Service Accounts sur Workspace).",
    )

    # --- SFTP ---
    sftp_host = fields.Char(string="Hote SFTP")
    sftp_port = fields.Integer(string="Port SFTP", default=22)
    sftp_username = fields.Char(string="Utilisateur SFTP")
    sftp_auth_type = fields.Selection(
        [
            ("password", "Mot de passe"),
            ("private_key", "Cle privee SSH"),
        ],
        string="Authentification SFTP",
        default="password",
    )
    sftp_password = fields.Char(string="Mot de passe SFTP", copy=False)
    sftp_private_key = fields.Text(
        string="Cle privee SSH (PEM)",
        copy=False,
        help="Contenu du fichier id_rsa / id_ed25519 au format PEM.",
    )
    sftp_private_key_passphrase = fields.Char(
        string="Passphrase de la cle",
        copy=False,
    )
    sftp_remote_path = fields.Char(
        string="Repertoire distant",
        default="/backups",
        help="Chemin absolu sur le serveur SFTP (ex: /var/backups/odoo).",
    )
    sftp_host_key_policy = fields.Selection(
        [
            ("strict", "Verifier l'empreinte (recommande)"),
            ("auto_add", "Accepter automatiquement (dev/test)"),
        ],
        string="Politique cle hote",
        default="strict",
        required=True,
    )
    sftp_host_key_fingerprint = fields.Char(
        string="Empreinte SHA256 du serveur",
        help="Format : SHA256:xxxx... Obtenir avec : "
             "ssh-keyscan -t ed25519,rsa HOTE | ssh-keygen -lf -",
    )

    last_backup_date = fields.Datetime(string="Dernier backup", readonly=True, copy=False)
    last_backup_status = fields.Selection(
        [("success", "Succes"), ("failed", "Echec")],
        string="Dernier statut",
        readonly=True,
        copy=False,
    )
    log_ids = fields.One2many("backup.log", "config_id", string="Logs")

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains(
        "destination_type",
        "drive_folder_id",
        "sftp_host",
        "sftp_username",
        "sftp_auth_type",
        "sftp_password",
        "sftp_private_key",
        "sftp_host_key_policy",
        "sftp_host_key_fingerprint",
    )
    def _check_destination_config(self):
        for cfg in self:
            if cfg.destination_type == "google_drive":
                if not cfg.drive_folder_id:
                    raise ValidationError(
                        _("L'ID du dossier Drive est obligatoire pour Google Drive.")
                    )
            elif cfg.destination_type == "sftp":
                if not cfg.sftp_host:
                    raise ValidationError(_("L'hote SFTP est obligatoire."))
                if not cfg.sftp_username:
                    raise ValidationError(_("L'utilisateur SFTP est obligatoire."))
                if cfg.sftp_auth_type == "password" and not cfg.sftp_password:
                    raise ValidationError(
                        _("Le mot de passe SFTP est obligatoire en mode mot de passe.")
                    )
                if cfg.sftp_auth_type == "private_key" and not cfg.sftp_private_key:
                    raise ValidationError(
                        _("La cle privee SSH est obligatoire en mode cle privee.")
                    )
                if (
                    cfg.sftp_host_key_policy == "strict"
                    and not cfg.sftp_host_key_fingerprint
                ):
                    raise ValidationError(
                        _(
                            "L'empreinte SHA256 du serveur est obligatoire "
                            "en mode strict."
                        )
                    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    def _compute_oauth_redirect_uri(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for cfg in self:
            cfg.oauth_redirect_uri = "%s/drive_backup/oauth/callback" % (base_url or "")

    @api.depends("oauth_refresh_token")
    def _compute_oauth_authorized(self):
        for cfg in self:
            cfg.oauth_authorized = bool(cfg.oauth_refresh_token)

    # ------------------------------------------------------------------
    # Credentials Google Drive
    # ------------------------------------------------------------------
    def _get_drive_service(self):
        self.ensure_one()
        if build is None:
            raise UserError(_(
                "Librairies Google manquantes. Dans le venv Odoo :\n"
                "pip install google-api-python-client google-auth google-auth-httplib2"
            ))
        if self.auth_type == "service_account":
            creds = self._get_service_account_credentials()
        else:
            creds = self._get_oauth_credentials()
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def _get_service_account_credentials(self):
        self.ensure_one()
        if not self.sa_key_file:
            raise UserError(_("Aucune cle Service Account fournie."))
        key_info = json.loads(base64.b64decode(self.sa_key_file))
        return ServiceAccountCredentials.from_service_account_info(
            key_info, scopes=DRIVE_SCOPES
        )

    def _get_oauth_credentials(self):
        self.ensure_one()
        if not self.oauth_refresh_token:
            raise UserError(_(
                "Configuration OAuth non autorisee. Cliquez sur "
                "'Autoriser Google Drive'."
            ))
        creds = UserCredentials(
            token=None,
            refresh_token=self.oauth_refresh_token,
            client_id=self.oauth_client_id,
            client_secret=self.oauth_client_secret,
            token_uri=GOOGLE_TOKEN_URI,
            scopes=DRIVE_SCOPES,
        )
        creds.refresh(GoogleAuthRequest())  # rafraichit l'access token
        return creds

    # ------------------------------------------------------------------
    # SFTP
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_sha256_fingerprint(fingerprint):
        fp = (fingerprint or "").strip()
        if not fp:
            return ""
        if not fp.upper().startswith("SHA256:"):
            fp = "SHA256:" + fp
        return fp.upper()

    @staticmethod
    def _host_key_fingerprint(host_key):
        digest = hashlib.sha256(host_key.asbytes()).digest()
        encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
        return "SHA256:" + encoded

    def _load_sftp_private_key(self):
        self.ensure_one()
        key_data = (self.sftp_private_key or "").strip()
        passphrase = self.sftp_private_key_passphrase or None
        for key_class in (Ed25519Key, ECDSAKey, RSAKey):
            if key_class is None:
                continue
            try:
                return key_class.from_private_key(
                    io.StringIO(key_data), password=passphrase
                )
            except Exception:  # noqa: BLE001
                continue
        raise UserError(_("Cle privee SSH invalide ou format non supporte."))

    def _verify_sftp_host_key(self, transport):
        self.ensure_one()
        if self.sftp_host_key_policy != "strict":
            return
        remote_key = transport.get_remote_server_key()
        actual = self._normalize_sha256_fingerprint(
            self._host_key_fingerprint(remote_key)
        )
        expected = self._normalize_sha256_fingerprint(
            self.sftp_host_key_fingerprint
        )
        if actual != expected:
            raise UserError(_(
                "Empreinte de cle hote invalide.\n"
                "Attendue : %(expected)s\n"
                "Recue    : %(actual)s"
            ) % {"expected": expected, "actual": actual})

    def _connect_sftp(self):
        self.ensure_one()
        if paramiko is None:
            raise UserError(_(
                "Librairie paramiko manquante. Dans le venv Odoo :\n"
                "pip install paramiko"
            ))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.sftp_host,
            "port": self.sftp_port or 22,
            "username": self.sftp_username,
            "timeout": 60,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.sftp_auth_type == "password":
            connect_kwargs["password"] = self.sftp_password
        else:
            connect_kwargs["pkey"] = self._load_sftp_private_key()

        client.connect(**connect_kwargs)
        self._verify_sftp_host_key(client.get_transport())
        return client

    @contextmanager
    def _sftp_session(self):
        client = self._connect_sftp()
        sftp = None
        try:
            sftp = client.open_sftp()
            yield sftp
        finally:
            if sftp:
                sftp.close()
            client.close()

    def _sftp_ensure_remote_dir(self, sftp, remote_dir):
        parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
        path = ""
        for part in parts:
            path = "%s/%s" % (path, part) if path else "/%s" % part
            try:
                sftp.stat(path)
            except OSError:
                sftp.mkdir(path)

    def _upload_to_sftp(self, local_path, filename):
        self.ensure_one()
        remote_dir = (self.sftp_remote_path or "/").rstrip("/") or "/"
        remote_path = "%s/%s" % (remote_dir, filename)
        with self._sftp_session() as sftp:
            self._sftp_ensure_remote_dir(sftp, remote_dir)
            sftp.put(local_path, remote_path)
        return remote_path

    def _apply_sftp_retention(self):
        self.ensure_one()
        if not self.retention_count or self.retention_count <= 0:
            return
        remote_dir = (self.sftp_remote_path or "/").rstrip("/") or "/"
        ext = "zip" if self.backup_format == "zip" else "dump"
        prefix = "%s_" % self.database_name
        suffix = ".%s" % ext

        with self._sftp_session() as sftp:
            entries = []
            for name in sftp.listdir(remote_dir):
                if not name.startswith(prefix) or not name.endswith(suffix):
                    continue
                remote_path = "%s/%s" % (remote_dir, name)
                attrs = sftp.stat(remote_path)
                entries.append((attrs.st_mtime, remote_path))

            entries.sort(key=lambda item: item[0], reverse=True)
            for _mtime, remote_path in entries[self.retention_count:]:
                sftp.remove(remote_path)

    def action_test_sftp_connection(self):
        self.ensure_one()
        client = self._connect_sftp()
        sftp = None
        fingerprint = ""
        remote_dir = ""
        try:
            transport = client.get_transport()
            if transport and self.sftp_host_key_policy == "auto_add":
                fingerprint = self._normalize_sha256_fingerprint(
                    self._host_key_fingerprint(
                        transport.get_remote_server_key()
                    )
                )
            sftp = client.open_sftp()
            remote_dir = (self.sftp_remote_path or "/").rstrip("/") or "/"
            self._sftp_ensure_remote_dir(sftp, remote_dir)
        finally:
            if sftp:
                sftp.close()
            client.close()

        message = _("Connexion SFTP reussie. Repertoire : %s") % remote_dir
        if fingerprint:
            message = _(
                "Connexion SFTP reussie.\n"
                "Empreinte du serveur : %(fp)s\n"
                "Copiez-la dans le champ 'Empreinte SHA256' puis passez "
                "en mode strict."
            ) % {"fp": fingerprint}

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("SFTP"),
                "message": message,
                "type": "success",
                "sticky": bool(fingerprint),
            },
        }

    # ------------------------------------------------------------------
    # Flow d'autorisation OAuth2
    # ------------------------------------------------------------------
    def action_oauth_authorize(self):
        self.ensure_one()
        if not (self.oauth_client_id and self.oauth_client_secret):
            raise UserError(_("Renseignez d'abord le Client ID et le Client Secret."))
        params = {
            "response_type": "code",
            "client_id": self.oauth_client_id,
            "redirect_uri": self.oauth_redirect_uri,
            "scope": " ".join(DRIVE_SCOPES),
            "access_type": "offline",  # indispensable pour obtenir un refresh_token
            "prompt": "consent",       # force le renvoi du refresh_token a chaque fois
            "state": str(self.id),
        }
        return {
            "type": "ir.actions.act_url",
            "url": "%s?%s" % (GOOGLE_AUTH_URI, urlencode(params)),
            "target": "new",
        }

    def _exchange_oauth_code(self, code):
        self.ensure_one()
        data = {
            "code": code,
            "client_id": self.oauth_client_id,
            "client_secret": self.oauth_client_secret,
            "redirect_uri": self.oauth_redirect_uri,
            "grant_type": "authorization_code",
        }
        resp = requests.post(GOOGLE_TOKEN_URI, data=data, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise UserError(_(
                "Google n'a pas renvoye de refresh_token. Revoquez l'acces de l'app "
                "dans votre compte Google (myaccount.google.com/permissions) puis "
                "relancez l'autorisation."
            ))
        self.oauth_refresh_token = refresh_token

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------
    @api.model
    def _cron_run_backups(self):
        """Appele par l'ir.cron : model._cron_run_backups()."""
        for cfg in self.search([("active", "=", True)]):
            try:
                cfg._run_backup()
                # commit par config : un echec n'annule pas les succes precedents
                self.env.cr.commit()
            except Exception as e:  # noqa: BLE001
                self.env.cr.rollback()
                _logger.exception("Backup en echec pour la config %s", cfg.name)
                cfg._log(False, str(e), 0)
                cfg.last_backup_status = "failed"
                self.env.cr.commit()

    def _run_backup(self):
        self.ensure_one()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "zip" if self.backup_format == "zip" else "dump"
        filename = "%s_%s.%s" % (self.database_name, ts, ext)

        fd, tmp_path = tempfile.mkstemp(suffix="." + ext)
        try:
            # dump_db ecrit directement dans le flux ; appele en interne il NE
            # verifie PAS le master password (ce controle vit dans le controleur web).
            with os.fdopen(fd, "wb") as stream:
                db_service.dump_db(
                    self.database_name, stream, backup_format=self.backup_format
                )
            size = os.path.getsize(tmp_path)

            if self.destination_type == "google_drive":
                service = self._get_drive_service()
                file_ref = self._upload_to_drive(service, tmp_path, filename)
                self._apply_drive_retention(service)
                log_message = "OK - Drive file id: %s" % file_ref
            else:
                remote_path = self._upload_to_sftp(tmp_path, filename)
                self._apply_sftp_retention()
                log_message = "OK - SFTP: %s" % remote_path

            self.write({
                "last_backup_date": fields.Datetime.now(),
                "last_backup_status": "success",
            })
            self._log(True, log_message, size)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _upload_to_drive(self, service, path, filename):
        self.ensure_one()
        mimetype = (
            "application/zip" if self.backup_format == "zip"
            else "application/octet-stream"
        )
        metadata = {"name": filename, "parents": [self.drive_folder_id]}
        media = MediaFileUpload(path, mimetype=mimetype, resumable=True)
        request = service.files().create(
            body=metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,  # necessaire pour les Drive partages
        )
        response = None
        while response is None:
            _status, response = request.next_chunk()
        return response.get("id")

    def _apply_drive_retention(self, service):
        self.ensure_one()
        if not self.retention_count or self.retention_count <= 0:
            return
        result = service.files().list(
            q="'%s' in parents and trashed = false" % self.drive_folder_id,
            orderBy="createdTime desc",
            fields="files(id, name, createdTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])
        for old in files[self.retention_count:]:
            service.files().delete(
                fileId=old["id"], supportsAllDrives=True
            ).execute()

    def _log(self, success, message, size):
        self.ensure_one()
        self.env["backup.log"].create({
            "config_id": self.id,
            "status": "success" if success else "failed",
            "message": message,
            "file_size": size,
        })

    def action_test_backup(self):
        self.ensure_one()
        self._run_backup()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Backup"),
                "message": _("Sauvegarde terminee avec succes."),
                "type": "success",
                "sticky": False,
            },
        }
