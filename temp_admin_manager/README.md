# Temp Admin Manager

Creates a temporary admin user on Dockerized Odoo instances and deletes it 6 hours later.
Install it on one central Odoo; it reaches the clients over SSH and runs `odoo shell`
inside their containers. Nothing is installed on the client instances.

## Requirements

- `pip install paramiko` on the central Odoo.
- SSH access to each Docker host, with permission to run `docker compose`
  (tick **Prefix with sudo** if docker needs sudo there).
- One directory per instance under the instances path, each with a compose file
  (`docker-compose.yml`, `docker-compose.yaml`, `compose.yml` or `compose.yaml`).

## Flow

1. **Temp Admin → Servers → New.** Fill the SSH details and the **Instances Path**
   (e.g. `/opt/odoo`), then **Test Connection**.
2. **Scan Instances.** Every sub-directory holding a compose file becomes an instance,
   and the Odoo / PostgreSQL compose services are guessed from the service names.
   Correct them on the instance if the guess is wrong.
3. Open an instance and **Scan Databases**. It lists the databases through
   `psql` in the DB service, so instances with several databases are handled.
4. Each database row has the state and the buttons: **Create temp admin**,
   **Delete now**, **Refresh**. `Temp Admin → Databases` shows every database
   across all servers in one list.

## What the temp admin gets

Odoo has no single group that means "everything": the superuser (`base.user_root`,
uid 1) bypasses access checks in code and is not a login account, and
`base.group_system` is only Settings — it carries none of the accounting,
inventory or sales manager rights. Access is the sum of the groups held, so the
**Access Model** on the instance says which set to hand over:

- **Same as administrator** (default) — copies the client's own administrator:
  its groups, its companies, and its language and timezone. You then see exactly
  what their admin sees. Granting every group instead would also switch on
  features they never enabled, so the instance would not look the way they use it.
- **Every group** — every group on the database. The escape hatch for when the
  client's own administrator is itself stripped or broken.
- **Copy a specific user** — copies the user whose res.users ID you give, for
  when the right one is not the administrator.

The administrator is found by **`base.group_system` membership**, not by name or
id, so a renamed, renumbered or archived one is still handled: active non-portal
users holding that group are the candidates, uid 1 is excluded (the superuser
bypasses checks in code and holds barely any groups to copy), `base.user_admin`
is preferred when it qualifies, and otherwise the candidate with the most groups
wins. If nobody holds the group it falls back to every group and says so, rather
than handing over a user who cannot open Settings.

**Cloned From** records which user was copied, and **Access Granted** the result,
e.g. `same as Mitchell Admin (#2, admin@example.com): 21 groups, 1 of 2 companies`.
If some module refuses a group, the set is retried in halves so the refusal costs
only itself, and the refused group is named.

Creation already applies the model, so nothing else is needed in normal use. For a
user that is already there, the **Re-apply access** button in the Databases list
applies the model again, widening or narrowing it in place. It is kept off the form
because it is only for recovery: a user who has done work cannot always be deleted
(the module retires it instead, and the login then still exists), so re-applying is
the way to change its access without deleting and recreating anything.

The instance also carries two options: **Copy language and timezone** (on) and
**Add technical features** (off), which grants `base.group_no_one` on top of the
copied access for the developer menus the client's admin usually lacks.

## States

- **Not created** - no temporary admin on this database.
- **Active** - created by this module, with the expiry shown.
- **Deleted** - removed, by the cron or by hand.
- **Already exists** - the login is taken by a user this module did not create.
  Nothing was touched.

## Safety rules

- **An existing user is never modified.** If the login already exists, creation stops
  and the database goes to *Already exists*. Use a different email, or remove that
  user on the instance first.
- **Deletion is matched on both ID and login**, so only the user this module created
  is ever removed. If that user owns records and cannot be deleted, it is archived
  and its password and groups are stripped instead.
- The SSH host key is pinned on the first connection; later connections are refused
  if it changes.

## Cleanup

`Temp Admin: delete expired users` runs every 5 minutes and deletes users past their
expiry. A failed cleanup is recorded on the database row and retried on the next run,
so the state stays *Active* until the user is really gone.

## Ahead of the publisher notification

Every client database runs `mail.ir_cron_module_update_notification` ("Publisher: Update
Notification"), weekly, which reports the database to Odoo. It sends **counts, not
identities** — `nbr_users`, `nbr_active_users` and the two share equivalents, plus the
database uuid, version, installed apps, subscription code and the company's name, email
and phone. No logins and no user names. A temporary admin still active when it fires is
counted among the users, which is what Odoo's reply to the client is based on. Every one
of those counts filters on `active = True`, so a retired user is already out of the
report.

So the temporary admin is given a life that ends **before** that cron, not six hours:

- The notification is read from the client (never written to) and measured against the
  **client's own clock**, so a clock difference between the manager and the client server
  cannot eat the margin.
- Further away than the lifetime, or switched off, or absent → the full lifetime, and
  nothing is written to the client.
- Inside the lifetime → the expiry becomes *notification − margin*, the database shows
  **Cut Short** and *Publisher Notification* says when it runs.
- Less than *Shortest useful life* left → **nothing is created**, and the message says
  when the notification runs and how to override it.

With **Arm a guard on the client** on, a second guard is created in the client database
itself: a one-shot scheduled action that archives the temporary admin at the same moment,
so it happens even when this manager cannot be reached. It is one-shot through a cron
trigger (Odoo 15+) or `numbercall` (Odoo 14), because from Odoo 19 a cron may not write to
itself while it runs. It is removed when the temporary admin is deleted, and it only ever
touches a user matching both the ID and the login.

Settings live on the instance: **Stay ahead of the publisher notification**, **Margin**
(15 minutes), **Shortest useful life** (10 minutes), **Arm a guard on the client**. Keep
the margin above the five minutes of the cleanup schedule.

## Defaults

Login `dbc@gmail.com`, password `Y@s123`, lifetime 6 hours — all editable per database.

## Notes

- Written for Odoo 19 on the central side (`models.Constraint` instead of
  `_sql_constraints`, which Odoo 19 no longer honours).
- The **client** instances can be any version from 14 to 19. The shell script stays
  on Python 3.6 syntax and looks up every version-specific name at runtime: the
  groups field (`groups_id`, renamed `group_ids` in 19), the user-type category
  (dropped in 19), flush / invalidate, which moved from the model to the environment
  in 17, and on `ir.cron` the name (`cron_name` from 17), `numbercall` (dropped in 17)
  and `_trigger` (absent in 14). Tested against real Odoo 14, 15, 16, 18 and 19
  containers.
- If a client's compose file mounts no `odoo.conf`, `docker compose exec` gives
  `odoo shell` no database settings, because it skips the entrypoint that would
  have read `HOST` / `USER` / `PASSWORD`. The module retries with explicit
  `--db_host` / `--db_user` / `--db_password` and then remembers that per instance
  (**Pass DB args**).
- SSH keys and passwords are stored in plain text in the central database.
  Keep that instance locked down.
