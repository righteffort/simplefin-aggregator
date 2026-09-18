# Brief: short-term fit and finish (agentic)

This is the brief for a run of seven steps taken from the "Short-term fit and
finish [agentic]" section of `docs/TODO.md`. An orchestrating session spawns one
agent per job below and amends this file when a decision changes. Read
`AGENTS.md` and `docs/ARCHITECTURE.md` before starting; this brief only records
what they do not already say.

## How this run differs from AGENTS.md

The user asked to be minimally involved, so **human review is skipped**:

- The cycle's step 2 ("present to the user, then stop") and step 6 ("present the
  fixes and rejections, and stop again") do not apply. Report to the
  orchestrator instead, and keep going.
- **CodeRabbit is not used in this run** (the user's decision for this run
  only; `AGENTS.md` is unchanged). The review is `/code-review` alone.
- The rest of the cycle still applies in full: self-review, the AI reviewer on
  the range, a disposition line for every finding, a rebuttal round
  for every rejection, a review-delta commit, and a squash into one commit.
- Squash once the review cycle is complete, without waiting for sign-off. Keep
  the pre-review state reachable on a branch named `ff-<N>-review-history` (the
  repo already follows this pattern: `step-a-review-history`), with the
  review-delta commit on top, so the user can inspect it afterward.
- **Escalate instead of deciding** when there is a real impasse: a
  contradiction between this brief and the code or `AGENTS.md`, a reviewer who
  holds its position with an argument you cannot answer, or a design question
  this brief does not settle and that the user would plausibly care about. Stop
  that step and report what the question is; do not guess.

## Ground rules for every step

- Work on `dev`. **Never push.** Never rewrite a commit from an earlier step.
- **Do not stage or commit `docs/TODO.md`.** It is the user's. Its "Repairs to last
  round" section is **not** part of this run: do not act on it. Stage files by name, never `git add -A` / `git add .` / `git commit -a`,
  and never `git stash`.
- Each step lands as exactly one commit on `dev`, on top of the previous step.
- Run all four verification commands after every change (`AGENTS.md`,
  "Verification").
- No backward compatibility is wanted anywhere in this run: the project is
  0.1.0 with one user.
