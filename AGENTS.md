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
- **Then check the assertion is answered by what the test set up.** Mutating
  the code will not find a test that passes for the wrong reason: that test
  still passes when the code is mutated, which is how it went unnoticed.
  Mutate what its premise rests on instead — for example, by weakening a
  compound constraint one clause at a time, replacing a setup step with a
  no-op, or deleting the operation whose failure the test is about.
- **A test that asserts an absence needs a premise it can observe.** For
  example, `assert secret not in output` passes for free whenever the fixture
  never produced the secret. The fixture has to record what it did, and the
  test has to assert on that record first. A record written beside the act
  rather than out of it is not enough: delete the act and the record is still
  there. Make the record depend on the act, so deleting the act deletes it.
- **A test states required behavior, never that the implementation matches
  its dependency.** Name no library in a test's name, docstring or comments,
  and never assert on a library's own output. Write the expectation as a rule
  about the domain — "the scheme's default port names the same origin as no
  port at all", not "httpx2 drops it". A dependency's quirk belongs in a
  comment at the call site that relies on it.
- **Say which tests pin a requirement and which pin current behavior** — an
  input the code declines to normalize, a gap left open on purpose — so a
  later change knows whether altering one is allowed or a regression.
- **Never make a real outbound network call**, in the suite or in a manual
  check, even where the sandbox would allow it. Use loopback fakes or a mock
  transport. `scripts/manual_verify.py` is the one deliberate exception: it is
  human-run, and stays that way.
- **Index a leak corpus by misparse shape, not by syntactic position.** A
  marked secret placed where a password belongs does not exercise the case
  where a password prefix is read as a port; that needs `8443/secret`.
  Enumerate the ways the parse can go wrong and put a marker in each resulting
  position.

## Secrets

The project's defining constraint: an access URL, a setup token, or a Basic
Auth password must not reach a log line, an exception message, or stdout.

`docs/ARCHITECTURE.md`'s "Cross-cutting: secrets and logging" lists the places
that actively defend this, and `url_validation.py`'s `UrlValidationError`
docstring states the rules for rendering a URL and where they deliberately
stop. Read both before touching anything credential-adjacent, and if you add a
path that handles a credential, add it there too.

**Check your change against every entry in that list, not only the entries it
appears to touch.** Each is a claim about the whole codebase — "the only place
a provider's text is passed on is X" — and a new log line in `merge.py` can
falsify one without going anywhere near the sentence that makes it.

## Comments and commit messages

- **Comments say why, not what**, and only where the reason is non-obvious. A
  comment restating the code is noise. Heavy comment density reads as
  intricacies standing in for principles; if a module needs that much
  narration, say what it is for in its docstring instead.
- **Size a comment to the code it explains, and open by naming its subject.**
  Long rationale is welcome where a reader would otherwise re-litigate a
  decision; a two-line helper does not need a five-paragraph justification.
- **Write for a reader who has only the file, not its history.** They cannot
  see what the code was, what else was considered, or what a reviewer said, so
  a comment leaning on any of it explains nothing to them. Tense is the
  checkable symptom: "now", "no longer", "any more", "used to", "instead of"
  all mean the sentence is addressed to someone watching a diff. Rejected
  alternatives and reviewer dialogue are the same mistake wearing different
  clothes — state what the code does and why it is right, and never hedge a
  deliberate improvement as a concession. The history has a home: the commit
  message, and for a finding's disposition, the reply to the reviewer. This
  governs `docs/` too. `docs/ARCHITECTURE.md` is a map of what is.
- **One fact, one home: the map points, it does not restate.** A fact goes in
  the lowest place that can hold it. Lowest is code that cannot be read any
  other way — a name, a type, a structure. Above that is a docstring or comment
  on the thing the fact is about, which is where a reader of that file will
  look. `docs/ARCHITECTURE.md` is for what no single file can hold: how two
  modules' guarantees depend on each other, an invariant that holds across
  them, a property of a configuration rather than of any code. A fresh decision
  feels like the most important thing about the system, which is the pull that
  puts it a rung too high — and then in the map *as well as* the code, since
  the map is the nearest blank space. Two tests can help with finding a second
  copy. *The move test*: a paragraph that could be cut and pasted into one
  module's docstring without losing anything belongs there. *The provenance
  test*: a paragraph written from the task's brief rather than from something
  read in the code is a decision record with no durable home yet, so place it
  by the ladder above rather than assume it is held — a brief is scratch and
  will be deleted, and a commit message answers why the code *changed*, not why
  it *is*.
- **Commit messages are for someone in `git log` asking why the code looks
  like this.** Lead with the point, organize by topic, and rewrite from the
  current state rather than appending each round's news.

## Writing things down

What you learn while working changes the work that is left, and the
conversation you learned it in will not be there when that work starts. Write
it where the work will look: a decision about the task in the task's own
prompt, a rule about working here in this file, a fact about the code in the
code or in `docs/ARCHITECTURE.md`. Amending the prompt is expected rather than
presumptuous — a brief that a decision has overtaken is worse than no brief,
because it still gets followed.

