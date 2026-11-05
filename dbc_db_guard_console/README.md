# Database Guard Console (Odoo 19)

Central console for the `dbc_db_access_guard` agent: register every managed
Odoo instance once, then read and change the hidden database-manager URL of all
of them from a single screen.

> **The console does not replace the agent.** The 404 and the secret URL are
> enforced by a WSGI hook *inside* each instance's own process — `/web/database/*`
> is served through Odoo's *nodb* routing map and the setting must be readable
> without any database cursor. No remote call can do that from outside.
> `dbc_db_access_guard` stays installed on every client instance; this module
> only drives it.

## The screen

**Instance Guard → Managed Instances**, one record per instance:

| | |
|---|---|
| **Connection** | Instance URL, database, login, API key, transport, timeout, SSL verification |
| **Protection** | Secret path, block the selector, block the `db` RPC service, IP allow-list |
| **Observed** | Agent installed + version, Odoo version, transport in use, loaded server-wide, what the instance currently serves, last sync |
| **State** | Never synced → Not protected / Protected, plus *Out of sync*, *Agent missing*, *Unreachable*, *Error* |
| **History** | Every test, fetch, apply and rollback, with who did it and the exact message |

Buttons: **Test Connection**, **Fetch Configuration**, **Apply & Protect**,
**Deactivate**, **Open Manager**.

## What each button really does

* **Test Connection** — resolves the transport, reads the Odoo version, the
  agent's state and whether the agent is in `server_wide_modules` on that
  instance (if it is not, the manager is exposed for the first seconds after
  each restart there, and the form says so).
* **Fetch Configuration** — reads what the instance *actually enforces*. The
  very first sync **adopts** those values; later ones **compare** them against
  what this console last applied and raise **Out of sync** with a field-by-field
  diff when somebody changed the instance behind your back.
* **Apply & Protect** — pushes the configuration, then **verifies over plain
  HTTP** that the new secret URL answers `200` and `/web/database/manager`
  answers `404`. If that fails, it **rolls the instance back** to the exact
  configuration it had before and marks the record in error. This is what stops
  a typo from locking your technical team out of a client's database manager.
  (With an IP allow-list the console cannot see the secret URL, so verification
  is skipped instead of triggering a false rollback — the history says so.)
* **Deactivate** — switches the protection off on that instance and checks the
  stock URL answers again.

A daily cron (`Instance Guard: refresh the state of managed instances`)
re-reads every instance so drift and unreachable instances surface on their own.

## Transport

| | |
|---|---|
| `json2` | `POST /json/2/<model>/<method>`, `Authorization: Bearer <api key>`, database in the `X-Odoo-Database` header. Odoo 19+. Needs a **global scope** API key — a password will not work. |
| `jsonrpc` | `POST /jsonrpc`, `service=object` / `execute_kw`. Works on older instances and accepts a password, but Odoo 19 logs it as deprecated and Odoo 22 removes it. |
| `auto` (default) | tries `json2`, falls back to `jsonrpc`. |

The agent's own "block the `db` RPC service" option does **not** get in the way:
it only blocks `service=db`, never `service=object` or `/json/2`.

### Credentials

Create the key on the managed instance: *Preferences → Account Security → New
API Key*, with a user in the **Settings** group, then paste it into the record.
Prefer a key over a password — it is revocable per instance and is the only
thing `json2` accepts.

The key is stored in clear in this database, restricted to the *Database Guard
Console / Manager* group. Treat this console as the most sensitive database of
your fleet: give the Manager group to the technical team only.

## How the agent is driven

Only standard models are used, so the agent module is never modified and needs
no API of its own:

* `ir.module.module` — is the agent installed, which version;
* `res.config.settings` — **read**: returns the *effective* configuration,
  because the agent's `get_values()` reads the mirror file its middleware obeys
  (the system parameters are only a copy and can be stale); **write**: runs the
  agent's `set_values()`, which validates the path and rewrites that mirror file.

Applying is therefore exactly equivalent to opening *Settings* on the remote
instance and pressing *Save*. Writing `ir.config_parameter` directly would
change nothing at all.

The path rules (`tools/guard_remote.normalize_secret_path`) are deliberately
duplicated from the agent's `config_store`, so the console can validate before
sending even where the agent is not installed next to it. A test asserts the two
stay identical when both modules are on the same addons path.

## Onboarding a new instance

1. On the instance: install `dbc_db_access_guard`, add it to `server_wide_modules`
   in `odoo.conf`, restart.
2. On the instance: create an API key for a Settings user.
3. Here: create the record, **Test Connection**, **Fetch Configuration**, set the
   secret path you want, **Apply & Protect**.

## Limits worth knowing

* Pushing code and editing `odoo.conf` are file-level operations: the console
  cannot do either. Onboarding keeps one manual step per instance.
* One record per *instance*, not per database: the guard protects the whole Odoo
  process, so the database on the record is only the one the console logs into.
* If an instance sits behind a reverse proxy that already blocks
  `/web/database/*`, deactivation cannot be confirmed over HTTP — the console
  reports that instead of guessing.
