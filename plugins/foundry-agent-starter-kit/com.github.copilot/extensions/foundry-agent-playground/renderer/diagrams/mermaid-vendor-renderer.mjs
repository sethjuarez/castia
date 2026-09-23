import { escapeHtml } from "../shared/html.mjs";
import { renderMermaidFallbackSource, renderSafeMermaidFlowchartBlock } from "./mermaid-safe-flowchart.mjs";

export const MERMAID_MAX_SOURCE_CHARS = 6000;
export const MERMAID_MAX_LINES = 240;
export const MERMAID_SUPPORTED_FAMILIES = ["flowchart", "graph", "sequencediagram", "statediagram", "classdiagram", "erdiagram"];

export function renderVendoredMermaidBlock(value) {
    const source = String(value || "");
    const support = mermaidSourceSupport(source);
    if (!support.ok) return { ok: false, reason: support.reason, error: support.error };
    if (!globalThis.__castiaMermaid) {
        return { ok: false, reason: "unavailable", error: "vendored Mermaid runtime is not available" };
    }
    return {
        ok: true,
        html: '<figure class="mermaid-diagram mermaid-vendor-diagram" data-mermaid-state="pending">' +
            '<div class="mermaid-vendor-target" role="img" aria-label="Mermaid diagram">Rendering Mermaid diagram...</div>' +
            '<pre class="mermaid-source" hidden>' + escapeHtml(source) + '</pre>' +
            '</figure>',
    };
}

export function mermaidSourceSupport(value) {
    const source = String(value || "");
    if (!source.trim()) return { ok: false, reason: "unsupported", error: "diagram source is empty" };
    if (source.length > MERMAID_MAX_SOURCE_CHARS) {
        return { ok: false, reason: "too_large", error: "diagram source is too large for inline Mermaid rendering" };
    }
    const lines = source.split(/\r?\n/);
    if (lines.length > MERMAID_MAX_LINES) {
        return { ok: false, reason: "too_large", error: "diagram has too many lines for inline Mermaid rendering" };
    }
    if (/%%\s*\{/m.test(source)) {
        return { ok: false, reason: "unsafe", error: "Mermaid init/config directives are disabled" };
    }
    if (hasRawHtmlMermaidLabel(source)) {
        return { ok: false, reason: "unsafe", error: "raw HTML labels are disabled" };
    }
    if (/(?:^|\n)\s*(?:click|href|call)\b/i.test(source)) {
        return { ok: false, reason: "unsafe", error: "interactive Mermaid links and callbacks are disabled" };
    }
    if (/\b(?:javascript|data):/i.test(source)) {
        return { ok: false, reason: "unsafe", error: "scriptable Mermaid URLs are disabled" };
    }
    const firstLine = lines.map((line) => line.trim()).find((line) => line && !line.startsWith("%%")) || "";
    const family = firstLine.split(/\s+/)[0].toLowerCase();
    if (!MERMAID_SUPPORTED_FAMILIES.includes(family)) {
        return {
            ok: false,
            reason: "unsupported",
            error: "only flowchart, sequence, state, class, and ER Mermaid diagrams are supported",
        };
    }
    return { ok: true, family };
}

export function hasRawHtmlMermaidLabel(value) {
    const htmlTags = [
        "a", "abbr", "article", "aside", "b", "blockquote", "br", "button", "code", "div",
        "em", "foreignObject", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "iframe",
        "img", "input", "label", "li", "link", "math", "object", "ol", "p", "pre", "script",
        "section", "select", "small", "span", "strong", "style", "sub", "sup", "svg", "table",
        "tbody", "td", "textarea", "th", "thead", "tr", "u", "ul", "video",
    ].join("|");
    return new RegExp("</?(" + htmlTags + ")(?:\\s|/?>)", "i").test(String(value || ""));
}

export function initializeVendoredMermaidRuntime() {
    const mermaid = globalThis.__castiaMermaid;
    if (!mermaid || globalThis.__castiaMermaidInitialized) return mermaid || null;
    mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        htmlLabels: false,
        deterministicIds: true,
        flowchart: {
            htmlLabels: false,
            useMaxWidth: true,
        },
        sequence: {
            useMaxWidth: true,
        },
        theme: "base",
        themeVariables: {
            background: "transparent",
            primaryColor: "transparent",
            primaryTextColor: "currentColor",
            lineColor: "currentColor",
            textColor: "currentColor",
        },
    });
    globalThis.__castiaMermaidInitialized = true;
    return mermaid;
}