"I will keep that in mind for the next step" is not a plan. It is the moment to
stop and write it down.

## Working in steps

Work in numbered steps, and finish each one before starting the next. A step
lands as exactly one commit; inside it, commit as freely as the work needs,
since review tools read committed code. Those mid-step commits are scaffolding
for the tools — not a way to punctuate a conversation, and not something to do
once per exchange with the user.

Each step ends with this cycle:

1. **Self-review before presenting for human or agentic review.** Read your
   change in the context of the existing codebase, not against what you meant
   to write. For each delta, check whether its behavior differs from similar
   existing code — if it does, decide whether that is intended or a defect,
   and leave a test that pins it either way. When the change routes a new kind
   of value through shared code that guarantees something — a loader, a
   validator, an error path — re-check the guarantee against the new kind,
   since the existing tests only cover the kinds that predate your change.
   Check each delta against the rules in two documents, the task's prompt and
   `docs/ARCHITECTURE.md`. If there is a contradiction between the code and
   either document, then either the document is stale or the code is
   incorrect; fix whichever it is.

   **A step is not finished until `docs/ARCHITECTURE.md` describes the code
   the step leaves behind.** That is more than resolving contradictions. A
   sentence whose subject the change deleted is stale while every word in it
   stays true, and a guarantee the change made load-bearing in a new place
   belongs in the list that names it. So read the sections your delta touches,
   not only the ones it argues with. Describing is not retelling: "one fact,
   one home" above is how this obligation is discharged, and a new section is
   warranted only when the step introduced something no existing section is
   about. A brief may schedule a final read of the file as a whole, because
   steps that each land locally-correct prose can still add up to something
   repetitive or badly ordered. A brief that defers the describing itself is
   buggy, whatever it says in writing: fix the brief. A map that is wrong for
   three steps running is worse than no map, and every reviewer of every step
   will report the same stale paragraph.
2. **Present the work to the user, then stop.** This is a gate, not a
   courtesy: end the turn and wait for a reply. Their review routinely changes
   scope or direction, so a review launched first is spent on a version that
   is about to change — and AI reviewers are a rationed resource. A turn that
   presents work and then keeps working has not stopped, whatever it said
   while presenting.
3. **Have the step's diff reviewed by two independent AI reviewers**, both of
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

   **Do not pass the task's brief to CodeRabbit via `-c`.** It suppresses
   findings the brief has no view on, including ones its out-of-scope list does
   not cover. What keeps SSRF noise out is `.coderabbit.yaml`, which applies on
   every run regardless. A brief's "if
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

   The CodeRabbit CLI has a small per-window quota, and a step that goes
   several rounds will exhaust it. Wait the window out — queue the run and do
   something else — rather than shipping a step one reviewer short.

   Give a reviewer the *whole* brief as context, not just the step's
   paragraph. One that lacks the sentence saying a later step fixes something
   will report it as stale.
4. **Account for every finding**, one line each: what was claimed, whether it
   is true, and fixed / rejected-with-reason / deferred. Verify a finding
   against the code before acting on it or relaying it — confident-but-wrong
   findings are common, from both reviewers. Never quietly drop one.
5. **Give each reviewer a chance to answer a rejection**, put back to the
   reviewer that raised it so it keeps the context of its own finding: re-run
   the CLI with the rebuttal supplied via `-c` — the one place that flag
   belongs, since here you *want* the argument in front of it — and reply to
   an agent-shaped reviewer in its own thread. A disagreement is settled between you and the
   reviewer, not decided unilaterally and reported to the user as a fait
   accompli. One that concedes was wrong; one that holds its position with a
   new argument may be right, so read it before deciding. Report the exchange,
   not just the original verdict.
6. **Present the fixes and rejections to the user, and stop again** — with the
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
7. **Squash into one commit after sign-off**, with a message describing the
   step rather than the review rounds. Squashing belongs to the user's reply,
   not to your own judgment that every finding is handled.

## Terminology

- **"client" or "client app"**, never "consumer", for the app talking to this
  aggregator. Where "client" would be ambiguous with an HTTP client to a
  provider, write "client app".
- **The client app is generic.** Actual Budget is the motivating example, not
  a dependency and not a special case — never bake it into naming, logic, or
  assumptions about client behavior.
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
- **A property held by construction beats one held by discipline.** A store
  that keeps only digests cannot print a credential, so no rule about printing
  has to be remembered or enforced. Where the choice exists, arrange for there
  to be nothing to get wrong.
- **A rule with a stated limit beats a rule defended by machinery.** Where
  holding a property absolutely would cost real complexity, take the simpler
  code and document the gap where a reader will meet it.
- **Check a claim about a dependency by running it.** Reading the source is how
  the belief forms; executing it against a loopback fake is what settles it.
  This applies to a reviewer's reproduction too — verify the repro, not just
  the conclusion.

## Conventions

- Use American English spellings, not British English.
