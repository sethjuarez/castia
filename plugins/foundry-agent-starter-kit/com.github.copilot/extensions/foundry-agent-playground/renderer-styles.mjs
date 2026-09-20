export const rendererStyles = `
    :root {
      color-scheme: light;
      --cp-bg: #f7f4ef;
      --cp-bg-elevated: #fcfbf8;
      --cp-surface: #ffffff;
      --cp-surface-soft: #f5f5f5;
      --cp-border: #dedede;
      --cp-border-strong: #919191;
      --cp-text: #242424;
      --cp-text-muted: #5c5c5c;
      --cp-text-soft: #6f6f6f;
      --cp-accent: #b11f4b;
      --cp-accent-hover: #9a1a41;
      --cp-accent-soft: rgba(177, 31, 75, 0.08);
      --cp-accent-fg: #ffffff;
      --cp-success: #16a34a;
      --cp-danger: #dc2626;
      --cp-warning: #f59e0b;
      --cp-link: #0078d4;
      --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
      --cp-overlay: rgba(255, 255, 255, 0.8);
      --cp-panel: rgba(255, 255, 255, 0.86);
      --cp-panel-strong: rgba(255, 255, 255, 0.96);
      --cp-sheen: rgba(255, 255, 255, 0.55);
      --cp-highlight: rgba(177, 31, 75, 0.12);
    }
    html[data-theme="dark"] {
      color-scheme: dark;
      --cp-bg: #3d3b3a;
      --cp-bg-elevated: #343231;
      --cp-surface: #292929;
      --cp-surface-soft: #2e2e2e;
      --cp-border: #474747;
      --cp-border-strong: #5f5f5f;
      --cp-text: #dedede;
      --cp-text-muted: #919191;
      --cp-text-soft: #b0b0b0;
      --cp-accent: #fd8ea1;
      --cp-accent-hover: #fb7b91;
      --cp-accent-soft: rgba(253, 142, 161, 0.14);
      --cp-accent-fg: #1a1a1a;
      --cp-success: #4ade80;
      --cp-danger: #f87171;
      --cp-warning: #fbbf24;
      --cp-link: #4da6ff;
      --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
      --cp-overlay: rgba(41, 41, 41, 0.88);
      --cp-panel: rgba(41, 41, 41, 0.72);
      --cp-panel-strong: rgba(41, 41, 41, 0.96);
      --cp-sheen: rgba(255, 255, 255, 0.04);
      --cp-highlight: rgba(253, 142, 161, 0.12);
    }
    :root,
    html[data-theme="dark"] {
      color-scheme: light dark;
      --cp-bg: var(--background-color-default, #ffffff);
      --cp-bg-elevated: var(--background-color-default, #ffffff);
      --cp-surface: var(--background-color-default, #ffffff);
      --cp-surface-soft: var(--background-color-subtle, #f5f5f5);
      --cp-border: var(--border-color-default, #dedede);
      --cp-border-strong: var(--border-color-muted, var(--border-color-default, #919191));
      --cp-text: var(--text-color-default, #242424);
      --cp-text-muted: var(--text-color-muted, #5c5c5c);
      --cp-text-soft: var(--text-color-muted, #6f6f6f);
      --cp-accent: var(--color-focus-outline, var(--cp-link));
      --cp-accent-hover: var(--color-focus-outline, var(--cp-link));
      --cp-accent-soft: color-mix(in srgb, var(--cp-accent) 10%, var(--cp-surface));
      --cp-accent-fg: var(--color-white, #ffffff);
      --cp-success: var(--true-color-green, #16a34a);
      --cp-danger: var(--true-color-red, #dc2626);
      --cp-warning: var(--true-color-yellow, #f59e0b);
      --cp-link: var(--true-color-blue, var(--color-focus-outline, #0078d4));
      --cp-shadow: 0 0 2px rgba(0, 0, 0, 0.12), 0 1px 2px rgba(0, 0, 0, 0.14);
      --cp-overlay: var(--background-color-default, #ffffff);
      --cp-panel: var(--background-color-default, #ffffff);
      --cp-panel-strong: var(--background-color-default, #ffffff);
      --cp-sheen: var(--background-color-subtle, #f5f5f5);
      --cp-highlight: var(--background-color-subtle, #f5f5f5);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; overflow: hidden; }
    html {
      scrollbar-color: var(--cp-border-strong) var(--cp-bg);
    }
    ::-webkit-scrollbar {
      width: 12px;
      height: 12px;
    }
    ::-webkit-scrollbar-track {
      background: var(--cp-bg);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--cp-border-strong);
      border: 3px solid var(--cp-bg);
      border-radius: 999px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--cp-text-muted);
    }
    body {
      margin: 0;
      background: var(--cp-bg);
      color: var(--cp-text);
      font-family: var(--font-sans, "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif);
      font-size: var(--text-body-medium, 14px);
      line-height: var(--leading-body-medium, 20px);
    }
    button, input, textarea { font: inherit; }
    button {
      border: 1px solid transparent;
      border-radius: 0.625rem;
      padding: 8px 12px;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { border-color: var(--cp-border); }
    button:disabled { cursor: not-allowed; opacity: 0.62; }
    button.primary {
      border-color: var(--cp-accent);
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    button.primary:hover { background: var(--cp-accent-hover); }
    input, textarea {
      width: 100%;
      border: 1px solid var(--cp-border);
      border-radius: 0.625rem;
      padding: 10px 12px;
      background: var(--cp-surface);
      color: var(--cp-text);
    }
    textarea { min-height: 108px; resize: none; }
    input:focus, textarea:focus, button:focus {
      outline: 2px solid var(--cp-accent);
      outline-offset: 2px;
    }
    code, pre {
      font-family: var(--font-mono, Consolas, "Courier New", Courier, monospace);
      font-size: var(--text-code-inline, 12px);
    }
    .app {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100dvh;
      overflow: hidden;
    }
    .hero {
      display: grid;
      gap: 10px;
      padding: 10px 12px;
      background: var(--cp-bg-elevated);
    }
    .journey {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .step {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-width: 0;
      min-height: 48px;
      padding: 8px;
      border: 1px solid transparent;
      border-radius: 14px;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
      text-align: left;
      box-shadow: none;
    }
    .step:hover {
      border-color: var(--cp-border);
    }
    .step.active {
      border-color: var(--cp-border);
      background: var(--cp-surface);
      box-shadow: var(--cp-shadow);
    }
    .step.done {
      border-color: color-mix(in srgb, var(--cp-success) 24%, transparent);
    }
    .step-index {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: var(--cp-panel-strong);
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 800;
    }
    .step.active .step-index {
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    .step.done .step-index {
      background: var(--cp-success);
      color: var(--cp-accent-fg);
    }
    .step-title {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 700;
    }
    .step-subtitle {
      display: block;
      margin-top: 1px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
    }
    .step-state {
      display: none;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 700;
    }
    @media (max-width: 900px) {
      .journey {
        gap: 6px;
      }
      .step {
        grid-template-columns: auto minmax(0, 1fr);
        gap: 8px;
        padding: 8px;
      }
      .step-index {
        width: 24px;
        height: 24px;
      }
    }
    @media (max-width: 520px) {
      .journey {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .step {
        grid-template-columns: auto minmax(0, 1fr);
        min-height: 44px;
        padding: 8px 6px;
        border-radius: 14px;
      }
      .step-index {
        width: 22px;
        height: 22px;
      }
      .step-title {
        font-size: 13px;
      }
      .step-subtitle {
        display: none;
      }
    }
    .action-card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 12px;
      border-radius: 16px;
      background: var(--cp-panel-strong);
      box-shadow: var(--cp-shadow);
    }
    .action-title {
      font-weight: 800;
    }
    .action-copy {
      color: var(--cp-text-muted);
      font-size: 13px;
      margin-top: 2px;
    }
    .local-endpoint-banner {
      margin-top: 8px;
      padding: 8px 10px;
      border: 1px solid var(--cp-border);
      border-radius: 12px;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
      font-size: 12px;
    }
    .local-endpoint-banner[hidden] { display: none; }
    .local-endpoint-banner.warn {
      border-color: color-mix(in srgb, var(--cp-warning) 50%, var(--cp-border));
      background: color-mix(in srgb, var(--cp-warning) 12%, var(--cp-surface));
    }
    .local-endpoint-banner code {
      word-break: break-all;
    }
    .endpoint-meta,
    .endpoint-warning {
      color: var(--cp-text-muted);
      margin-top: 2px;
    }
    .endpoint-warning button {
      margin-left: 6px;
      padding: 4px 8px;
    }
    .local-ticker {
      display: flex;
      gap: 6px;
      align-items: center;
      margin-top: 8px;
      color: var(--cp-text-muted);
      font-size: 12px;
      min-height: 16px;
    }
    .local-ticker[hidden] { display: none; }
    .deploy-ticker {
      display: flex;
      margin-top: 8px;
      color: var(--cp-text-muted);
      font-size: 12px;
      min-height: 16px;
      width: min(100%, 520px);
    }
    .deploy-ticker[hidden] { display: none; }
    .deploy-ticker-row {
      align-items: center;
      width: 100%;
      display: grid;
      grid-template-columns: max-content 6ch max-content minmax(0, 1fr);
      gap: 8px;
    }
    .deploy-label {
      color: var(--cp-text-muted);
      font-weight: 600;
      white-space: nowrap;
    }
    .deploy-elapsed {
      color: var(--cp-text);
      font-variant-numeric: tabular-nums;
      font-weight: 700;
      text-align: center;
      white-space: nowrap;
    }
    .deploy-phase {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .ticker-mark {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--cp-text-muted);
      flex: 0 0 auto;
    }
    .deploy-ticker-row .ticker-mark {
      align-self: center;
      justify-self: center;
    }
    .local-ticker.ok .ticker-mark { background: var(--cp-success); }
    .local-ticker.warn .ticker-mark { background: var(--cp-warning); }
    .local-ticker.fail .ticker-mark { background: var(--cp-danger); }
    .deploy-ticker .ticker-mark { background: var(--cp-warning); }
    .ticker-text {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    .action-buttons {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .hero-picker {
      width: min(240px, 34vw);
    }
    .secondary {
      color: var(--cp-text-muted);
    }
    .agent-picker {
      position: relative;
      min-width: 0;
    }
    .agent-picker-button {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      width: 100%;
      border-color: var(--cp-border);
      background: var(--cp-surface);
      color: var(--cp-text);
      font-weight: 500;
      text-align: left;
    }
    .agent-picker-button:hover {
      border-color: var(--cp-border-strong);
      background: var(--cp-panel-strong);
    }
    .agent-picker-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .agent-picker-chevron {
      width: 8px;
      height: 8px;
      border-right: 1.5px solid var(--cp-text-muted);
      border-bottom: 1.5px solid var(--cp-text-muted);
      transform: translateY(-2px) rotate(45deg);
    }
    .agent-menu {
      position: absolute;
      z-index: 20;
      top: calc(100% + 6px);
      left: 0;
      right: 0;
      max-height: 260px;
      overflow: auto;
      padding: 4px;
      border: 1px solid var(--cp-border);
      border-radius: 0.75rem;
      background: var(--cp-panel-strong);
      color: var(--cp-text);
      box-shadow: var(--cp-shadow);
    }
    .agent-menu[hidden] { display: none; }
    .agent-option {
      display: grid;
      gap: 2px;
      width: 100%;
      padding: 8px 10px;
      border: 0;
      border-radius: 0.5rem;
      background: transparent;
      color: var(--cp-text);
      text-align: left;
      font-weight: 500;
    }
    .agent-option:hover,
    .agent-option.active {
      background: var(--cp-accent-soft);
      color: var(--cp-text);
    }
    .agent-option-folder {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
    }
    .content {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: minmax(0, 1fr);
      gap: 8px;
      min-height: 0;
      padding: 8px 12px;
      overflow: hidden;
    }
    .content.deploy-mode {
      grid-template-rows: minmax(0, 1fr);
    }
    .view { min-height: 0; }
    .view[hidden] { display: none; }
    .panel {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      min-height: 0;
      background: var(--cp-panel);
      overflow: hidden;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 4px 10px;
      background: var(--cp-panel-strong);
    }
    .panel-title {
      font-weight: 700;
    }
    .panel-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 4px;
    }
    .meta-pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 0;
      background: transparent;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
    }
    .muted { color: var(--cp-text-muted); }
    .health {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--cp-text-muted);
      min-width: 0;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--cp-text-muted);
    }
    .dot.ok { background: var(--cp-success); }
    .dot.warn { background: var(--cp-warning); }
    .dot.fail { background: var(--cp-danger); }
    .foundry-status {
      display: grid;
      gap: 8px;
      margin: 0 2px 10px;
      padding: 10px;
      border: 1px solid var(--cp-border);
      border-radius: 16px;
      background: var(--cp-surface-soft);
    }
    .foundry-status[hidden] { display: none; }
    .foundry-status-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }
    .foundry-status-title {
      font-weight: 800;
    }
    .foundry-status-copy {
      color: var(--cp-text-muted);
      font-size: 13px;
      margin-top: 2px;
    }
    .foundry-status-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .foundry-status-item {
      min-width: 0;
      padding: 8px;
      border-radius: 12px;
      background: var(--cp-surface);
    }
    .foundry-status-label {
      color: var(--cp-text-muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }
    .foundry-status-value {
      margin-top: 3px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 700;
    }
    .foundry-status-value.endpoint {
      font-family: var(--font-mono, Consolas, "Courier New", Courier, monospace);
      font-size: 12px;
      font-weight: 500;
    }
    .transcript {
      grid-row: 3;
      height: 100%;
      overflow: auto;
      padding: 8px 2px 16px;
    }
    .empty {
      display: grid;
      place-items: center;
      min-height: 100%;
      border-radius: 16px;
      color: var(--cp-text-muted);
      text-align: center;
      padding: 24px;
      background: var(--cp-surface-soft);
    }
    .turn {
      display: grid;
      gap: 12px;
      margin-bottom: 12px;
    }
    .bubble {
      border-radius: 16px;
      background: var(--cp-surface);
      overflow: hidden;
      box-shadow: var(--cp-shadow);
    }
    .bubble.user {
      justify-self: end;
      width: min(calc(100% - 48px), 1120px);
      max-width: calc(100% - 48px);
      background: var(--cp-surface-soft);
    }
    .bubble.agent {
      justify-self: start;
      width: min(calc(100% - 48px), 1120px);
      max-width: calc(100% - 48px);
      background: var(--cp-accent-soft);
    }
    .bubble-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px 0;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 600;
    }
    .speaker {
      letter-spacing: 0.01em;
    }
    .speaker.user {
      color: var(--cp-text-muted);
    }
    .speaker.agent {
      color: var(--cp-accent);
      font-weight: 700;
    }
    .bubble-body {
      padding: 12px;
      white-space: normal;
    }
    .bubble-body .md {
      display: grid;
      gap: 6px;
    }
    .bubble-body p,
    .bubble-body h1,
    .bubble-body h2,
    .bubble-body h3,
    .bubble-body ul,
    .bubble-body ol,
    .bubble-body pre,
    .bubble-body .table-scroll {
      margin: 0;
    }
    .bubble-body h1,
    .bubble-body h2,
    .bubble-body h3 {
      color: var(--cp-text);
      font-weight: 700;
      line-height: 1.2;
    }
    .bubble-body h1 {
      font-size: 16px;
    }
    .bubble-body h2 {
      font-size: 14px;
    }
    .bubble-body h3 {
      font-size: 13px;
    }
    .bubble-body ul,
    .bubble-body ol {
      padding-left: 18px;
    }
    .bubble-body li {
      margin: 2px 0;
    }
    .bubble-body code {
      padding: 1px 4px;
      border-radius: 6px;
      background: color-mix(in srgb, var(--cp-text) 8%, transparent);
      font-size: 12px;
    }
    .bubble-body pre {
      overflow: auto;
      padding: 8px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--cp-text) 8%, transparent);
    }
    .bubble-body pre code {
      padding: 0;
      background: transparent;
    }
    .bubble-body pre.json-pre {
      border: 1px solid var(--cp-border);
      background: var(--cp-surface);
    }
    .json-key {
      color: var(--cp-accent);
      font-weight: 600;
    }
    .json-string {
      color: var(--cp-success);
    }
    .json-number {
      color: var(--cp-warning);
    }
    .json-literal {
      color: var(--cp-danger);
      font-weight: 600;
    }
    .bubble-body .table-scroll {
      overflow-x: auto;
      border: 1px solid var(--cp-border);
      border-radius: 10px;
      background: var(--cp-surface);
    }
    .bubble-body table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .bubble-body th,
    .bubble-body td {
      padding: 6px 8px;
      border-bottom: 1px solid var(--cp-border);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    .bubble-body th {
      color: var(--cp-text);
      font-weight: 700;
      background: var(--cp-surface-soft);
    }
    .bubble-body tr:last-child td {
      border-bottom: 0;
    }
    .bubble-body a {
      color: var(--cp-accent);
      text-decoration: none;
    }
    .bubble-body a:hover {
      text-decoration: underline;
    }
    .first-token {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--cp-text-muted);
      font-weight: 600;
    }
    .token-dots {
      display: inline-flex;
      gap: 3px;
    }
    .token-dots span {
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: var(--cp-text-muted);
      animation: tokenPulse 1.1s ease-in-out infinite;
      opacity: 0.45;
    }
    .token-dots span:nth-child(2) { animation-delay: 0.15s; }
    .token-dots span:nth-child(3) { animation-delay: 0.3s; }
    @keyframes tokenPulse {
      0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
      40% { transform: translateY(-3px); opacity: 1; }
    }
    .no-answer {
      color: var(--cp-text-muted);
      font-size: 13px;
      font-style: italic;
    }
    .details-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 0 12px 10px;
    }
    .details-toggle,
    .copy-answer {
      appearance: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      width: auto;
      min-height: 20px;
      padding: 0;
      border: 0;
      border-radius: 4px;
      background: transparent;
      color: var(--cp-link);
      font-size: 12px;
      font-weight: 600;
      line-height: 16px;
      text-align: left;
      text-decoration: none;
      box-shadow: none;
    }
    .details-toggle:hover,
    .copy-answer:hover:not(:disabled) {
      background: transparent;
      text-decoration: underline;
    }
    .copy-answer {
      margin-left: auto;
    }
    .copy-answer:disabled {
      color: var(--cp-text-muted);
      cursor: not-allowed;
      opacity: 0.7;
    }
    .details-toggle-icon {
      width: 0;
      height: 0;
      border-top: 3.5px solid transparent;
      border-bottom: 3.5px solid transparent;
      border-left: 5px solid currentColor;
      transform-origin: 45% 50%;
      transition: transform 120ms ease;
    }
    .details-toggle[aria-expanded="true"] .details-toggle-icon {
      transform: rotate(90deg);
    }
    .details-panel {
      padding: 0 12px 12px;
    }
    .details-panel[hidden] {
      display: none;
    }
    .details-toggle:focus-visible,
    .copy-answer:focus-visible {
      outline: 2px solid var(--cp-accent);
      outline-offset: 2px;
    }
    .details-panel pre {
      max-height: 240px;
      overflow: auto;
      margin: 0;
      padding: 10px;
      border-radius: 0.625rem;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
    }
    .deploy-view,
    .teams-view {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 12px;
      min-height: 0;
      padding: 4px 2px 16px;
    }
    .teams-view {
      grid-template-rows: auto auto;
      align-content: start;
      overflow: auto;
    }
    .deploy-summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .deploy-card {
      padding: 12px;
      border-radius: 16px;
      background: var(--cp-surface-soft);
      box-shadow: var(--cp-shadow);
    }
    .deploy-label {
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 600;
    }
    .deploy-value {
      margin-top: 4px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 700;
    }
    .terminal {
      min-height: 0;
      overflow: auto;
      margin: 0;
      max-height: none;
      background: var(--cp-surface-soft);
      box-shadow: var(--cp-shadow);
    }
    .guide-card {
      display: grid;
      gap: 12px;
      padding: 16px;
      border-radius: 16px;
      background: var(--cp-surface-soft);
      box-shadow: var(--cp-shadow);
    }
    .guide-title {
      font-size: 16px;
      font-weight: 700;
    }
    .guide-copy {
      color: var(--cp-text-muted);
    }
    .guide-steps {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .guide-steps li {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      padding: 10px;
      border-radius: 12px;
      background: var(--cp-surface);
    }
    .guide-number {
      display: inline-grid;
      place-items: center;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      background: var(--cp-accent-soft);
      color: var(--cp-accent);
      font-size: 12px;
      font-weight: 700;
    }
    .teams-gates {
      display: grid;
      gap: 10px;
      position: relative;
      margin-top: 4px;
    }
    .teams-gates::before {
      content: "";
      position: absolute;
      top: 44px;
      bottom: 44px;
      left: 23px;
      width: 2px;
      border-radius: 999px;
      background: linear-gradient(180deg, #7c3aed, #242424, #6264a7);
      opacity: 0.28;
    }
    .teams-gate {
      position: relative;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 14px;
      border: 1px solid var(--cp-border);
      border-radius: 16px;
      background: var(--cp-surface);
      box-shadow: var(--cp-shadow);
    }
    .gate-icon {
      display: inline-grid;
      place-items: center;
      width: 46px;
      height: 46px;
      border: 1px solid var(--cp-border);
      border-radius: 16px;
      background: var(--cp-panel-strong);
      color: var(--cp-accent);
      font-size: 18px;
      font-weight: 800;
      box-shadow: var(--cp-shadow);
    }
    .gate-logo {
      width: 28px;
      height: 28px;
      object-fit: contain;
    }
    .gate-title {
      font-weight: 800;
    }
    .gate-meta {
      margin-top: 2px;
      color: var(--cp-text-muted);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 0;
      background: transparent;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 600;
    }
    .badge.ok {
      background: transparent;
      color: var(--cp-success);
    }
    .badge.fail {
      background: transparent;
      color: var(--cp-danger);
    }
    .composer {
      display: grid;
      gap: 10px;
      padding: 10px 12px 12px;
      background: var(--cp-bg-elevated);
    }
    .composer-actions {
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    .right-actions {
      display: flex;
      gap: 8px;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 100;
      display: grid;
      place-items: center;
      padding: 20px;
      background: color-mix(in srgb, var(--cp-bg) 72%, transparent);
    }
    .modal-backdrop[hidden] { display: none; }
    .modal-card {
      width: min(560px, 100%);
      display: grid;
      gap: 12px;
      padding: 18px;
      border: 1px solid var(--cp-border);
      border-radius: 18px;
      background: var(--cp-panel-strong);
      box-shadow: var(--cp-shadow);
    }
    .modal-title {
      font-size: 16px;
      font-weight: 800;
    }
    .modal-copy {
      color: var(--cp-text-muted);
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
    @media (max-width: 820px) {
      .action-card,
      .foundry-status-grid,
      .deploy-summary,
      .content {
        grid-template-columns: 1fr;
      }
      .action-buttons {
        flex-wrap: wrap;
        justify-content: flex-start;
      }
      .hero-picker {
        width: 100%;
      }
      .bubble.user,
      .bubble.agent {
        margin-left: 0;
        margin-right: 0;
        max-width: calc(100% - 24px);
        width: calc(100% - 24px);
      }
    }
`;
