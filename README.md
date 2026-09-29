# stilhawt-cli

**A typed, bounded command line for humans and AI agents.**

Every command is *declared* in a grammar (a YAML file): what it does, what it reads, the keys of
the objects it outputs, and its **effect** (`read`, `network`, `write`…). Commands emit JSON Lines;
**pipes** filter, sort, group, join and draw them. A line is checked **before anything runs**:
unknown stages, effects that are not allowed, unbounded repetitions and ungranted commands are
refused with the reason and the correct form.

It was built as the working tool of an AI agent in a large multi-project workspace — a small,
well-known vocabulary instead of ad-hoc shell one-liners — and it is just as usable by a human.

```text
stilhawt tools commands | where kind eq command | group namespace
stilhawt tools list | select module verb does effect | head 5
stilhawt explain tools commands | count | view tree        # the plan of a line, without running it
```

## Why

Ad-hoc shell commands are powerful and opaque: quoting traps, unbounded loops, output that has to
be parsed by eye, and no way to say *in advance* what a line will touch. `stilhawt` trades a little
expressiveness for four properties:

- **Typed objects, not text.** Every command declares its output keys (and their types); a verb
  that returns an object without a promised key fails loudly instead of feeding garbage downstream.
- **Effects are declared and enforced.** `read` changes nothing; `network` talks to a remote host;
  `display` writes a local page; `record` a snapshot; `write` / `deploy` / `device` act, and only
  under explicit locks. A line's effect is known before it runs (`stilhawt explain …`).
- **Everything repeating is bounded.** `each`, `map` and the AI pipes declare a maximum number of
  objects; above it the line is refused — never truncated in silence.
- **Refusals teach.** A wrong line gets the reason and the right shape, not a stack trace.

## Install

```bash
pip install stilhawt-cli              # or: pip install "stilhawt-cli[shell]" for tables and completion
stilhawt --check                      # lints the grammar: 0 grievance
stilhawt help
```

`stilhawt` alone opens an interactive shell (history, completion with the `shell` extra).
Outside a terminal (a pipe, a file, an agent) the output is always JSON Lines; `--json` / `--text`
force either one.

## The language in five minutes

| Stage | What it does | Example |
|---|---|---|
| `where` | keep objects matching one condition | `where effect eq read` |
| `select` / `sort` / `head` | project, order, cut | `sort -lines \| head 10` |
| `count` / `sum` / `group` | aggregates made by the TOOL | `group language sum lines` |
| `extract` / `replace` | sed-like, into fields, no model | `extract text "TODO: (?P<todo>.*)"` |
| `tee` … `join` | parallel branches, merged by key | `tee (where a gt 0) (where b gt 0) \| join id` |
| `each` | one READ command per object, `{}` = its value, bounded | `snip uses \| group session \| each session (snip uses --session {})` |
| `diff` | what changed since the last snapshot, by key | `diff mysnap --key id` |
| `view` | a local page: table, bars, tree, graph, document | `view graph namespace name` |
| `groq` | one generated line per object (or `--all`) | `groq "summarise in 8 words" --on description` |

Snippets are named lines: `stilhawt @commands-by-namespace`, `stilhawt explain @verbs`. The example
library is `stilhawt_cli/snippets/snippets.dsl.yaml`.

## Adding your own commands

A command maps a **tool verb**. A tool is a Python module that declares its verbs once:

```python
from stilhawt_cli.tool import Arg, Toolset

tools = Toolset("mypkg.inventory")

@tools.verb("hosts", output=["name", "role", "up"], types={"name": "str", "role": "str", "up": "bool"},
            args=[Arg("--role", help="only this role")])
def hosts(role):
    """the hosts of my inventory"""
    return [{"name": "web-1", "role": "web", "up": True}]
```

and the grammar names it:

```yaml
namespaces:
  inv:
    description: my inventory
    home: mypkg/inventory.py
    export: local
    commands:
      hosts:
        description: the hosts of my inventory
        effect: read
        pan: texte
        kind: tool
        tool: mypkg.inventory:hosts
        output: [name, up]
```

`stilhawt --check` verifies the mapping **both ways**: the verb exists, the command's output keys
are promised by the verb, the effect is the same on both sides.

## AI pipes and the egress guard

The engine ships **no model access** of its own. Transports, the egress guard and where the CLI
writes its files are **extension points** (`stilhawt_cli.ext`), filled by modules the grammar
names under `extensions:`. This package ships one: `stilhawt_cli.ext_groq`.

- The `groq` pipe calls the Groq API with **your** key (`GROQ_API_KEY`, read at call time, never
  stored). Model: `STILHAWT_GROQ_MODEL`.
- Every text that leaves goes through **the egress guard**. The shipped guard does **not**
  anonymise — it says so — and it **refuses** the pans `donnees` (raw data), `personne` (data about
  a person) and `image`; `texte` and `code` are sent as they are. Each field of the grammar says
  which pan it carries.
- **Fail-closed:** with no guard registered, every model pipe is refused.
- The answer of a model is **data**: it is added as a field, never executed.

To reach another model or anonymise before sending, write your own extension on the same pattern.

## Agents: mandates

An agent can be held to a **mandate** (`STILHAWT_MANDAT=<id>`): a declared list of the commands
and pipes it may run. A line using anything else is refused before it runs — the barrier is the
grant, not an instruction in a prompt. See the example `stilhawt_cli/mandats.dsl.yaml`.

## Where files go

`view` pages, `diff` snapshots and snippet uses are written under `STILHAWT_CLI_DATA`
(default `~/.stilhawt-cli`). An extension can register another place (`ext.data_dir`).

## Tests

Every module carries its own self-test, including cases that **must fail**:

```bash
python -m stilhawt_cli --selftest
python -m stilhawt_cli.ext --selftest
python -m stilhawt_cli.ext_groq --selftest
python -m stilhawt_cli.tool --selftest
```

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 StilHawt.
