# Future work

- `claim --provider` and the `app` commands' `--key` both take a key. Accepting
  a label instead would be kind — nobody remembers whether it is Lunchflow,
  Lunch Flow or LunchFlow — but matching one case- and whitespace-insensitively
  means constraining labels, and an app's label is free text.
- review language for 'attacker', 'hostile', etc. and make sure they are not
  being used when 'misbehaving' would be more accurate.
- very optional: modify manual_verify to fetch a token from
  https://beta-bridge.simplefin.org/info/developers by looking for a string of
  80 or more `[0-9a-zA-Z]` characters
