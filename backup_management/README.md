# Database Backup Management (Odoo 19)

Sauvegarde automatique de la base Odoo (dump + filestore) vers **Google Drive**
ou **SFTP securise**, avec retention configurable.

## Dependances Python (dans le venv Odoo)

```bash
pip install google-api-python-client google-auth google-auth-httplib2 paramiko
```

Sur le serveur AREON :

```bash
sudo -u odoo19 /opt/odoo19/odoo19-venv/bin/pip install \
  google-api-python-client google-auth google-auth-httplib2 paramiko
sudo systemctl restart odoo19
```

## Mode SFTP

1. Creer une configuration, destination **SFTP securise**.
2. Renseigner hote, port, utilisateur, repertoire distant.
3. Authentification : mot de passe ou cle privee SSH (PEM).
4. **Securite** : mode strict (recommande) avec empreinte SHA256 du serveur.
   - Obtenir l'empreinte : `ssh-keyscan -t ed25519,rsa HOTE | ssh-keygen -lf -`
   - Mode `auto_add` : utile en dev/test ; le bouton « Tester connexion SFTP »
     affiche l'empreinte a copier.
5. « Tester connexion SFTP » puis « Lancer un backup test ».

## Mode OAuth2 (Gmail / compte perso)

1. Google Cloud Console -> APIs & Services -> Enable "Google Drive API".
2. OAuth consent screen : type "External". **Important : publier l'app
   ("Publish App").** Tant qu'elle reste en "Testing", Google invalide le
   refresh_token au bout de 7 jours et les backups cassent.
3. Credentials -> Create OAuth client ID -> type "Web application".
   Dans "Authorized redirect URIs", coller exactement la valeur du champ
   "Redirect URI" affiche dans la config Odoo
   (`<web.base.url>/drive_backup/oauth/callback`).
4. Dans Odoo : destination Google Drive, mode OAuth2, coller Client ID + Secret,
   "Autoriser Google Drive", valider le consentement Google.

## Mode Service Account (Workspace)

1. Cloud Console -> Service Accounts -> creer un compte -> generer une cle JSON.
2. Activer Google Drive API.
3. Partager le dossier (ou le Shared Drive) cible avec l'email du service
   account en "Editeur".
4. Dans Odoo : mode Service Account, uploader la cle JSON, renseigner l'ID
   du dossier (et le Shared Drive ID si applicable).

## Planification

Le cron `Google Drive : backup automatique` est cree **desactive**.
L'activer (Parametres techniques -> Actions planifiees) une fois qu'une config
a ete testee via « Lancer un backup test ».

## Restauration

Hors scope volontairement : restauration via `/web/database/manager`.
