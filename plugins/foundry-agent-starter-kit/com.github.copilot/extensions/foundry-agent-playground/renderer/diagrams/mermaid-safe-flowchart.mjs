import { escapeHtml } from "../shared/html.mjs";

export function renderMermaidBlock(value) {
    return renderSafeMermaidFlowchartBlock(value);
}

export function renderMermaidExpandButton(disabled = false) {
    return '<div class="mermaid-toolbar">' +
        '<button type="button" class="mermaid-expand-button" aria-label="Expand diagram"' + (disabled ? " disabled" : "") + '>Expand diagram</button>' +
        '</div>';
}

export function renderSafeMermaidFlowchartBlock(value) {
    const parsed = parseMermaidFlowchart(value);
    if (!parsed.ok) {
        return renderMermaidFallbackSource(value, parsed.error);
    }
    const { nodes, edges, direction } = parsed;
    const horizontal = ["LR", "RL"].includes(direction);
    const width = horizontal ? Math.max(360, nodes.length * 190 + 48) : 420;
    const height = horizontal ? 180 : Math.max(180, nodes.length * 112 + 48);
    const positions = new Map();
    nodes.forEach((node, index) => {
        const order = direction === "RL" || direction === "BT" ? nodes.length - index - 1 : index;
        positions.set(node.id, horizontal
            ? { x: 24 + order * 190, y: 54 }
            : { x: 70, y: 24 + order * 112 });
    });
    const edgeSvg = edges.map((edge) => {
        const from = positions.get(edge.from);
        const to = positions.get(edge.to);
        if (!from || !to) return "";
        const x1 = horizontal ? from.x + 150 : from.x + 75;
        const y1 = horizontal ? from.y + 30 : from.y + 60;
        const x2 = horizontal ? to.x : to.x + 75;
        const y2 = horizontal ? to.y + 30 : to.y;
        const label = edge.label
            ? '<text class="mermaid-edge-label" x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 8) + '">' + escapeHtml(edge.label) + '</text>'
            : "";
        const edgeClass = edge.dotted ? "mermaid-edge mermaid-edge-dotted" : "mermaid-edge";
        return '<path class="' + edgeClass + '" d="M ' + x1 + " " + y1 + " L " + x2 + " " + y2 + '" marker-end="url(#mermaid-arrow)" />' + label;
    }).join("");
    const nodeSvg = nodes.map((node) => {
        const position = positions.get(node.id);
        const label = wrapMermaidLabel(node.label || node.id);
        const lines = label.map((line, index) =>
            '<tspan x="75" dy="' + (index === 0 ? 0 : 15) + '">' + escapeHtml(line) + '</tspan>'
        ).join("");
        return '<g class="mermaid-node" transform="translate(' + position.x + " " + position.y + ')">' +
            '<rect width="150" height="60" rx="12" />' +
            '<text x="75" y="' + (label.length > 1 ? 24 : 34) + '">' + lines + '</text>' +
            '</g>';
    }).join("");
    return '<figure class="mermaid-diagram" aria-label="Mermaid diagram">' +
        renderMermaidExpandButton() +
        '<svg role="img" viewBox="0 0 ' + width + " " + height + '" xmlns="http://www.w3.org/2000/svg">' +
        '<defs><marker id="mermaid-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>' +
        edgeSvg + nodeSvg +
        '</svg>' +
        '</figure>';
}

export function renderMermaidFallbackSource(value, error) {
    return '<figure class="mermaid-fallback">' +
        '<figcaption>Mermaid diagram could not be rendered: ' + escapeHtml(error) + '</figcaption>' +
        '<pre><code>' + escapeHtml(value) + '</code></pre>' +
        '</figure>';
}

