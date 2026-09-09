# Future work

- `GET /create`, the browser flow a real provider offers for minting a setup
  token. `app new` is this application's equivalent and is the right shape for
  a single-user server on loopback, so this is only worth building if something
  needs the standard flow.
- `claim --provider` and the `app` commands' `--key` both take a key. Accepting
  a label instead would be kind — nobody remembers whether it is Lunchflow,
  Lunch Flow or LunchFlow — but matching one case- and whitespace-insensitively
  means constraining labels, and an app's label is free text.
