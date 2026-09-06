# Future work

- Make claims one-time-use, dynamically generating username/password, and support revocation. This might allow moving all sensitive data out of config.toml.
- `claim --provider <key>`, so a claim can run non-interactively. It would be kind to also accept a case- and whitespace-insensitive spelling of the provider label, since nobody remembers whether it is Lunchflow, Lunch Flow or LunchFlow — but that means constraining labels, which is not worth it yet.
