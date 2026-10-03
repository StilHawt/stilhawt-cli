# Changelog

Every release of `stilhawt-cli`. The package is generated from a declaration and verified before it
leaves: a release whose README, CHANGELOG or tests disagree with the package is refused.

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
