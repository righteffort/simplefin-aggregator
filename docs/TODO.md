# Short-term fit and finish [agentic]
- `--config-dir` SHOULD BE A GLOBAL not buried in commands, sub-commands,
  sub-sub-commands, ...! It is applicable to practically all
  commands/subcommands subcommands (and can be ignored for any current or future
  commands that do not need it), and is nice to show to users as part of
  `simplefin-aggregator --help`.
- if feasible, when failing in `app.py:create_app` due to an unclaimed
  providers, show them all. Currently the app exits on the first one on the call
  to `_resolve_access_url` and the user encounters them one by one.
- why have labels for app tokens?
- cli app new help should state restrictions on keys for app tokens
- why have labels at all for custom providers?
- `cli.py:_probe_access_url`: 'Checking that the credentials work...' then
  silence on success !
- README: "Keep [`config.toml` and the config dir] readable" is both unhelpful
  (what's the impact) and informal ("goes double")

# Repairs to last round of agentic fit and finish
- reverse the decision to use `rich_markup_mode=None` and instead simply
  backtick the patterns. Also get rid of the overly clever factoring of
  `_KeyOption` when it is only relevant to `app new`.

# Short-term fit and finish [human]
- emphasize that `providers_creds.json` holds bearer tokens and what that means!
- a pointer in README to https://www.simplefin.org/protocol.html#app-quickstart
  for devs who want to smoketest simplefin-aggregator from the command line
  would be nice
- add a quickstart section to the top of the README
- maybe add an actualbudget+lunchmoney quickstart section to the top of the
  README

# Medium-term fit and finish
- ship a docker image
- maybe: provide an example docker_compose.yml .. currently in .notes/docker-compose-example.yml

# Other future work

- `claim --provider` and the `app` commands' `--key` both take a key. Accepting
  a label instead would be kind — nobody remembers whether it is Lunchflow,
  Lunch Flow or LunchFlow — but matching one case- and whitespace-insensitively
  means constraining labels, and an app's label is free text.
- review language for 'attacker', 'hostile', etc. and make sure they are not
  being used when 'misbehaving' would be more accurate.
- maybe in `cli.py:claim` show asterisks instead of nothing (`hide_input=True`
  behavior). IIUC once python3.13 is deprecated we can use a python3.14 to help
  with this, if true leave a TODO behind to simplify
