---
name: codebase-atlas
description: Build or update a source-linked codebase atlas with editable flow definitions, SVG diagrams, and a standalone interactive HTML explorer. Use for repository architecture tours, runtime walkthroughs, and file or class maps.
---

# Codebase Atlas

Reproduce the atlas as a self-contained HTML explorer and linked SVG diagrams,
using the bundled renderer and UI. Keep each repository's architecture and copy
in editable JSON. Preserve existing view and node IDs when updating an atlas.

Resolve scripts, references and UI assets relative to this skill's directory.
Pass the target repository explicitly with `--root`; the skill may be installed
outside that repository. Python 3.11+ and its standard library are sufficient.
No frontend build, application imports or server are required to view the result.

## Ground the atlas in the project

Start at the relevant executable boundaries: a route, command, consumer or
public API. Inspect source, registration and configuration, then follow an
actual scenario through its callers, storage, external operations and result.
Use a code index when available; verify dynamic edges in the source.

Make the first flow an architecture overview, because it opens by default.
Explain where requests enter, which components act, where state is saved and
how results reach the caller. Include coordination and recovery only where
the project has them. Keep implementation detail in focused flows and the
inspector. Do not copy another project's queues, worker counts or model stack.

Separate observed behavior from planned or historical architecture. Separate
committed business changes, processing completion and external delivery where
those are distinct. Tests demonstrate the behavior they exercise; a configured
worker count or a stubbed benchmark does not establish production capacity.

## Write cards for a new reader

- Name a concrete action or recognizable component in the title.
- In `detail`, say who acts and what happens to the request or record.
- In `why`, explain the specific outcome or failure this step addresses.
- Define necessary terms at first use. Labels such as "intake", "claim",
  "head", "slot" or "checkpoint promotion" need a plain-language explanation.
- Avoid compressed implementation notes such as "claims use the same slot"
  or "broker startup is never a gate on durable-work recovery".
- Keep titles short enough for diagram cards. Move supporting explanation to
  the inspector or a focused flow instead of removing meaning to save words.

Read [authoring.md](references/authoring.md) when preparing the JSON. It covers
source references, navigation metadata, file/class inventories, transcripts,
and an example of replacing jargon with a clear action and rationale.

## Author the editable inputs

Follow the repository's documentation destination, normally
`docs/codebase-atlas/flows.json` and `files.json`. Choose the views that explain
the requested scope: overview, an end-to-end example, relevant failure/recovery
paths and a grouped file/class reference. Add tests or evaluations when they
are useful evidence for those paths, rather than inventing a fixed view list.

Each meaningful node needs a purpose and rationale. Link it to the actual
function or source location when one exists. Infrastructure and concept nodes
may omit a source reference. Never invent files to make a node clickable.

Set project-specific `navTitle` and `icon` values in the JSON when needed.
The UI has no built-in names for a particular repository. Put the preferred
reading entry first in each file's entry list; the first cataloged file is
also the initial selection in File connections.

Write a short atlas guide beside the generated files, normally `README.md`,
with the reading order, scope, regeneration command and any source-link limits.
The HTML's Atlas guide link defaults to that file; use `--guide-url` when the
repository has another guide location. The renderer does not create the guide.

## Generate the atlas

Run the bundled script against the target repository and its authored inputs:

```sh
python3 /path/to/codebase-atlas/scripts/render_atlas.py \
  --root /path/to/repository \
  --out /path/to/repository/docs/codebase-atlas \
  --web --project "Project name" \
  /path/to/repository/docs/codebase-atlas/flows.json \
  /path/to/repository/docs/codebase-atlas/files.json
```

The renderer validates source references, embeds source snapshots, and writes
one SVG per view plus `index.html`. Python definitions and direct imports are
resolved with the AST without importing the application. Other text languages
support file references and explicit `#L12` line references; their definitions
and imports are not inferred. Recheck explicit line numbers after source edits.

Regenerate after final source or JSON changes. Render an SVG preview or take a
browser screenshot when a directly viewable image is useful; Mermaid alone
does not reproduce this atlas. Avoid adding a frontend stack for a preview.

## Verify and deliver

Run `scripts/test_render_atlas.py` after changing the bundled renderer. Check
that all referenced paths and symbols resolve and that existing bookmarks
survive an update. Review the opening overview before the detailed flows.

Open the generated HTML at a desktop viewport and a mobile viewport around
390 × 844, or the user's specified size. Exercise search, node selection,
inspector, full source reader, file/class navigation, pan/zoom and Open SVG.
Check transcripts when provided. Essential controls and inspector paragraphs
must remain readable; detailed diagrams can use pan/zoom. Inspect the SVGs for
clipped titles, crowded text and misleading arrow routes. Capture the actual
rendered result and correct visible problems before delivery.

Deliver the editable inputs, generated HTML/SVGs, a useful preview and the
regeneration command. Keep the bundle reusable by leaving project-specific
copy and paths in the target repository's JSON, not in the shared UI.

Publishing is a separate action unless already requested. If authorized,
read [publishing.md](references/publishing.md), use the established hosting
configuration, and verify the live content after deployment succeeds.
