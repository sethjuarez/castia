import { escapeHtml } from "../shared/html.mjs";

export function renderJsonDocument(value, language = "") {
    const text = String(value || "").trim();
    if (!text || (!["json", "jsonc"].includes(language) && !/^[\[{]/.test(text))) return null;
    try {
        const parsed = JSON.parse(text);
        return '<pre class="json-pre"><code>' + highlightJson(JSON.stringify(parsed, null, 2)) + "</code></pre>";
    } catch {
        return null;
    }
}

export function highlightJson(value) {
    const tokenPattern = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
    let output = "";
    let lastIndex = 0;
    String(value || "").replace(tokenPattern, (match, stringToken, keySuffix, literal, offset) => {
        output += escapeHtml(value.slice(lastIndex, offset));
        if (stringToken) {
            const className = keySuffix ? "json-key" : "json-string";
            output += '<span class="' + className + '">' + escapeHtml(stringToken) + "</span>" + escapeHtml(keySuffix || "");
        } else if (literal) {
            output += '<span class="json-literal">' + escapeHtml(match) + "</span>";
        } else {
            output += '<span class="json-number">' + escapeHtml(match) + "</span>";
        }
        lastIndex = offset + match.length;
        return match;
    });
    return output + escapeHtml(value.slice(lastIndex));
}
