import { cp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import MarkdownIt from "markdown-it";
import GithubSlugger from "github-slugger";

const root = fileURLToPath(new URL("../", import.meta.url));
const out = path.join(root, "site/dist");
const repository = "https://github.com/bluesk1d/cueverge";
const escape = (text) =>
  text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
async function markdownFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  return (
    await Promise.all(
      entries.map((entry) =>
        entry.isDirectory()
          ? markdownFiles(path.join(dir, entry.name))
          : entry.name.endsWith(".md")
            ? [path.join(dir, entry.name)]
            : [],
      ),
    )
  ).flat();
}
const files = await markdownFiles(path.join(root, "docs"));
const route = (file) =>
  path
    .relative(root, file)
    .replace(/README\.md$/, "index.html")
    .replace(/\.md$/, ".html");
const routes = new Map(files.map((file) => [file, route(file)]));
const primary = [
  ["show-creator-guide.md", "User guide"],
  ["operator-guide.md", "Operator guide"],
  ["service-administration.md", "Service administration"],
];
const mark =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M20 13a8 8 0 1 1-5-8M12 12l7-7"/><circle cx="12" cy="12" r="2"/></svg>';
function frame({ title, description, body, page = "index.html", doc = false }) {
  const prefix = path.posix.relative(path.posix.dirname(page), ".") || ".";
  const href = (target) => `${prefix}/${target}`;
  const nav = primary
    .slice(0, 2)
    .map(
      ([source, label]) =>
        `<a href="${href(`docs/${source.replace(".md", ".html")}`)}"${page === `docs/${source.replace(".md", ".html")}` ? ' aria-current="page"' : ""}>${label}</a>`,
    )
    .join("");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#0b111b"><meta name="description" content="${escape(description)}"><title>${escape(title)} · Cueverge</title><link rel="icon" href="${href("favicon.svg")}" type="image/svg+xml"><link rel="stylesheet" href="${href("styles.css")}"></head>
<body${doc ? ' class="documentation"' : ""}><a class="skip-link" href="#main-content">Skip to content</a>
<header class="site-header"><div class="header-inner"><a class="brand" href="${href("index.html")}"><span class="brand-mark">${mark}</span><span>Cueverge<small>Show control</small></span></a><nav aria-label="Primary">${nav}<a href="${href("docs/index.html")}"${page === "docs/index.html" ? ' aria-current="page"' : ""}>All docs</a><a class="source-link" href="${repository}">GitHub <span aria-hidden="true">↗</span></a></nav></div></header>
${body}
<footer class="site-footer"><a class="footer-brand" href="${href("index.html")}">Cueverge</a><span>Self-hosted. Community-built. Made for creative shows.</span><a href="${repository}">Explore the source <span aria-hidden="true">↗</span></a></footer></body></html>`;
}
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await cp(path.join(root, "site/styles.css"), path.join(out, "styles.css"));
await cp(path.join(root, "site/favicon.svg"), path.join(out, "favicon.svg"));
await cp(path.join(root, "docs/assets"), path.join(out, "docs/assets"), {
  recursive: true,
});
await writeFile(path.join(out, ".nojekyll"), "");
const home = await readFile(path.join(root, "site/index.html"), "utf8");
await writeFile(
  path.join(out, "index.html"),
  frame({
    title: "Bring your display to life",
    description:
      "Cueverge is a self-hosted, open-source show engine for synchronized media, lighting, and visitor-responsive displays.",
    body: home,
  }),
);
for (const source of files) {
  const page = routes.get(source);
  const prefix = path.posix.relative(path.posix.dirname(page), "docs") || ".";
  const text = await readFile(source, "utf8");
  const slugger = new GithubSlugger();
  const headings = [];
  const md = new MarkdownIt({ html: false, linkify: false });
  for (const type of ["fence", "code_block"]) {
    const render = md.renderer.rules[type];
    md.renderer.rules[type] = (...args) =>
      render(...args).replace("<pre>", '<pre tabindex="0">');
  }
  md.core.ruler.push("site_links_and_headings", (state) => {
    for (let i = 0; i < state.tokens.length; i++) {
      const token = state.tokens[i];
      if (token.type === "table_open") token.attrSet("tabindex", "0");
      if (token.type === "heading_open") {
        const inline = state.tokens[i + 1];
        const label = (inline.children ?? [])
          .filter((child) => ["text", "code_inline"].includes(child.type))
          .map((child) => child.content)
          .join("");
        const id = slugger.slug(label);
        token.attrSet("id", id);
        if (token.tag === "h2" && label !== "Contents")
          headings.push({ id, label });
      }
      for (const child of token.children ?? []) {
        const attr =
          child.type === "link_open"
            ? "href"
            : child.type === "image"
              ? "src"
              : null;
        if (!attr) continue;
        const url = child.attrGet(attr);
        if (!url || /^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/i.test(url)) continue;
        const [pathname, suffix = ""] = url.split(/(?=[#?])/s, 2);
        const resolved = path.resolve(
          path.dirname(source),
          decodeURIComponent(pathname),
        );
        if (routes.has(resolved)) {
          child.attrSet(
            attr,
            path.posix.relative(
              path.posix.dirname(page),
              routes.get(resolved),
            ) + suffix,
          );
        } else if (attr === "href") {
          child.attrSet(
            attr,
            `${repository}/blob/main/${path.relative(root, resolved).split(path.sep).map(encodeURIComponent).join("/")}${suffix}`,
          );
        }
        if (child.type === "image") child.attrSet("loading", "lazy");
      }
    }
  });
  const content = md.render(text);
  const title = text.match(/^# (.+)$/m)?.[1] ?? "Documentation";
  const guideLinks = primary
    .map(
      ([file, label]) =>
        `<a href="${prefix}/${file.replace(".md", ".html")}"${path.basename(source) === file ? ' aria-current="page"' : ""}>${label}</a>`,
    )
    .join("");
  const toc = headings
    .map(({ id, label }) => `<a href="#${escape(id)}">${escape(label)}</a>`)
    .join("");
  const body = `<div class="docs-layout"><aside class="docs-sidebar"><p class="eyebrow">Documentation</p><nav aria-label="Guides"><a href="${prefix}/index.html"${path.basename(source) === "README.md" && path.dirname(source) === path.join(root, "docs") ? ' aria-current="page"' : ""}>Documentation home</a>${guideLinks}</nav><a class="edit-link" href="${repository}/blob/main/${path.relative(root, source)}">View Markdown on GitHub ↗</a></aside><main class="doc-content" id="main-content"><p class="eyebrow">Cueverge / ${primary.find(([file]) => path.basename(source) === file)?.[1] ?? "Reference"}</p><article>${content}</article><a class="back-top" href="#main-content">Back to top ↑</a></main><aside class="on-this-page"><details open><summary>On this page</summary><nav aria-label="On this page">${toc}</nav></details></aside></div>`;
  await mkdir(path.dirname(path.join(out, page)), { recursive: true });
  await writeFile(
    path.join(out, page),
    frame({
      title,
      description: `${title}: Cueverge documentation, workflows, and current implementation boundaries.`,
      body,
      page,
      doc: true,
    }),
  );
}
console.log(
  `Built landing page and ${files.length} documentation pages in site/dist.`,
);
