# Public repository audit

Audit date: 2026-10-26. Scope: current tracked files, nonignored untracked
files, all locally available Git refs, dependency lockfiles, Git/Docker
exclusions, build checks, and public-facing documentation. Existing authoring
and documentation work was preserved. No commits, pushes, visibility changes,
or history rewrites were performed.

## Publication blockers

- **Personal email in Git history.** The owner requested that this block
  publication. Historical author metadata includes a personal address, even
  though newer commits use a GitHub noreply address. Changing `.gitignore`,
  adding `.mailmap`, or changing future Git identity does not remove the
  original metadata. Before publication, rewrite author and committer identity
  in an isolated clone across every branch and tag intended for release, or
  publish a new repository with a sanitized initial commit. Keep a private
  backup, coordinate any rewritten remote refs, and recheck identities and
  secret scans afterward. The working repository was not rewritten.

## Licensing

The owner selected **GPL-3.0-only**. The repository now includes the complete
GNU GPL version 3 text, Python license metadata and license-file packaging,
matching JavaScript package metadata, and a README explanation. Commercial use
and sales are permitted; distribution of modified versions requires corresponding
source under GPL terms. Dependency licenses remain governed by their own terms.

## Findings and fixes

- Gitleaks 8.30.1 found no secrets in all locally available refs (361 commits,
  approximately 124 MB scanned) or in the publishable working-tree snapshot
  (approximately 6.2 MB). These scans exclude ignored local files from the
  working-tree snapshot; ignored files are not publication artifacts. Detection
  is pattern based and does not prove that every possible secret is absent.
- No tracked files matched the repository's ignore rules. Local controller and
  Frigate environment files, the local hardware show, dependencies, caches,
  and generated builds were ignored. No private environment files, device
  credentials, runtime databases, or installation media paths were identified
  in the historical filename inventory.
- Git exclusions now cover plain and variant `.env` files, `*.env` files,
  private keys/certificate bundles, SQLite databases and journals, root data,
  secrets, and credentials directories, and dependency directories. Public
  environment templates remain visible. `.gitignore` never removes files
  already committed and can be bypassed by force-adding files.
- Docker exclusions now also cover credentials, runtime state, `*.env` files,
  and the unrelated static site. Explicit application `COPY` instructions
  continue to define runtime image contents.
- Updated cryptography to 50.0.2, Pillow to 13.0.0, pytest to 9.1.1, certifi to
  2026.10.23, and Vitest packages to 4.1.12 to address audit findings. Python
  dependency ranges and both affected lockfiles were updated. Final pip-audit
  and npm audits reported no known vulnerabilities, including development
  dependencies. The documentation site's npm audit was also clean.
- CI now validates the browser production build and component tests before
  the controller image publication job can run.
- Documentation describes synthetic screenshot fixtures; reference mockups are
  documented as generated. Operator PNGs contain no ancillary metadata chunks;
  the two design references contain provenance metadata. No installation media
  is included in the tracked show examples. This audit does not establish
  ownership of every asset or review dependency license compatibility.

## Validation

- Python: Ruff formatting and lint, and strict mypy checks passed.
- Python: full test suite with upgraded dependencies pending at report creation.
- Browser: clean dependency install, TypeScript check, Vite production build,
  and 79 tests across 19 files passed. Vite reports existing bundle-size and
  mixed static/dynamic import warnings. Validation used an isolated temporary
  copy because the workspace's existing `web/node_modules` has permission
  problems; local generated dependencies were not repaired as part of this audit.
- Static documentation: clean dependency install, build, and checks of 40
  pages for links, anchors, duplicate IDs, and image alt text passed.
- Browser end-to-end tests, container builds, live deployment, and hardware
  operation were not exercised. Remote repository settings, private security
  reporting, branch protection, registry visibility, and refs unavailable
  locally were not audited.

## Before changing repository visibility

1. Resolve the historical identity blocker above.
2. Review and commit the intended working-tree changes, including the existing
   site and deployment work. The audit started with substantial uncommitted
   changes; a clean clone sees only committed content.
3. Run CI against the exact commit intended for publication. Re-run secret
   scanning after any history rewrite and inspect all refs being published.
4. Publish only repository artifacts. Keep ignored environments, private show
   packages, credentials, runtime state, and backup copies outside any public
   source archive or uploaded attachment.

When preparing the public repository, push only the intended branches and tags.
This working repository also has local agent/checkpoint refs; a mirror push
would include those refs and their otherwise unpublished history. The full-ref
secret scan covered them, but they are not intended release branches.