export function parseMermaidFlowchart(value) {
    const lines = String(value || "").split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith("%%"));
    const header = lines.shift() || "";
    const headerMatch = header.match(/^(flowchart|graph)\s+(TD|TB|BT|LR|RL)?$/i);
    if (!headerMatch) return { ok: false, error: "only flowchart/graph diagrams are supported" };
    const direction = (headerMatch[2] || "TD").toUpperCase();
    const nodeMap = new Map();
    const edges = [];
    const addNode = (id, label = "") => {
        const nextLabel = label || id;
        if (!nodeMap.has(id)) nodeMap.set(id, { id, label: nextLabel });
        else if (nextLabel && nodeMap.get(id).label === id) nodeMap.get(id).label = nextLabel;
    };
    for (const line of lines) {
        const parsedEdges = parseMermaidEdges(line);
        if (parsedEdges) {
            for (const edge of parsedEdges) {
                addNode(edge.from, edge.fromLabel);
                addNode(edge.to, edge.toLabel);
                edges.push(edge);
            }
            continue;
        }
        const node = parseMermaidNode(line);
        if (node) {
            addNode(node.id, node.label);
            continue;
        }
        if (/^subgraph\s+/i.test(line) || /^end$/i.test(line)) continue;
        return { ok: false, error: "unsupported Mermaid syntax near: " + line.slice(0, 80) };
    }
    if (!nodeMap.size) return { ok: false, error: "diagram has no nodes" };
    if (nodeMap.size > 30 || edges.length > 50) return { ok: false, error: "diagram is too large for inline rendering" };
    return { ok: true, direction, nodes: [...nodeMap.values()], edges };
}

export function parseMermaidEdges(line) {
    const shape = String.raw`(\s*(?:\[[^\]]+\]|\([^)]+\)|\{[^}]+\}))?`;
    const solid = new RegExp(String.raw`^\s*([A-Za-z][\w-]*)${shape}\s*--(?:\|([^|]+)\||\s+(.+?)\s+--)?>(?:\s*)([A-Za-z][\w-]*)${shape}\s*;?\s*$`);
    const dotted = new RegExp(String.raw`^\s*([A-Za-z][\w-]*)${shape}\s*-\.(?:\s*"([^"]+)"\s*\.)?->\s*([A-Za-z][\w-]*)${shape}\s*;?\s*$`);
    const chained = new RegExp(String.raw`^\s*([A-Za-z][\w-]*)${shape}((?:\s*-->\s*[A-Za-z][\w-]*${shape})+)\s*;?\s*$`);
    const chainedMatch = line.match(chained);
    if (chainedMatch) {
        const nodes = [...line.matchAll(new RegExp(String.raw`([A-Za-z][\w-]*)${shape}`, "g"))]
            .map((match) => ({ id: match[1], label: mermaidShapeLabel(match[2]) || match[1] }));
        return nodes.slice(0, -1).map((node, index) => ({
            from: node.id,
            fromLabel: node.label,
            dotted: false,
            label: "",
            to: nodes[index + 1].id,
            toLabel: nodes[index + 1].label,
        }));
    }
    const solidMatch = line.match(solid);
    const dottedMatch = solidMatch ? null : line.match(dotted);
    const match = solidMatch || dottedMatch;
    if (!match) return null;
    return [{
        from: match[1],
        fromLabel: mermaidShapeLabel(match[2]) || match[1],
        dotted: Boolean(dottedMatch),
        label: String(match[3] || match[4] || "").trim(),
        to: match[5],
        toLabel: mermaidShapeLabel(match[6]) || match[5],
    }];
}

export function parseMermaidNode(line) {
    const match = line.match(/^\s*([A-Za-z][\w-]*)(\s*(?:\[[^\]]+\]|\([^)]+\)|\{[^}]+\}))\s*;?\s*$/);
    return match ? { id: match[1], label: mermaidShapeLabel(match[2]) || match[1] } : null;
}

export function mermaidShapeLabel(shape = "") {
    return String(shape || "")
        .trim()
        .replace(/^[\s[({"']+|[\s\])}"']+$/g, "")
        .replace(/\\"/g, '"')
        .trim();
}

export function wrapMermaidLabel(value) {
    const words = String(value || "").split(/\s+/).filter(Boolean);
    const lines = [];
    let current = "";
    for (const word of words) {
        if ((current + " " + word).trim().length > 22 && current) {
            lines.push(current);
            current = word;
        } else {
            current = (current + " " + word).trim();
        }
    }
    if (current) lines.push(current);
    return lines.slice(0, 3);
}
