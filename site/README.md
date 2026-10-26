# Cueverge static site

The public-facing landing page and documentation site, kept separate from the
controller's authenticated browser application. Building or opening this site
does not connect to the controller or operate hardware.

Requires Node.js 22 or newer, npm, and Python 3 (preview and link checks).

```bash
cd site
npm ci
npm run build
npm run check
npm run preview
```

Open <http://127.0.0.1:4173>. The generated `site/dist/` directory can be hosted
by any static web server, including under a subdirectory such as `/cueverge/`.
It requires no server-side runtime, client-side router, or external font service.
This change does not configure or publish hosting.

## Editing

- `index.html`: landing-page content (inserted into the shared HTML shell).
- `styles.css`: shared styling, based on the application's navy, blue, and cyan
  palette in `web/src/styles.css`.
- `build.mjs`: shared page shell, Markdown rendering, navigation, and link rewriting.
- `../docs/`: canonical documentation sources and screenshots. Edit the guides
  here, then rebuild; do not edit generated HTML or maintain duplicate guides.

Use fictional, clearly labeled examples for public documentation. Describe the
capability with sample resources, geometry, and visitor responses; avoid personal
installation inventories, measured calibration results, network addresses,
hostnames, and storage paths. Use documentation-only addresses and example
domains for deployment snippets. Keep real UI labels and implementation limits
accurate, and distinguish examples from hardware qualification evidence.

The **User guide** presents `docs/show-creator-guide.md`, the creative capability
reference. The **Operator guide** presents `docs/operator-guide.md`, the browser
setup, authoring, and daily-operation walkthrough. The service administration
guide has its own page. All other Markdown references under `docs/` are rendered
as well, preserving their directory structure and section anchors.

Documentation links to rendered Markdown remain within the site. Images are
copied from `docs/assets/`. Repository files, examples, and source directories
link to GitHub. Existing Markdown paths remain valid for GitHub readers and
contributors. The build intentionally copies only documentation assets, never
private show packages or local controller settings.

## Validation

`npm run check` checks every built page for broken local links, missing section
fragments, duplicate IDs, and missing image descriptions. CI builds the site and
runs this check. When changing layout, also inspect the landing page and long
guides at desktop and mobile widths; tables and code should scroll within the
article rather than widening the whole page.
