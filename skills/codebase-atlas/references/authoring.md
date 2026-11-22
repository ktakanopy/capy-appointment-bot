# Authoring atlas inputs

## Choose a reading path

Use the overview to answer the reader's first questions before they open code:
what starts the work, what receives it, which records matter, which component
acts next, and where the result goes. Focused views can explain a real request,
a failed dependency, a retry or an interrupted operation. Follow the actual
code; a queue, database or background worker is not required in every project.

Use a source reference at the step that owns the behavior. A static import
shows a code dependency; it does not prove a runtime call. Follow registration,
framework callbacks and adapters when checking runtime edges.

## Flow shape

Each input file contains an array of views. A flow has this shape; these source
paths illustrate the format and must be replaced with inspected project code:

```json
[
  {
    "id": "architecture-overview",
    "title": "How a request reaches a saved result",
    "navTitle": "Architecture overview",
    "icon": "atlas",
    "subtitle": "Follow the request through validation, processing and its stored result.",
    "columns": 3,
    "lanes": [
      {
        "label": "Receive and handle the request",
        "nodes": [
          {
            "id": "receive",
            "title": "Receive a request",
            "detail": "The API checks the submitted fields before calling the service.",
            "why": "Invalid input must be rejected before it can change saved data.",
            "ref": "src/api.py#submit",
            "tone": "entry"
          },
          {
            "id": "process",
            "title": "Apply the business rules",
            "detail": "The service validates the operation and asks the repository to save it.",
            "why": "All entry points must apply the same rules before changing the record.",
            "ref": "src/service.py#Service.create"
          },
          {
            "id": "save",
            "title": "Save the result",
            "detail": "The repository saves the record and returns its identifier.",
            "why": "The caller receives an identifier for a result that was actually saved.",
            "ref": "src/repository.py#Repository.save",
            "tone": "data"
          }
        ]
      }
    ],
    "edges": [["receive", "process"], ["process", "save"]]
  }
]
```

- `id` uses lowercase letters, digits and hyphens. View IDs must be unique.
  Keep view and node IDs stable because bookmarks use `#view-id/node-id`.
- `title` is the full title used in the SVG. Optional `navTitle` is a shorter
  label for the HTML navigation and heading; it otherwise uses `title`.
- Optional `icon` names an icon from `assets/web/app.js`; omit it to use the
  atlas icon. Unknown icons fall back to that icon.
- Each lane is one diagram row. Put one node or `null` in each column; an
  optional node `column` selects a column explicitly. Node IDs must be unique
  within the flow. Every edge endpoint must name a node in that flow.
- `tone` is `entry`, `data`, `success`, `failure` or `normal` (the default).
- `["from", "to"]` is a normal call/flow; `["from", "to", "async"]` marks a
  notification, delayed continuation or framework handoff. Explain the real
  boundary in the card text rather than relying on arrow style alone.
- SVG cards have a fixed width. The renderer rejects details longer than three
  wrapped lines. Fit titles visually; use fewer columns, shorter titles or a
  focused view when needed. Put fuller explanation in `why`.

## Source references

Paths are relative to `--root` and must resolve inside that repository.

| Reference | Behavior |
| --- | --- |
| `src/worker.py#run` | Resolve a top-level Python function to its current line |
| `src/service.py#Service.create` | Resolve a top-level Python class method |
| `src/contracts.py#Request` | Link a Python data class or type declaration |
| `src/worker.py` | Link a whole source file |
| `src/client.ts#L12` | Link an explicitly checked line in any UTF-8 text file |

Python symbol resolution covers top-level functions/classes and their direct
methods. Nested definitions, assignments and other languages can use a file or
explicit line reference. An out-of-range line fails generation, but a line
that still exists may point at the wrong code after an edit: inspect it again.
Class rows require Python symbols; describe other-language classes in the file
purpose and use verified file or line entries.

Omit `ref` for an infrastructure concept with no defining source. Source code
is embedded in the HTML, so inspect the referenced files for secrets or private
fixtures before distributing or publishing an atlas.

## File and class reference

A catalog uses `files` instead of `lanes`. Group files into areas that match the
project. Each row contains path, purpose, entry symbols and class descriptions:

```json
[
  {
    "id": "files-runtime",
    "title": "Source reference · Request processing",
    "navTitle": "Request processing",
    "subtitle": "Start with the request handler, then follow its service and repository.",
    "files": [
      ["src/api.py", "Validate incoming requests and call the service.", ["submit"], []],
      ["src/service.py", "Check business rules before saving a record.", ["Service.create"], [
        ["Service", "Owns the checks shared by request handlers.", "create"]
      ]],
      ["src/client.ts", "Send requests and handle the returned identifier.", ["L12"], []]
    ]
  }
]
```

Use `[""]` for a file-level entry. Class rows are `[name, purpose]` for a
declaration or `[name, purpose, method]` for a behavior entry. Inventory relevant
files and top-level classes within the agreed scope. Do not imply complete
repository coverage if intentionally showing only one subsystem.

The HTML File connections view follows direct imports among cataloged Python
files. Import arrows point from importer to dependency and open the import
statement. It is not a call graph or a cross-language dependency analyzer.

## Example of clearer card copy

For a project with saved messages and a notification queue, this is too terse:

```json
{
  "title": "Recover without RabbitMQ",
  "detail": "Startup and periodic PostgreSQL claims use the same slot.",
  "why": "Broker startup is never a gate on durable-work recovery."
}
```

A new reader has to guess what is recovered, who checks, what a claim or slot
means and why it matters. A clearer version is:

```json
{
  "title": "Find waiting messages",
  "detail": "At startup and about once a second, a free processor finds pending messages.",
  "why": "Saved messages still get processed if RabbitMQ is down or a notification was lost. A busy processor finishes its current message before checking again."
}
```

The timing and technologies are facts of this example, not defaults for other
projects. Derive a new project's description from its source. Likewise, "take
the next message in this chat" can explain a chat claim, and "receive and save
the message" can explain intake. Name the records and actor instead of merely
substituting one unexplained technical term for another.

## Transcript and walkthrough

For a linear conversation or other request/reply example, add a `transcript`
array to its flow. Each item has `node`, `speaker` and `text`; `node` must name
the corresponding node. `speaker` also labels the inspector excerpt, so use
an actor or artifact name that fits the project. Optional `transcriptTitle`
changes the dialog heading from "Example walkthrough" to a suitable label such
as "Example conversation" or "Recorded command run". Use `transcriptContext` for synthetic dates, fixture
assumptions or other context. Example text should match the referenced fixture
or be labeled illustrative. Keep approval, business commit, processing
completion and external delivery separate when the code separates them.
