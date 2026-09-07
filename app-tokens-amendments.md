# Amendments to `app-tokens-prompt.md`

Decisions taken during the work that the brief does not say, or says the
opposite of. Approved by the human. Read this alongside the brief.

## 1. The lock covers both stores, not just the app tokens

The brief scopes the `fcntl.flock` to the app token store: a sidecar
`app_tokens.lock` under "Concurrency", and step B's "the flock'd
read-modify-write" as part of the new module.

**Put it in `state_file.py` instead, and lock `provider_access_urls.py`'s
writes with it too.**

`save_access_url` is a load-modify-save with no lock, so two `claim` processes
running at once each save a different provider key and the second `replace`
drops the first. That loses an access URL *after* its one-time setup token was
spent, which is the worst outcome this application has — `provider_creds.json`
is the file nothing regenerates. CodeRabbit found it reviewing step A; the
brief never considered the store because it was reasoning about which
*writers* run concurrently, and `claim` has no server counterpart the way the
claim handler does.

Step A extracted `state_file.py` precisely because both stores turned out to
have identical file-handling requirements. Locking is one more of them, and
step B builds the lock anyway, so this is one implementation rather than two.

The sidecar rule still holds and is the reason it cannot be simpler: lock a
sidecar, never the store, because the atomic write replaces the file and a
lock held on the store's inode does not guard the file that ends up at that
path. Each store gets its own sidecar next to it.

## 2. Where the brief's own reasoning was tightened

Both of these are already durable — in the code, its commit message, or
`AGENTS.md` — and are listed only so this file is a complete account.

- **The directory `fsync` is best-effort.** The brief says to fsync the file
  and the directory or neither. Both happen, but a failure *after* the rename
  is not a failed save: the file is in place and every reader sees it, so
  raising would tell `claim` it lost a credential it had in fact stored. It is
  reachable without an exotic filesystem — `check_can_save` tests
  `W_OK | X_OK` and never `R_OK` (correctly: nothing on the store's path ever
  lists the directory), so a mode-0300 config directory takes every write and
  still refuses the read-only open.
- **A credential in *key* position is filtered out of validation errors, not
  documented as a gap.** `_safe_location` renders only the parts a validation
  location shares with the model's declared fields. "A rule with a stated
  limit beats a rule defended by machinery" applies where holding the property
  costs real complexity; here it costs one predicate.

## 3. Reviewing

`AGENTS.md` carries this now, but the measurement is worth keeping with the
task it came from: passing this brief to CodeRabbit as `-c` **suppresses**.
On step A's range it took two findings down to one, and the finding it hid was
the lost-update bug in amendment 1 — which the brief's "If you want to push
back" section had no view on at all. Run CodeRabbit without `-c` and reject
brief-contradicting findings by hand, where the rejection is reasoned and
reported.
