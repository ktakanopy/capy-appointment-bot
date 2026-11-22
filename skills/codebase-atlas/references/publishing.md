# Publishing an atlas

Use this reference only when publication is requested or already authorized.
Generating an atlas or selecting a repository does not itself authorize a live
site update. Follow the project's established destination and deployment path.

## Prepare the hosted artifact

Render with `--source-base` pointing to a real revision of the repository and
`--guide-url` pointing to the matching guide. This keeps source links valid
when the HTML and SVGs are copied out of their original directory:

```sh
python3 /path/to/codebase-atlas/scripts/render_atlas.py \
  --root /path/to/repository --out /path/to/publish-directory \
  --web --project "Project name" \
  --source-base https://github.com/owner/repository/blob/REAL_COMMIT_SHA \
  --guide-url https://github.com/owner/repository/blob/REAL_COMMIT_SHA/docs/codebase-atlas/README.md \
  /path/to/repository/docs/codebase-atlas/flows.json \
  /path/to/repository/docs/codebase-atlas/files.json
```

Use an existing commit SHA; never invent a future revision. Include requested
previews beside the HTML/SVGs. Do not copy credentials, attachments, local
configuration or an entire application checkout into a static publication.

## GitHub Pages

Inspect the actual repository's Pages source, allowed deployment branches and
workflow before choosing a publishing path. A commit on a feature branch does
not deploy a workflow that only runs on another branch. A successful push also
does not prove that the deployment ran or that environment rules accepted it.

Use the existing allowed publishing branch or workflow when it fits the task.
For a static artifact branch, prepare changes in an isolated checkout and
preserve branch history and unrelated files. Keep application deployment and
merging implementation branches outside an atlas-only publishing request.

Do not widen deployment protection rules, expose private source or change
billing settings merely to finish publishing. Diagnose the actual failure and
use a supported, already-authorized path when available. For example, a
repository already configured for a static `gh-pages` branch may use GitHub's
managed branch publisher. This is conditional on that repository's settings;
it is not a universal replacement for custom Actions workflows.

If GitHub reports an account or billing lock, report the concrete rejection.
Do not keep retrying an unchanged blocked custom workflow or modify payment
information. Complete the prepared artifact and any unaffected verification;
if no authorized publishing path works, state what remains blocked.

## Verify the result

Wait for the relevant deployment to finish successfully, then open the public
URL. Confirm the new overview or changed card text, source links, inspector and
SVG rather than relying only on a workflow status. Account for stale browser
content by reloading after an update. Save a screenshot of the live result and
return the verified public link. Describe only what was actually deployed.