- Commit messages end with the attribution lines:

  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HRz33HFPkKDpLwyfYmw1n1
  ```

## Jobs per step

Each step is two agents in sequence, with the orchestrator's own read of the
diff in between.

**Implementation agent.** Implements the step, runs the four checks, does the
AGENTS.md self-review, commits one commit, and reports. It does not run the AI
reviewers.

**Review agent.** Given `BASE` (the commit before the step) and `TIP` (the step
commit), it:

1. Tags the pre-review state: `git branch ff-<N>-review-history TIP`.
2. Runs the `/code-review` skill at level `high` on "the range BASE..TIP, not
   commit BASE" (invoke it with the Skill tool, `skill: "code-review"`), giving
   it the whole brief as context.
3. Verifies each finding against the code, running things where that settles
   it, and records a disposition: claim, true or not, fixed / rejected with
   reason / deferred.
4. Puts every rejection back to the reviewer: re-invoke `/code-review` with the
   rebuttal and the finding. A reviewer that concedes was wrong; one that holds with a new
   argument gets read and reconsidered. An unresolved disagreement is an
   escalation, not a unilateral call.
5. Commits the fixes (four checks clean), then records the review delta on the
   history branch: `git branch -f ff-<N>-review-history $(git commit-tree
   HEAD^{tree} -p TIP -m "Step <N> review fixes")`.
6. Squashes everything since `BASE` into one commit on `dev` with a message
   describing the step, not the review rounds.
7. Reports: the squashed hash, the disposition list (one line per finding,
   including the rebuttal exchanges), and anything escalated.

## Steps

### 1. `--config-dir` is a global option

- Move `--config-dir` from every subcommand to the top-level `simplefin-aggregator`
  callback, so it shows in `simplefin-aggregator --help` and is written
  `simplefin-aggregator --config-dir DIR <command> ...`. It is **not** accepted
  after a subcommand as well.
- Also read it from the environment variable `SIMPLEFIN_AGGREGATOR_CONFIG_DIR`,
  with the flag taking precedence. The help text should mention the variable.
- A command that does not need the config dir ignores it.
- Update every caller: the `Dockerfile` (set
  `ENV SIMPLEFIN_AGGREGATOR_CONFIG_DIR=/config` and simplify `CMD` to
  `["serve"]`, so `docker run ... claim` works with no flag), the README
  (including the Docker section), `scripts/manual_verify.py`, and
  `docs/ARCHITECTURE.md` (headings such as `serve [--config-dir DIR]`).
- Tests: the flag, the environment variable, and that the flag wins.

### 2. Startup reports every unresolved provider at once

`app.py:create_app` resolves each provider's access URL with
`_resolve_access_url` and stops at the first failure. Make it gather the
failures for **all** configured providers, covering both kinds (no access URL
stored, and a stored access URL that fails validation against the provider's
root), and report them together in one error before uvicorn starts. `serve`
prints them all and exits non-zero. Keep the secrets rules: the only text per
provider is what the existing errors already render. Update the
`create_app` docstring, which currently says only the first is named, and
anything in `docs/ARCHITECTURE.md` that says the same.

### 3. Remove app token labels

Client apps have no label: the key is the name. Remove `--label` from
`app new`, `label` from `UnclaimedAppToken`/`ClaimedAppToken` and
`new_app_token`, the LABEL column from `app list`, and every mention in the
README, `docs/ARCHITECTURE.md`, `scripts/manual_verify.py`, and the test
support code. **Do not** write a test pinning that an old store file with a
`label` field still loads; the user explicitly declined that. The "Other future
work" bullet in `docs/TODO.md` that calls an app's label free text goes stale
with this step, but this run does not touch that file: mention it in your report.

### 4. A custom provider's label is optional

In `[[custom_providers]]`, `label` becomes optional and defaults to the key.
Built-in providers keep their labels (`provider_registry.py`). Update the
README, the `config.toml` template comment, and `docs/ARCHITECTURE.md` as
needed. Test that an entry without a label is shown by its key where a label
would appear (the claim menu).

### 5. CLI polish

- `cli.py:_probe_access_url` prints "Checking that the credentials work..." and
  then nothing when the check succeeds. Print a short success line on stderr.
- `app new --help` renders the key option's help as "The app's key, matching +."
  because Typer's rich help formatting reads `[a-z0-9-]` as a markup tag and
  drops it. Fix it so the pattern is shown, check every other help string and
  command docstring for the same problem, and give `claim --provider` the same
  statement of the key rule. Choose between escaping and turning rich markup
  off by whichever is smaller and holds by construction; say which in the
  commit message. A test pins that the rendered help shows the pattern.
- The setup-token prompt in `claim` keeps Typer's default hidden input. Masking
  with asterisks was considered and dropped; do not change it.

### 6. README: config file permissions

Rewrite the README paragraph beginning "**`config.toml` holds no
credentials.**" (and check the similar "keep it readable only by you" sentence
about `provider_creds.json`). The current text tells the reader to restrict
*reading* but explains only a *writing* risk, and its tone is informal ("The
same goes double"). Say the impact plainly and **succinctly**: this is not a
security treatise. For `config.toml` and its directory, one or two sentences on
what someone who can write them can do is enough. Do not let this outweigh the
much easier and more important exposure, read access to the bearer tokens in
`provider_creds.json`; that emphasis is the user's own separate task, so do not
write it, but do not bury it either. Match the file's existing voice.

### 7. The directory is chosen only by `SIMPLEFIN_AGGREGATOR_DIR`

This supersedes step 1's design, which has already landed as its own commit;
do not rewrite that commit. The user decided after step 1 that a flag is not
needed.

- Remove the `--config-dir` option and the top-level callback and context
  plumbing that exists only to carry it. The directory comes from the
  environment variable `SIMPLEFIN_AGGREGATOR_DIR`, else the platformdirs
  default. `SIMPLEFIN_AGGREGATOR_CONFIG_DIR` goes away (no compatibility).
- The top-level `simplefin-aggregator --help` names the variable and the
  default directory.
- The variable is invisible state, so the directory in use is named where it
  matters: `serve` states it once at startup (stderr), `app new` and
  `app regen` name the store they wrote, and `claim` keeps naming the store
  path as it does. Keep this terse — one line each, no new noise elsewhere.
- Update every caller and doc: `Dockerfile` (`ENV SIMPLEFIN_AGGREGATOR_DIR=/config`),
  README (setup, Docker section, every example that passed the flag),
  `scripts/manual_verify.py`, `docs/ARCHITECTURE.md` (the On-disk state
  section and the flow headings), and the tests (the CLI tests pass the
  variable through the runner's environment, isolated from the developer's own
  environment).
- Tests: the variable selects the directory, the default applies when it is
  unset, and the commands that should name the directory do.
