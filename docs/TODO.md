<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Short-term fit and finish [human]
- emphasize that `providers_creds.json` holds bearer tokens and what that means!
- README: "`config.toml` holds no credentials ..." is unhelpful: threats are unclear.
- a pointer in README to https://www.simplefin.org/protocol.html#app-quickstart
  for devs who want to smoketest simplefin-aggregator from the command line
  would be nice
- add a quickstart section to the top of the README
- maybe add an actualbudget+lunchmoney quickstart section to the top of the
  README
- ARCHITECTURE.md is chock-full of reciting what the code does rather than
  explaining principles, structure, why is the way it is, etc.
- In general the prose is terrible.

# Medium-term fit and finish
- ship a docker image righteffort/simplefin-aggregator
- ship a python package similarly
- maybe: provide an example docker_compose.yml .. currently in .notes/docker-compose-example.yml

# Questions
- Do we check permissions on the chain of directories from a symlinked config file to the real file?
- Why is
  `test_url_validation.py:test_mismatch_message_withholds_the_setup_token`
  docstring "The paste-error case..." ?

# Other future work

- review language for 'attacker', 'hostile', etc. and make sure they are not
  being used when 'misbehaving' would be more accurate.
- maybe in `cli.py:claim` show asterisks instead of nothing (`hide_input=True`
  behavior). IIUC once python3.13 is deprecated we can use a python3.14 to help
  with this, if true leave a TODO behind to simplify
