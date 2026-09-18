# User-facing terminology updates

## CLI and help strings

Overall help:

### `client` subcommand

- Overall help: "Manage clients."
- `client add`.
- `client revoke`.
- `client list`. List the clients, their creation time, and last access time.

User-facing states: 

## README.md

pyproject.toml


### Future

Add `provider` command in place of instead of hand-editing config.toml and `claim` command. Help: "Manage providers."

- `provider add [provider]`. Exchange a setup token from a provider for an access URL, and add entry for provider to state.
- `provider remove [provider]`. Remove a provider; inform user that this does not revoke access at the provider. 
- `provider list`. List the providers (no other information).

Note to use a custom providers, it must be added to `config.toml` and the server restarted.
