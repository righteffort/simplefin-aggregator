# Working agreements

`simplefin-aggregator` is a single-user server that speaks [SimpleFIN protocol
v1](https://www.simplefin.org/protocol-v1.html) to a personal-finance client
app and proxies one or more SimpleFIN providers behind it. It holds
credentials that read the user's bank data, and most of its design follows
from protecting them.

`docs/ARCHITECTURE.md` is the map of the code — read it first, and update it
when a change would make it wrong. `README.md` is the operator-facing guide.
This file is how to work here; it does not restate either.

## Verification

After every change, all four:

```sh
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run pytest
```

A subset is not sufficient, however mechanical the edit. basedpyright runs a
zero-warning policy: fix each warning, or suppress it with
`# pyright: ignore[rule]` and a one-line reason. Tolerating one instance of a
warning category does not license the next one.

## Tests

- **Demonstrate a new test is non-vacuous**: mutate the code under test,
  confirm the test fails, restore. Clear `__pycache__` on both sides of that
  (`find . -name __pycache__ -type d -prune -exec rm -rf {} +`) — CPython
  validates cached bytecode by source size and mtime-in-seconds, so a
  same-length mutation restored within the same second can leave the mutant's
  bytecode in place and report a false pass.
- **A test states required behaviour, never that the implementation matches
  its dependency.** Name no library in a test's name, docstring or comments,
  and never assert on a library's own output. Write the expectation as a rule
  about the domain — "the scheme's default port names the same origin as no
  port at all", not "httpx2 drops it". A dependency's quirk belongs in a
  comment at the call site that relies on it.
- **Say which tests pin a requirement and which pin current behaviour** — an
  input the code declines to normalize, a gap left open on purpose — so a
  later change knows whether altering one is allowed or a regression.
- **Never make a real outbound network call**, in the suite or in a manual
  check, even where the sandbox would allow it. Use loopback fakes or a mock
  transport. `scripts/manual_verify.sh` is the one deliberate exception: it is
  human-run, and stays that way.
- **Index a leak corpus by misparse shape, not by syntactic position.** A
  marked secret placed where a password belongs does not exercise the case
  where a password prefix is read as a port; that needs `8443/secret`.
  Enumerate the ways the parse can go wrong and put a marker in each resulting
  position. Three corpora in this project missed a real leak by getting this
  backwards.

## Secrets

The project's defining constraint: an access URL, a setup token, or a Basic
Auth password must not reach a log line, an exception message, or stdout.

`docs/ARCHITECTURE.md`'s "Cross-cutting: secrets and logging" lists the places
that actively defend this, and `url_validation.py`'s `UrlValidationError`
docstring states the rules for rendering a URL and where they deliberately
stop. Read both before touching anything credential-adjacent, and if you add a
path that handles a credential, add it there too.

## Comments and commit messages

- **Comments say why, not what**, and only where the reason is non-obvious. A
  comment restating the code is noise. Heavy comment density reads as
  intricacies standing in for principles; if a module needs that much
  narration, say what it is for in its docstring instead.
- **Size a comment to the code it explains, and open by naming its subject.**
  Long rationale is welcome where a reader would otherwise re-litigate a
  decision; a two-line helper does not need a five-paragraph justification.
- **No rejected alternatives and no reviewer dialogue.** State what the code
  does and why it is right. A comment written to pre-empt an objection is
  addressed to someone who will never read it, and a "we considered X"
  narrative is history the next reader did not ask for. A review finding's
  disposition belongs in the reply to the reviewer and, if it changed the
  design, in the commit message. Never hedge a deliberate improvement as a
  concession.
- **Commit messages are for someone in `git log` asking why the code looks
  like this.** Lead with the point, organize by topic, and rewrite from the
  current state rather than appending each round's news.

## Working in steps

Work in numbered steps, and finish each one before starting the next. A step
lands as exactly one commit; inside it, commit as freely as the work needs,
since review tools read committed code. Those mid-step commits are scaffolding
for the tools — not a way to punctuate a conversation, and not something to do
once per exchange with the user.

Each step ends with this cycle:

1. **Present the work to the user, then stop.** This is a gate, not a
   courtesy: end the turn and wait for a reply. Their review routinely changes
   scope or direction, so a review launched first is spent on a version that
   is about to change — and AI reviewers are a rationed resource. A turn that
   presents work and then keeps working has not stopped, whatever it said
   while presenting.
2. **Have the step's diff reviewed by two independent AI reviewers**, both of
   them given the range and not the tip commit — asked to review "commit X" a
   tool may review that commit rather than the range ending at it. Commit
   first: both read committed code, so an uncommitted fix is reviewed as the
   stale tree it replaced.

   ```sh
   coderabbit review --agent --committed --base-commit <commit before the step>
   coderabbit review findings          # re-print them; the next run overwrites
   /code-review high <base>..<tip>     # say "the range A..B, not commit A"
   ```

   Use `--base-commit` for a commit on the current branch; `--base` expects a
   branch. The two reviewers are genuinely different tools, which is the
   point: CodeRabbit finds pattern and security defects, the second reads the
   surrounding code and runs things. `/code-review` is *not* CodeRabbit,
   whatever the plugin skill of that name suggests — and the
   `coderabbit:code-review` skill and `coderabbit:code-reviewer` agent are not
   a third opinion either, since both only wrap the CLI above.

   **Do not pass the task's brief to CodeRabbit via `-c`.** It suppresses.
   Measured on one step's range: two findings without it, one with, and the
   finding it hid was a major lost-update bug that the brief's out-of-scope
   list did not cover and had no view on. What keeps SSRF noise out is
   `.coderabbit.yaml`, which applies on every run regardless. A brief's "if
   you want to push back" section belongs in *your* rejection of a finding,
   where it is reasoned and reported — not in the reviewer's prompt, where it
   silently pre-empts. Expect to reject more findings by hand; that is the
   trade, and it is the right one.

   `.coderabbit.yaml` is load-bearing, not boilerplate. `profile: assertive`
   because the default `chill` suppresses lower-confidence findings and this
   repo would rather triage nits than miss one; `path_instructions` carrying
   the threat model and the out-of-scope list, which is what stops CodeRabbit
   proposing SSRF defenses every round. Extend the out-of-scope list when a
   round produces a finding it should have pre-empted.

   Give a reviewer the *whole* brief as context, not just the step's
   paragraph. Three reviewers independently reported the same stale
   `ARCHITECTURE.md` because none of them had the sentence saying a later step
   fixes it.
3. **Account for every finding**, one line each: what was claimed, whether it
   is true, and fixed / rejected-with-reason / deferred. Verify a finding
   against the code before acting on it or relaying it — confident-but-wrong
   findings are common, from both reviewers. Never quietly drop one.
4. **Give each reviewer a chance to answer a rejection**, put back to the
   reviewer that raised it so it keeps the context of its own finding: re-run
   the CLI with the rebuttal supplied via `-c` — the one place that flag
   belongs, since here you *want* the argument in front of it — and reply to
   an agent-shaped reviewer in its own thread. A disagreement is settled between you and the
   reviewer, not decided unilaterally and reported to the user as a fait
   accompli. One that concedes was wrong; one that holds its position with a
   new argument may be right, so read it before deciding. Report the exchange,
   not just the original verdict.
5. **Present the fixes and rejections to the user, and stop again** — with the
   review delta as something reviewable. The disposition list says what you
   claim changed; the diff says what did. `git diff <pre-review> <current>` is
   the minimum; better is a commit whose parent is the pre-review state and
   whose tree is the current one, which can be read, commented on and fed back
   to a reviewer like any other commit:

   ```sh
   git branch <name> $(git commit-tree <current>^{tree} -p <pre-review> -m <msg>)
   ```

   Keep the pre-review commit reachable from a tag or branch until sign-off —
   `git reset --soft` leaves it findable only through the reflog.
6. **Squash into one commit after sign-off**, with a message describing the
   step rather than the review rounds. Squashing belongs to the user's reply,
   not to your own judgment that every finding is handled.

## Terminology

- **"client" or "client app"**, never "consumer", for the app talking to this
  aggregator. Where "client" would be ambiguous with an HTTP client to a
  provider, write "client app".
- **The client app is generic.** Actual Budget is the motivating example, not
  a dependency and not a special case — never bake it into naming, logic, or
  assumptions about client behaviour.
- **"a provider the built-in list does not name"**, not "a self-hosted
  provider", for a config-supplied entry. Self-hosting is only one reason an
  entry is missing; a real third-party provider that is simply not built in is
  at least as likely, and the other wording tells that user the feature is not
  for them.

## Judgment

- **More code means more bugs.** Prefer the smaller design. Don't build
  framework-shaped abstractions for a single caller.
- **Take the cheap mechanism that answers the question** and let it be wrong at
  the margins — `stat` on a directory rather than writing a probe file to find
  out if it is writable. A check that is right in the ordinary case and wrong
  when someone else owns the file is fine here. Where a check would need more
  machinery than the code it checks, state the intent in a comment and leave it
  untested; a one-keyword fix does not earn a subprocess harness.
- **A rule with a stated limit beats a rule defended by machinery.** Where
  holding a property absolutely would cost real complexity, take the simpler
  code and document the gap where a reader will meet it.
- **Check a claim about a dependency by running it.** Reading the source is how
  the belief forms; executing it against a loopback fake is what settles it.
  This applies to a reviewer's reproduction too — verify the repro, not just
  the conclusion.
