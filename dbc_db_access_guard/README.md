# Database Access Guard (Odoo 19)

Hides the Odoo database manager and database selector behind a secret,
configurable URL and answers a plain **404 Not Found** on the stock URLs.

## What it blocks

Every stock database-management route answers `404`:

| URL | |
|---|---|
| `/web/database/manager` | the database manager |
| `/web/database/selector` | the database selector (can be left public) |
| `/web/database/create`, `/duplicate`, `/drop` | database creation / duplication / deletion |
| `/web/database/backup`, `/restore` | dumps and restores |
| `/web/database/change_password` | master password change |
| `/web/database/list` | database listing used by the mobile app |
| `db` RPC service over `/jsonrpc` and `/xmlrpc` | remote database listing / management |

## What it opens instead

The complete database manager is served on a secret base path, by default:

```
https://your-host/database/technique-acces            -> database manager
https://your-host/database/technique-acces/selector   -> database selector
```

Links, form actions and redirects of the manager are rewritten on the fly, so
creating, duplicating, dropping, backing up and restoring databases all keep
working under the secret path. Nothing of the secret path ever appears on a
public page.

## Configuration

**Settings → General Settings → Database Manager Protection**
(visible to the *Settings* group):

| Setting | Default | |
|---|---|---|
| Protect the database manager | on | master switch |
| Secret database manager path | `/database/technique-acces` | the hidden base URL |
| Restrict to IP addresses | empty | optional allow-list (`10.0.0.5, 192.168.1.0/24`) for the secret path *and* for the `db` RPC service |
| Block the database selector | on | also 404 on `/web/database/selector` |
| Block the database RPC service | on | refuse the `db` RPC service |

The settings are also readable/writable as system parameters:
`dbc_db_access_guard.enabled`, `.secret_path`, `.block_selector`,
`.block_db_rpc`, `.allowed_ips`.

Because the database manager is **not attached to any database**, the guard
protects the whole Odoo process: the settings are instance-wide and the last
database that saves them wins. They are mirrored into
`<data_dir>/dbc_db_access_guard/guard.json`, which is what the middleware reads
on each request (it cannot open a cursor: those routes are served with no
database at all).

## Installation

1. Put the module in your addons path and install it (Apps → *Database Access
   Guard*). Installing it already activates the guard with the default path.
2. **Recommended** – add it to `server_wide_modules` in `odoo.conf` so the guard
   is loaded before the first request a worker serves, even before any database
   registry is loaded:

   ```ini
   [options]
   server_wide_modules = base,rpc,web,dbc_db_access_guard
   ```

   then restart Odoo. The settings screen shows a warning while this is missing.

Uninstalling the module (or switching the master switch off) restores the stock
URLs, so you can never lock yourself out.

### If you lock yourself out

You do not need to uninstall anything; the mirror file is the source of truth.
On the server (inside the container, for a Docker install):

```bash
# where am I?
cat <data_dir>/dbc_db_access_guard/guard.json      # shows the active secret path
# emergency: give the stock URLs back, takes effect on the next request
sed -i 's/"enabled": true/"enabled": false/' <data_dir>/dbc_db_access_guard/guard.json
```

Deleting the file instead puts the guard back on its default path
(`/database/technique-acces`). No restart is needed in either case.

## Notes for the technical team

* The active secret path is logged at every server start:
  `Database access guard active: database manager on /database/technique-acces`.
  Blocked attempts are logged too (`odoo.addons.dbc_db_access_guard.guard.middleware`).
* Blocking the selector removes the page Odoo redirects to when it cannot tell
  which database a visitor wants. Pin the database in `odoo.conf` so that
  redirect never happens (recommended):

  ```ini
  db_name = your_main_database   # with an empty dbfilter, this is the only
                                 # database exposed over HTTP -> /web/login works
  ```

  `/web/login` then answers directly and the hidden manager lists that database
  only. Alternatives: a `dbfilter` such as `^%d$`, reaching a database
  explicitly with `/odoo?db=mydb`, or leaving the selector public. To work on
  another database, comment `db_name` out again and restart.
* Blocking the `db` RPC service also stops remote database *listing*, which is
  the main information leak left once the manager is hidden. Backup scripts that
  use that service keep working from the allow-listed IP addresses.
* This module hides the manager; it does not replace a strong
  `admin_passwd` in `odoo.conf`, which still protects every destructive
  database operation.

## How it works

`/web/database/*` are `auth="none"` routes. A request without a database is
served through `Application.nodb_routing_map`, which is built from the
*server-wide* modules only, so a controller in a regular module can never
override them; and routing maps are cached, so a route whose URL is a user
setting cannot be declared with `@http.route` either.

The module therefore wraps `odoo.http.Application.__call__` (and
`odoo.service.db.dispatch` for the RPC service). That runs before any routing,
for every request, with or without a database, and re-reads the secret path on
each request.