export async function hydrateVendoredMermaidDiagrams(root = document) {
    const mermaid = initializeVendoredMermaidRuntime();
    if (!mermaid || !root?.querySelectorAll) return;
    const diagrams = [...root.querySelectorAll(".mermaid-vendor-diagram[data-mermaid-state='pending']")];
    for (let index = 0; index < diagrams.length; index += 1) {
        const figure = diagrams[index];
        const source = figure.querySelector(".mermaid-source")?.textContent || "";
        const target = figure.querySelector(".mermaid-vendor-target");
        const support = mermaidSourceSupport(source);
        if (!support.ok) {
            figure.outerHTML = renderMermaidFallbackSource(source, support.error);
            continue;
        }
        figure.dataset.mermaidState = "rendering";
        try {
            const id = "castia-mermaid-" + mermaidSourceHash(source) + "-" + index;
            const rendered = await mermaid.render(id, source);
            const svg = sanitizeMermaidSvg(rendered?.svg || "");
            target.innerHTML = svg;
            figure.dataset.mermaidState = "rendered";
        } catch (error) {
            figure.outerHTML = renderSafeMermaidFlowchartBlock(source) ||
                renderMermaidFallbackSource(source, error?.message || "vendored Mermaid rendering failed");
        }
    }
}

export function mermaidSourceHash(value) {
    let hash = 2166136261;
    const source = String(value || "");
    for (let index = 0; index < source.length; index += 1) {
        hash ^= source.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(36);
}

export function sanitizeMermaidSvg(svgText) {
    if (typeof DOMParser === "undefined" || typeof XMLSerializer === "undefined") {
        throw new Error("SVG sanitizer is not available in this browser");
    }
    const parsed = new DOMParser().parseFromString(String(svgText || ""), "image/svg+xml");
    if (parsed.querySelector("parsererror")) throw new Error("Mermaid returned invalid SVG");
    const root = parsed.documentElement;
    if (!root || root.tagName.toLowerCase() !== "svg") throw new Error("Mermaid did not return an SVG document");
    sanitizeSvgElement(root);
    return new XMLSerializer().serializeToString(root);
}

export function sanitizeSvgElement(element) {
    const allowedTags = new Set([
        "svg", "g", "defs", "marker", "path", "rect", "circle", "ellipse", "line",
        "polyline", "polygon", "style", "text", "tspan", "title", "desc",
    ]);
    const rejectedTags = new Set(["script", "foreignobject", "iframe", "object", "embed", "image", "a", "use"]);
    const allowedAttributes = new Set([
        "aria-hidden", "aria-label", "class", "clip-path", "cx", "cy", "d", "direction",
        "dominant-baseline", "dx", "dy", "fill", "font-family", "font-size", "height",
        "id", "marker-end", "marker-height", "marker-mid", "marker-start", "marker-units",
        "marker-width", "orient", "points", "preserveaspectratio", "r", "refx", "refy",
        "role", "rx", "ry", "stroke", "stroke-dasharray", "stroke-linecap",
        "stroke-linejoin", "stroke-width", "text-anchor", "transform", "viewbox", "width",
        "style", "x", "x1", "x2", "xmlns", "y", "y1", "y2",
    ]);
    const tag = element.tagName.toLowerCase();
    if (rejectedTags.has(tag) || !allowedTags.has(tag)) {
        throw new Error("Mermaid returned unsafe SVG element: " + tag);
    }
    if (tag === "style" && hasUnsafeSvgCss(element.textContent || "")) {
        throw new Error("Mermaid returned unsafe SVG style content");
    }
    for (const attr of [...element.attributes]) {
        const name = attr.name.toLowerCase();
        const value = String(attr.value || "");
        if (name.startsWith("on")) throw new Error("Mermaid returned unsafe SVG event attribute: " + attr.name);
        if (name === "href" || name.endsWith(":href")) throw new Error("Mermaid returned unsafe SVG link attribute: " + attr.name);
        if (/\b(?:javascript|data):/i.test(value)) throw new Error("Mermaid returned unsafe SVG URL");
        if (name === "style" && hasUnsafeSvgCss(value)) throw new Error("Mermaid returned unsafe SVG style attribute");
        if (!allowedAttributes.has(name)) element.removeAttribute(attr.name);
    }
    for (const child of [...element.children]) sanitizeSvgElement(child);
}

export function hasUnsafeSvgCss(value) {
    return /(?:@import|url\s*\(|expression\s*\(|-moz-binding|javascript\s*:|data\s*:)/i.test(String(value || ""));
}
