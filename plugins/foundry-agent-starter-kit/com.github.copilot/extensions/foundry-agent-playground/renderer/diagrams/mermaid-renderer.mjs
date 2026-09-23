import { renderMermaidFallbackSource, renderSafeMermaidFlowchartBlock } from "./mermaid-safe-flowchart.mjs";
import { renderVendoredMermaidBlock } from "./mermaid-vendor-renderer.mjs";

export function renderMermaidBlock(value) {
    const vendored = renderVendoredMermaidBlock(value);
    if (vendored.ok) return vendored.html;
    if (vendored.reason === "unsafe" || vendored.reason === "too_large") {
        return renderMermaidFallbackSource(value, vendored.error);
    }
    return renderSafeMermaidFlowchartBlock(value);
}
