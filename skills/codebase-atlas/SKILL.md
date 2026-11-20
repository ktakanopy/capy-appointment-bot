---
name: codebase-atlas
description: Map a repository into source-linked SVGs or an interactive HTML atlas of file and class responsibilities, runtime callers, entry points, and happy and failure flows. Use for codebase walkthroughs and visual architecture references.
---

# Codebase Atlas

Start at the executable boundaries the user cares about, such as routes, job
consumers, CLIs and public library calls. Verify them in configuration and
source. Follow each scenario through its normal entry point, then walk the
branches where processing can stop, retry, or leave an uncertain outcome.

- Inventory source files and top-level classes within the agreed scope. Give
  each a concise purpose and at least one place to start reading. For a data
  class, protocol, or row mapping, link its declaration. For a behavioral
  class, link the method that anchors the scenario when there is a clear one.
- Trace calls through adapters, framework registration, queues and stores.
  Verify dynamic edges against the wiring. Treat imports as leads for source
  inspection. Label queues, stored checkpoints, and callback boundaries where
  control passes asynchronously or through a framework.
- Use **File connections** in the HTML to explore direct imports among cataloged
  Python files. Import arrows point from importer to dependency and link to
  the statement line. Use a code index such as Codebase Memory for deeper
  caller and callee exploration when available.
- Draw the overview and major flows separately. Name the trigger, decisions,
  transaction boundaries, durable effects and recovery outcome that matter to
  each scenario. Link each labeled code location to its source. Preserve the
  distinction between a completed business write and delivery of its reply.
- Explain why each step exists. Use the code or a nearby test to identify the
  dependency it satisfies, the invariant it preserves, or the failure it
  avoids. Write the rationale separately from the step description.
- Keep node IDs and edges in editable source data so the same diagrams can be
  loaded into the interactive canvas. Check text, arrow routes, source links
  and the documented coverage in the requested formats. For the HTML view,
  also exercise graph controls, navigation, and source inspection.

## Renderer

The bundled Python 3.11+ renderer reads hand-authored JSON and resolves Python
symbols to current source lines using the AST. It checks references without
importing the application. Put each path, card label, behavior description,
and edge in the inputs.

```sh
python3 path/to/codebase-atlas/scripts/render_atlas.py \
  --root path/to/repository --out path/to/repository/docs/atlas \
  path/to/atlas.json
```

Add `--web` to generate a standalone `index.html` alongside the SVGs. The
HTML embeds the referenced source files for its code reader. Open it directly
in a browser. Use `--project` to set the project name. When publishing a static
copy, use `--source-base` for a revision-specific source URL and `--guide-url`
for the hosted guide link.

Each input is an array of views. A flow view needs `id`, `title`, `subtitle`,
`columns`, `lanes`, and `edges`. A lane has a `label` and a `nodes` array.
Each node has `id`, `title`, and `detail`. Set `why` to show its rationale in
the HTML inspector. Use `ref` such as
`src/api.py#Controller.handle` and `tone` of `entry`, `data`, `success`, or
`failure`. Use `null` for an empty column. Edges are
`["source", "target"]` or `["source", "target", "async"]`.

For a linear end-to-end example, connect the turns in one view and add a
`transcript` array of objects with `node` (a node ID), `speaker`, and `text`.
Use `transcriptContext` to identify any fixture, date, or other assumption
needed to read the conversation.

A reference view has `id`, `title`, `subtitle`, and `files`. Each file row is
`[path, purpose, [entry_symbol, ...], [class_row, ...]]`. Put the preferred entry
first. Use an empty string for a file-level entry. Each class row is
`[class_name, purpose]` or `[class_name, purpose, method_name]`. Use nested
paths and dotted Python symbols as they appear in source. For other languages,
link the file and put the entry symbol in the card text.
