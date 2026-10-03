# Changelog

Every release of `stilhawt-cli`. The package is generated from a declaration and verified before it
leaves: a release whose README, CHANGELOG or tests disagree with the package is refused.

## 0.3.2 — 2026-10-03

- **Private keys never leave, `--on` included**: `--on _secret` is now refused before anything is sent.
  Until 0.3.1 the README promised it, but the filter only applied when `--on` was absent.
- **An interruption stops everything**: Ctrl-C (or a timeout) now stops the command *and* every process
  it started. Before, only the front process stopped and the work went on behind it.
- **Refusals instead of tracebacks**: `git hunks` without git installed, and `--max abc` on `groq` / `jev`.
- **`where` explains an empty result** when the cause is the line, not the data: comparing text with a
  number, or filtering on the field of a model that answered nothing.
- **README**: `grep`, `min`, `max` and `open` were missing. The 0.3.1 note below claimed every command and
  pipe was documented; that was wrong — the release check counted a name found inside an ordinary word
  (`min` in "minutes"). It now requires the name as a whole word in a code span.

## 0.3.1 — 2026-10-03

- **README** documents every public command and pipe: `git hunks` and `view diff`, `snip list` /
  `snip uses`, `flatten`, `avg`, `notify` — 0.3.0 shipped some of them without a word.
- **Tests**: the README lists every module whose self-test the release replays.
- This changelog.

## 0.3.0 — 2026-10-03

- **Anonymous usage telemetry**, opt-out — command words only, never their arguments; a random
  installation id; off with `STILHAWT_TELEMETRY=0`, `DO_NOT_TRACK=1` or in CI; nothing is sent on the
  first run. See [Telemetry](README.md#telemetry) and `stilhawt_cli/telemetry.dsl.yaml`.
- **`git hunks`** — one row per hunk of a diff — and **`view diff`**, a fold/unfold page (`--live`
  re-reads the working tree).
- Project links: [stilhawt.com](https://stilhawt.com) in the README and the package metadata.

## Earlier versions

0.2.0 to 0.2.2: see the tags of the repository.
