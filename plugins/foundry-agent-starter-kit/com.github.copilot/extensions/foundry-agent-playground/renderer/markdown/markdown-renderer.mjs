import { renderMermaidBlock } from "../diagrams/mermaid-renderer.mjs";
import { renderJsonDocument } from "../json/json-renderer.mjs";
import { escapeHtml } from "../shared/html.mjs";

export const codeBlockRenderers = [
    {
        id: "mermaid",
        canRender: ({ language }) => String(language || "").split(/\s+/)[0] === "mermaid",
        render: ({ value, complete }) => renderMermaidBlock(value, { complete }),
    },
    {
        id: "json",
        canRender: ({ value, language }) => Boolean(renderJsonDocument(value, language)),
        render: ({ value, language }) => renderJsonDocument(value, language),
    },
];

export function renderMarkdown(value) {
    const jsonDocument = renderJsonDocument(value);
    if (jsonDocument) return '<div class="md">' + jsonDocument + "</div>";
    const blocks = [];
    let inFence = false;
    let fence = [];
    let fenceLanguage = "";
    let list = null;
    let paragraph = [];

    function flushParagraph() {
        if (!paragraph.length) return;
        blocks.push("<p>" + renderInline(paragraph.join(" ")) + "</p>");
        paragraph = [];
    }

    function flushList() {
        if (!list) return;
        blocks.push("<" + list.type + ">" + list.items.map((item) => "<li>" + renderInline(item) + "</li>").join("") + "</" + list.type + ">");
        list = null;
    }

    const lines = String(value || "").split(/\r?\n/);
    for (let index = 0; index < lines.length; index += 1) {
        const rawLine = lines[index];
        const line = rawLine.replace(/\s+$/, "");
        if (line.trim().startsWith(String.fromCharCode(96, 96, 96))) {
            if (inFence) {
                blocks.push(renderCodeBlock(fence.join("\n"), fenceLanguage));
                fence = [];
                fenceLanguage = "";
                inFence = false;
            } else {
                flushParagraph();
                flushList();
                fenceLanguage = line.trim().slice(3).trim().toLowerCase();
                inFence = true;
            }
            continue;
        }
        if (inFence) {
            fence.push(rawLine);
            continue;
        }
        if (!line.trim()) {
            flushParagraph();
            flushList();
            continue;
        }
        const heading = line.match(/^\s*(#{1,3})\s+(.+)$/);
        if (heading) {
            flushParagraph();
            flushList();
            blocks.push("<h" + heading[1].length + ">" + renderInline(heading[2]) + "</h" + heading[1].length + ">");
            continue;
        }
        const table = renderTable(lines, index);
        if (table) {
            flushParagraph();
            flushList();
            blocks.push(table.html);
            index = table.nextIndex - 1;
            continue;
        }
        const unordered = line.match(/^\s*[-*]\s+(.+)$/);
        const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
        if (unordered || ordered) {
            flushParagraph();
            const type = unordered ? "ul" : "ol";
            if (!list || list.type !== type) flushList();
            list ||= { type, items: [] };
            list.items.push((unordered || ordered)[1]);
            continue;
        }
        flushList();
        paragraph.push(line.trim());
    }
    if (inFence) blocks.push(renderCodeBlock(fence.join("\n"), fenceLanguage, { complete: false }));
    flushParagraph();
    flushList();
    return '<div class="md">' + (blocks.join("") || "<p></p>") + "</div>";
}

export function renderCodeBlock(value, language, options = {}) {
    for (const renderer of codeBlockRenderers) {
        if (renderer.canRender({ value, language })) return renderer.render({ value, language, ...options });
    }
    return "<pre><code>" + escapeHtml(value) + "</code></pre>";
}

export function renderTable(lines, start) {
    if (!String(lines[start] || "").includes("|") || !isTableSeparator(lines[start + 1] || "")) return null;
    const header = splitTableRow(lines[start]);
    const separator = splitTableRow(lines[start + 1]);
    if (!header.length || separator.length < header.length) return null;
    const alignments = separator.map((cell) =>
        cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : cell.startsWith(":") ? "left" : ""
    );
    const rows = [];
    let index = start + 2;
    while (index < lines.length && String(lines[index] || "").trim() && String(lines[index] || "").includes("|")) {
        if (String(lines[index]).trim().startsWith(String.fromCharCode(96, 96, 96))) break;
        rows.push(splitTableRow(lines[index]));
        index += 1;
    }
    const cellAttr = (column) => alignments[column] ? ' style="text-align:' + alignments[column] + '"' : "";
    const head = "<thead><tr>" + header.map((cell, column) => "<th" + cellAttr(column) + ">" + renderInline(cell) + "</th>").join("") + "</tr></thead>";
    const body = "<tbody>" + rows.map((row) => "<tr>" + header.map((_, column) => "<td" + cellAttr(column) + ">" + renderInline(row[column] || "") + "</td>").join("") + "</tr>").join("") + "</tbody>";
    return { html: '<div class="table-scroll"><table>' + head + body + "</table></div>", nextIndex: index };
}

export function splitTableRow(line) {
    return String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

export function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(String(line || ""));
}

export function renderInline(value) {
    const tick = String.fromCharCode(96);
    const inlineCode = new RegExp(tick + "([^" + tick + "]+)" + tick, "g");
    return escapeHtml(value)
        .replace(inlineCode, "<code>$1</code>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}
