import { button, el, heading, notice, safeLink } from "./common.js";
import { workbenchRequest } from "./api.js";

const slug = text => text.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, "").trim().replace(/\s+/g, "-");
function inline(text, article, sourceUrl) {
  const fragment = document.createDocumentFragment();
  const tokens = String(text).split(/(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*)/g);
  for (const token of tokens) {
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
    if (link) {
      if (link[2].startsWith("#")) {
        fragment.append(button(link[1], () => {
          const target = [...article.querySelectorAll("[data-anchor]")].find(node => node.dataset.anchor === link[2].slice(1));
          target?.scrollIntoView({ block: "start" });
        }, "inline-link"));
      } else {
        let url;
        try { url = new URL(link[2], sourceUrl).href; } catch { /* Render invalid links as text. */ }
        fragment.append(url ? safeLink(url, link[1]) || document.createTextNode(link[1]) : document.createTextNode(link[1]));
      }
    } else if (token.startsWith("`") && token.endsWith("`")) fragment.append(el("code", { text: token.slice(1, -1) }));
    else if (token.startsWith("**") && token.endsWith("**")) fragment.append(el("strong", { text: token.slice(2, -2) }));
    else fragment.append(document.createTextNode(token));
  }
  return fragment;
}
export function renderedMarkdown(markdown, sourceUrl) {
  const article = el("article", { class: "panel documentation stack" });
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  let code = null, list = null, listKind = null, table = null;
  const flush = () => { list = null; listKind = null; table = null; };
  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      if (code) { code = null; flush(); continue; }
      flush();
      const codeNode = el("code", { text: "" });
      const outcome = el("span", { class: "meta", role: "status" });
      code = codeNode;
      article.append(el("div", { class: "code-block" }, button("Copy code", async () => {
        try { await navigator.clipboard.writeText(codeNode.textContent); outcome.textContent = "Copied"; }
        catch { outcome.textContent = "Select the code to copy it."; }
      }, "quiet-button"), outcome, el("pre", {}, codeNode)));
      continue;
    }
    if (code) { code.textContent += (code.textContent ? "\n" : "") + line; continue; }
    const match = /^(#{1,6})\s+(.+)$/.exec(line);
    if (match) { flush(); article.append(el(`h${Math.min(6, match[1].length + 1)}`, { "data-anchor": slug(match[2]) }, inline(match[2], article, sourceUrl))); continue; }
    if (/^\|.*\|\s*$/.test(line)) {
      const cells = line.trim().slice(1, -1).split("|").map(cell => cell.trim());
      if (cells.every(cell => /^:?-+:?$/.test(cell))) continue;
      const first = !table;
      if (first) { flush(); table = el("table"); article.append(el("div", { class: "table-wrap" }, table)); }
      table.append(el("tr", {}, cells.map(cell => el(first ? "th" : "td", first ? { scope: "col" } : {}, inline(cell, article, sourceUrl)))));
      continue;
    }
    table = null;
    const item = /^\s*(?:[-*]|(\d+)\.)\s+(.+)$/.exec(line);
    if (item) {
      const kind = item[1] ? "ol" : "ul";
      if (!list || listKind !== kind) { list = el(kind); listKind = kind; article.append(list); }
      list.append(el("li", {}, inline(item[2], article, sourceUrl))); continue;
    }
    flush();
    if (!line.trim()) continue;
    if (/^[-*_]{3,}\s*$/.test(line)) { article.append(el("hr")); continue; }
    article.append(el(line.startsWith("> ") ? "blockquote" : "p", {}, inline(line.replace(/^> /, ""), article, sourceUrl)));
  }
  return article;
}
export async function documentationView(ctx, id) {
  const root = el("div", { class: "workbench-page stack", "data-story": "US-SETTINGS-01" }, heading("Documentation", "Guides packaged with this Workbench installation."));
  try {
    const catalog = await workbenchRequest("documents", { signal: ctx.signal });
    const selected = id || catalog.items?.[0]?.id;
    root.append(el("nav", { class: "local-tabs", "aria-label": "Documentation" }, ...(catalog.items || []).map(item => button(item.title, () => { location.hash = `#/documentation/${encodeURIComponent(item.id)}`; }, item.id === selected ? "primary" : "quiet-button"))));
    if (selected) {
      const data = await workbenchRequest(`documents/${encodeURIComponent(selected)}`, { signal: ctx.signal });
      root.append(el("div", { class: "actions" }, el("span", { class: "meta", text: `Packaged version ${data.version} · ${data.digest.slice(0, 12)}` }), safeLink(data.source_url, "View source")), renderedMarkdown(data.markdown, data.source_url));
    }
  } catch (error) { root.append(notice(error.message, "danger")); }
  return root;
}
