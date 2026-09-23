import { renderMermaidFallbackSource, renderSafeMermaidFlowchartBlock } from "./mermaid-safe-flowchart.mjs";
import { renderVendoredMermaidBlock } from "./mermaid-vendor-renderer.mjs";

export function renderMermaidBlock(value, options = {}) {
    if (options.complete === false) {
        return renderMermaidFallbackSource(value, "Mermaid diagram is still streaming");
    }
    const vendored = renderVendoredMermaidBlock(value);
    if (vendored.ok) return vendored.html;
    if (vendored.reason === "unsafe" || vendored.reason === "too_large") {
        return renderMermaidFallbackSource(value, vendored.error);
    }
    return renderSafeMermaidFlowchartBlock(value);
}
