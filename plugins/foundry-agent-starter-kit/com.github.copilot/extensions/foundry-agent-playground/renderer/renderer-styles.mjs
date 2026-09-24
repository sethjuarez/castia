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
      --cp-success: #1f883d;
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
    html[data-theme="dark"],
    html[data-color-mode="dark"],
    body[data-color-mode="dark"] {
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
    :root {
      color-scheme: light dark;
      --cp-bg: var(--background-color-default, var(--n-0, #ffffff));
      --cp-bg-elevated: var(--background-color-subtle, var(--n-1, #f7f7f7));
      --cp-surface: var(--background-color-default, var(--n-0, #ffffff));
      --cp-surface-soft: var(--background-color-subtle, var(--n-1, #f5f5f5));
      --cp-border: var(--border-color-default, #dedede);
      --cp-border-strong: var(--border-color-muted, var(--border-color-default, #919191));
      --cp-text: var(--text-color-default, #242424);
      --cp-text-muted: var(--text-color-muted, #5c5c5c);
      --cp-text-soft: var(--text-color-muted, #6f6f6f);
      --cp-accent: var(--color-focus-outline, var(--cp-link));
      --cp-accent-hover: var(--color-focus-outline, var(--cp-link));
      --cp-accent-soft: color-mix(in srgb, var(--cp-accent) 10%, var(--cp-surface));
      --cp-accent-fg: var(--color-white, #ffffff);
      --cp-success: var(--true-color-green, #1f883d);
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
    html[data-theme="dark"],
    html[data-color-mode="dark"],
    body[data-color-mode="dark"] {
      --cp-bg: var(--background-color-default, var(--n-13, #1f1f1f));
      --cp-bg-elevated: var(--background-color-subtle, var(--n-12, #242424));
      --cp-surface: var(--background-color-default, var(--n-12, #242424));
      --cp-surface-soft: var(--background-color-subtle, var(--n-11, #2e2e2e));
      --cp-border: var(--border-color-default, var(--n-9, #474747));
      --cp-border-strong: var(--border-color-muted, var(--n-8, #5f5f5f));
      --cp-text: var(--text-color-default, var(--n-1, #dedede));
      --cp-text-muted: var(--text-color-muted, var(--n-4, #b0b0b0));
      --cp-text-soft: var(--text-color-muted, var(--n-5, #919191));
      --cp-accent: var(--color-focus-outline, var(--true-color-blue, #4da6ff));
      --cp-accent-hover: var(--color-focus-outline, var(--true-color-blue, #75bfff));
      --cp-accent-soft: color-mix(in srgb, var(--cp-accent) 16%, var(--cp-surface));
      --cp-accent-fg: var(--background-color-default, #1f1f1f);
      --cp-shadow: 0 0 2px rgba(0, 0, 0, 0.45), 0 8px 24px rgba(0, 0, 0, 0.35);
      --cp-overlay: color-mix(in srgb, var(--cp-bg) 88%, transparent);
      --cp-panel: color-mix(in srgb, var(--cp-surface) 88%, transparent);
      --cp-panel-strong: var(--cp-surface);
      --cp-sheen: color-mix(in srgb, var(--cp-text) 6%, transparent);
      --cp-highlight: var(--cp-accent-soft);
    }
    html[data-theme="light"],
    html[data-color-mode="light"],
    body[data-color-mode="light"] {
      color-scheme: light;
      --cp-bg: #f6f8fa;
      --cp-bg-elevated: #f6f8fa;
      --cp-surface: #ffffff;
      --cp-surface-soft: #f5f5f5;
      --cp-panel: #ffffff;
      --cp-panel-strong: #ffffff;
      --cp-shadow: 0 1px 2px rgba(31, 35, 40, 0.08);
      --cp-overlay: rgba(255, 255, 255, 0.92);
      --cp-sheen: #f5f5f5;
      --cp-highlight: color-mix(in srgb, var(--cp-accent) 10%, #ffffff);
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
      transition:
        background-color 120ms ease,
        border-color 120ms ease,
        box-shadow 120ms ease,
        color 120ms ease,
        transform 120ms ease;
    }
    button:hover { border-color: var(--cp-border); }
    button:active { transform: translateY(1px); }
    button:disabled { cursor: not-allowed; opacity: 0.62; }
    button.primary {
      border-color: var(--cp-accent);
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    button.primary:hover { background: var(--cp-accent-hover); }
    button.danger {
      border-color: var(--cp-danger);
      background: var(--cp-danger);
      color: var(--cp-accent-fg);
    }
    button.danger:hover { filter: brightness(0.94); }
    .icon-button {
      inline-size: 36px;
      min-inline-size: 36px;
      block-size: 36px;
      display: inline-grid;
      place-items: center;
      padding: 0;
      border-color: var(--cp-border);
      border-radius: 10px;
      background: var(--cp-surface);
      line-height: 1;
    }
    .icon-button:hover {
      border-color: var(--cp-border-strong);
      background: var(--cp-surface-soft);
    }
    .icon-button.primary {
      border-color: var(--cp-accent);
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    .icon-button.primary:hover {
      background: var(--cp-accent-hover);
    }
    .icon-button.danger {
      border-color: var(--cp-danger);
      background: var(--cp-danger);
      color: var(--cp-accent-fg);
    }
    .icon-button svg {
      inline-size: 17px;
      block-size: 17px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      position: relative;
      z-index: 1;
    }
    .action-buttons .icon-button {
      inline-size: 40px;
      min-inline-size: 40px;
      block-size: 40px;
    }
    .action-buttons .icon-button svg {
      inline-size: 18px;
      block-size: 18px;
    }
    input, textarea {
      width: 100%;
      border: 1px solid var(--cp-border);
      border-radius: 0.625rem;
      padding: 10px 12px;
      background: var(--cp-surface);
      color: var(--cp-text);
    }
    textarea { min-height: 68px; resize: none; }
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
      gap: clamp(8px, 1.1vw, 14px);
      padding: clamp(10px, 1.2vw, 16px);
      background: var(--cp-bg-elevated);
    }
    .journey {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(96px, 0.42fr);
      gap: clamp(8px, 1vw, 14px);
    }
    .step {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-width: 0;
      min-height: 46px;
      padding: 10px 12px;
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
    .step.auxiliary {
      opacity: 0.68;
    }
    .step.auxiliary.active,
    .step.auxiliary.done,
    .step.auxiliary:hover {
      opacity: 1;
    }
    .step-index {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      font-size: 11px;
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
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      }
      .step.auxiliary {
        display: none;
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
      grid-template-columns: minmax(16rem, 1fr) minmax(22rem, auto);
      gap: clamp(10px, 1.2vw, 16px);
      align-items: center;
      padding: 12px 16px;
      border-radius: 16px;
      background: var(--cp-panel-strong);
      box-shadow: var(--cp-shadow);
    }
    .action-summary {
      display: grid;
      grid-template-columns: minmax(0, auto) minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-width: 0;
    }
    .action-heading {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      max-width: min(42ch, 100%);
    }
    .action-title {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 800;
      line-height: 18px;
      flex: 0 1 auto;
    }
    .action-detail-trigger {
      position: relative;
      flex: 0 0 auto;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      padding: 1px 7px;
      background: var(--cp-surface-soft);
      color: var(--cp-text-muted);
      font-size: 11px;
      font-weight: 800;
      cursor: help;
    }
    .action-detail-trigger:focus {
      outline: 2px solid var(--cp-accent);
      outline-offset: 2px;
    }
    .action-copy {
      position: absolute;
      z-index: 30;
      top: calc(100% + 6px);
      left: 0;
      width: min(420px, 72vw);
      padding: 8px 10px;
      border: 1px solid var(--cp-border);
      border-radius: 10px;
      background: var(--cp-panel-strong);
      box-shadow: var(--cp-shadow);
      color: var(--cp-text-muted);
      font-size: 13px;
      font-weight: 500;
      line-height: 18px;
      opacity: 0;
      pointer-events: none;
      visibility: hidden;
    }
    .action-detail-trigger:hover .action-copy,
    .action-detail-trigger:focus .action-copy,
    .action-detail-trigger:focus-within .action-copy {
      opacity: 1;
      visibility: visible;
    }
    .status-row {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      min-height: 0;
      overflow: visible;
    }
    .action-state-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
      max-width: 12rem;
      min-height: 20px;
      padding: 2px 8px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: var(--cp-surface-soft);
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 800;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .action-state-chip::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--cp-text-muted);
      flex: 0 0 auto;
    }
    .action-state-chip.ok::before { background: var(--cp-success); }
    .action-state-chip.warn::before { background: var(--cp-warning); }
    .action-state-chip.fail::before { background: var(--cp-danger); }
    .local-endpoint-banner {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      flex: 0 1 auto;
      min-width: 0;
      max-width: min(100%, 420px);
      padding: 2px 7px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
      font-size: 12px;
      min-height: 20px;
    }
    .local-endpoint-banner[hidden] { display: none; }
    .local-endpoint-banner.warn {
      border-color: color-mix(in srgb, var(--cp-warning) 50%, var(--cp-border));
      background: color-mix(in srgb, var(--cp-warning) 12%, var(--cp-surface));
    }
    .local-endpoint-banner code {
      font-size: 12px;
      white-space: nowrap;
    }
    .endpoint-compact {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      max-width: 100%;
    }
    .endpoint-label {
      color: var(--cp-text-muted);
      font-weight: 700;
    }
    .endpoint-meta {
      color: var(--cp-text-muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .endpoint-warning {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      color: var(--cp-text-muted);
      white-space: nowrap;
    }
    .endpoint-warning-text {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .endpoint-warning button {
      flex: 0 0 auto;
      padding: 4px 8px;
    }
    .local-ticker {
      position: relative;
      display: inline-flex;
      gap: 6px;
      align-items: center;
      min-width: 0;
      max-width: min(100%, 380px);
      padding: 2px 7px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: var(--cp-surface-soft);
      color: var(--cp-text-muted);
      font-size: 12px;
      min-height: 20px;
    }
    .local-ticker[hidden] { display: none; }
    .deploy-ticker {
      position: relative;
      display: inline-flex;
      min-width: 0;
      max-width: min(100%, 520px);
      padding: 2px 7px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: var(--cp-surface-soft);
      color: var(--cp-text-muted);
      font-size: 12px;
      min-height: 20px;
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
    .local-endpoint-banner[data-detail]::after,
    .local-ticker[data-detail]::after,
    .deploy-ticker[data-detail]::after {
      content: attr(data-detail);
      position: absolute;
      z-index: 30;
      top: calc(100% + 6px);
      left: 0;
      width: min(520px, 72vw);
      max-width: 72vw;
      padding: 8px 10px;
      border: 1px solid var(--cp-border);
      border-radius: 10px;
      background: var(--cp-panel-strong);
      color: var(--cp-text-muted);
      box-shadow: var(--cp-shadow);
      line-height: 18px;
      opacity: 0;
      pointer-events: none;
      white-space: normal;
      visibility: hidden;
    }
    .local-endpoint-banner:hover::after,
    .local-endpoint-banner:focus-within::after,
    .local-ticker:hover::after,
    .local-ticker:focus-within::after,
    .deploy-ticker:hover::after,
    .deploy-ticker:focus-within::after {
      opacity: 1;
      visibility: visible;
    }
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
      justify-content: flex-end;
      min-width: 0;
    }
    .hero-picker {
      flex: 1 1 20rem;
      width: auto;
      min-width: 14rem;
      max-width: 28rem;
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
      gap: clamp(8px, 1vw, 12px);
      min-height: 0;
      padding: 10px 16px;
      background: var(--cp-surface);
      overflow: hidden;
    }
    .content.deploy-mode {
      grid-template-rows: minmax(0, 1fr);
    }
    .view { min-height: 0; }
    .view[hidden] { display: none; }
    .panel {
      display: grid;
      grid-template-rows: auto auto auto minmax(0, 1fr);
      min-height: 0;
      position: relative;
      background: var(--cp-panel);
      overflow: hidden;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 4px 6px;
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
    .protocol-toggle {
      display: inline-flex;
      flex-wrap: wrap;
      gap: 4px;
      justify-content: flex-start;
      min-width: 0;
    }
    .protocol-tab {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      min-height: 26px;
      padding: 4px 8px;
      border-radius: 999px;
      border-color: var(--cp-border);
      background: var(--cp-surface-soft);
      font-size: 11px;
    }
    .protocol-tab.active {
      border-color: var(--cp-accent);
      background: var(--cp-accent-soft);
      color: var(--cp-text);
    }
    .protocol-tab.unsupported,
    .protocol-tab.unknown {
      opacity: 0.6;
    }
    .protocol-state {
      color: var(--cp-text-muted);
      font-size: 11px;
      font-weight: 500;
    }
    .muted { color: var(--cp-text-muted); }
    .health {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--cp-text-muted);
      font-size: 12px;
      min-width: 0;
    }
    #statusText {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .dot {
      flex: 0 0 auto;
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--cp-text-muted);
    }
    .dot.ok { background: var(--cp-success); }
    .dot.warn { background: var(--cp-warning); }
    .dot.fail { background: var(--cp-danger); }
    .foundry-status {
      display: grid;
      gap: 6px;
      margin: 0 0 5px;
      padding: 4px 6px;
      border: 1px solid var(--cp-border);
      border-radius: 12px;
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
      padding: 5px 6px;
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
    .activity-log {
      position: absolute;
      right: 12px;
      bottom: 12px;
      z-index: 8;
      width: auto;
      max-width: min(34rem, calc(100% - 24px));
      margin: 0;
      padding: 0;
      border: 0;
      background: transparent;
    }
    .activity-log[hidden] { display: none; }
    .activity-log[open] {
      display: grid;
      left: 12px;
      right: 12px;
      bottom: 12px;
      gap: 8px;
      width: auto;
      max-width: none;
      max-height: min(520px, 72vh);
      padding: 10px 12px;
      border: 1px solid var(--cp-border);
      border-radius: 12px;
      background: var(--cp-surface);
      box-shadow: var(--cp-shadow);
    }
    .activity-head {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      list-style: none;
      min-width: 0;
      min-height: 24px;
      padding: 3px 6px;
      border: 1px solid var(--cp-border);
      border-radius: 6px;
      background: var(--cp-surface);
      font-family: var(--font-mono, Consolas, "Courier New", Courier, monospace);
      box-shadow: inset 3px 0 0 var(--cp-border-strong);
    }
    .activity-log:not([open]) .activity-head {
      width: 36px;
      height: 36px;
      grid-template-columns: 1fr;
      place-items: center;
      padding: 0;
      border-radius: 999px;
      background: color-mix(in srgb, var(--cp-surface) 92%, transparent);
      box-shadow: var(--cp-shadow);
      opacity: 0.86;
    }
    .activity-log:not([open]) .activity-head > div,
    .activity-log:not([open]) .activity-head .badge {
      display: none;
    }
    .activity-log[open] .activity-head {
      grid-template-columns: minmax(0, 1fr) auto auto;
      min-height: 44px;
      padding: 5px 6px 5px 10px;
      border: 1px solid var(--cp-border);
      border-radius: 10px;
      background: var(--cp-surface-soft);
      box-shadow: none;
    }
    .activity-log[open] .activity-head::before {
      content: "";
      display: none;
    }
    .activity-log[open] .activity-head > div {
      order: 1;
    }
    .activity-log[open] .activity-head .badge {
      order: 2;
    }
    .activity-log[open] .activity-icon {
      order: 3;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 34px;
      padding: 0 12px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: var(--cp-surface);
      color: var(--cp-text);
      font-family: var(--font-sans, "Segoe UI", Aptos, Calibri, sans-serif);
      font-size: 12px;
      font-weight: 800;
    }
    .activity-log[open] .activity-icon::after {
      content: "Minimize";
    }
    .activity-icon {
      display: inline-grid;
      place-items: center;
      color: var(--cp-text-muted);
    }
    .activity-icon svg {
      width: 16px;
      height: 16px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .activity-head > div {
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }
    .activity-log:not([open]) .activity-summary {
      display: none;
    }
    .activity-head::-webkit-details-marker {
      display: none;
    }
    .activity-head::before {
      content: "▸";
      color: var(--cp-text-muted);
      font-size: 12px;
      line-height: 1;
    }
    .activity-log:not([open]) .activity-head::before {
      content: "";
      display: none;
    }
    .activity-log[open] .activity-head::before {
      content: "▾";
    }
    .activity-title {
      font-weight: 800;
      font-size: 12px;
      color: var(--cp-text);
      text-transform: lowercase;
      white-space: nowrap;
    }
    .activity-title::after {
      content: "$";
      margin-left: 6px;
      color: var(--cp-text-muted);
    }
    .activity-summary {
      color: var(--cp-text-muted);
      font-size: 12px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .activity-items {
      display: grid;
      gap: 0;
      max-height: min(440px, 58vh);
      overflow: auto;
      padding: 4px 2px 0 14px;
      border-top: 1px solid var(--cp-border);
      font-family: var(--font-mono, Consolas, "Courier New", Courier, monospace);
    }
    .activity-item {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-width: 0;
      padding: 3px 0;
      border-radius: 0;
      background: transparent;
      color: var(--cp-text-muted);
      font-size: 12px;
    }
    .activity-item.running .ticker-mark,
    .activity-item.warn .ticker-mark { background: var(--cp-warning); }
    .activity-item.completed .ticker-mark { background: var(--cp-success); }
    .activity-item.failed .ticker-mark { background: var(--cp-danger); }
    .activity-main {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .activity-actor {
      color: var(--cp-text);
      font-weight: 800;
    }
    .activity-time {
      white-space: nowrap;
      color: var(--cp-text-muted);
      font-variant-numeric: tabular-nums;
    }
    .transcript {
      grid-row: 4;
      height: 100%;
      overflow: auto;
      padding: 6px 10px 32px;
    }
    .transcript.empty-state {
      display: flex;
      height: 100%;
      overflow: auto;
      padding: 6px 10px 28px;
    }
    .empty {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      flex-wrap: wrap;
      min-height: 100%;
      width: 100%;
      border-radius: 12px;
      color: var(--cp-text-muted);
      text-align: center;
      padding: 8px 12px;
      background: var(--cp-surface-soft);
      font-size: 13px;
    }
    html[data-theme="light"] .hero,
    html[data-color-mode="light"] .hero,
    body[data-color-mode="light"] .hero {
      border-bottom: 1px solid var(--cp-border);
    }
    html[data-theme="light"] .step,
    html[data-color-mode="light"] .step,
    body[data-color-mode="light"] .step,
    html[data-theme="light"] .action-card,
    html[data-color-mode="light"] .action-card,
    body[data-color-mode="light"] .action-card {
      border: 1px solid var(--cp-border);
    }
    html[data-theme="light"] .action-card,
    html[data-color-mode="light"] .action-card,
    body[data-color-mode="light"] .action-card,
    html[data-theme="light"] .step.active,
    html[data-color-mode="light"] .step.active,
    body[data-color-mode="light"] .step.active {
      box-shadow: var(--cp-shadow);
    }
    html[data-theme="light"] .empty,
    html[data-color-mode="light"] .empty,
    body[data-color-mode="light"] .empty {
      border: 1px solid var(--cp-border);
      background: var(--cp-surface);
    }
    .turn,
    responses-turn,
    activity-turn,
    invocation-turn {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
    }
    responses-turn,
    activity-turn,
    invocation-turn {
      contain: layout style;
    }
    .bubble {
      border-radius: 11px;
      background: var(--cp-surface);
      overflow: hidden;
      box-shadow: var(--cp-shadow);
    }
    .bubble.user {
      justify-self: end;
      width: min(calc(100% - 64px), 1120px);
      max-width: calc(100% - 64px);
      background: var(--cp-surface-soft);
    }
    .bubble.agent {
      justify-self: start;
      width: min(calc(100% - 64px), 1120px);
      max-width: calc(100% - 64px);
      background: var(--cp-accent-soft);
    }
    .activity-turn,
    activity-turn {
      gap: 4px;
    }
    .teams-thread {
      display: grid;
      gap: 8px;
    }
    .teams-row {
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 8px;
      align-items: end;
    }
    .teams-row.outgoing {
      grid-template-columns: minmax(0, 1fr) 32px;
      justify-items: end;
    }
    .teams-stack {
      display: grid;
      gap: 4px;
      min-width: 0;
      width: 100%;
    }
    .teams-row.outgoing .teams-stack {
      justify-items: end;
    }
    .avatar {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      border: 1px solid var(--cp-border);
      background: var(--cp-surface);
      color: var(--cp-text-muted);
      font-size: 11px;
      font-weight: 800;
      box-shadow: var(--cp-shadow);
    }
    .user-avatar {
      width: 32px;
      height: 32px;
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    .agent-avatar {
      align-self: start;
      background: color-mix(in srgb, var(--cp-accent) 18%, var(--cp-surface));
      color: var(--cp-accent);
    }
    invocation-turn .invocation-result {
      border-left: 3px solid var(--cp-accent);
    }
    .bubble.activity-event {
      background: transparent;
      box-shadow: none;
    }
    .bubble.deleted {
      opacity: 0.72;
      background: var(--cp-surface-soft);
    }
    .teams-message {
      border-bottom-left-radius: 4px;
    }
    .teams-row.outgoing .bubble.user {
      border-bottom-right-radius: 4px;
    }
    .typing-pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      margin: 0 0 0 8px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      padding: 4px 9px;
      color: var(--cp-text-muted);
      background: var(--cp-surface);
      font-size: 12px;
      font-weight: 600;
    }
    .teams-typing {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 28px;
    }
    .teams-typing .avatar {
      display: none;
    }
    .typing-dots {
      display: inline-flex;
      gap: 3px;
    }
    .typing-dots span {
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: currentColor;
      opacity: 0.45;
      animation: tokenPulse 1.1s ease-in-out infinite;
    }
    .typing-dots span:nth-child(2) { animation-delay: 0.15s; }
    .typing-dots span:nth-child(3) { animation-delay: 0.3s; }
    .reaction-row {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding: 0 9px 6px;
      margin-top: -2px;
    }
    .reaction {
      display: inline-grid;
      place-items: center;
      min-width: 24px;
      min-height: 22px;
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: var(--cp-surface);
      font-size: 13px;
      box-shadow: var(--cp-shadow);
    }
    .bubble-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 16px 0;
      color: var(--cp-text-muted);
      font-size: 11px;
      font-weight: 600;
      line-height: 15px;
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
      padding: 10px 16px 14px;
      white-space: normal;
    }
    .bubble-body .md {
      display: grid;
      gap: 10px;
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
    .bubble-body .mermaid-diagram,
    .bubble-body .mermaid-fallback {
      margin: 0;
      overflow-x: auto;
      border: 1px solid var(--cp-border);
      border-radius: 12px;
      background: var(--cp-surface);
    }
    .bubble-body .mermaid-diagram {
      position: relative;
      padding: 10px;
    }
    .mermaid-toolbar {
      position: absolute;
      top: 6px;
      right: 6px;
      z-index: 2;
      display: flex;
      justify-content: flex-end;
      margin: 0;
    }
    .mermaid-expand-button,
    .mermaid-lightbox-close,
    .mermaid-lightbox-controls button {
      border: 1px solid var(--cp-border);
      border-radius: 999px;
      background: color-mix(in srgb, var(--cp-surface) 92%, transparent);
      color: var(--cp-text);
      font: inherit;
      font-size: 12px;
      font-weight: 600;
      line-height: 1;
      padding: 6px 10px;
      cursor: pointer;
      box-shadow: var(--cp-shadow);
    }
    .mermaid-expand-button {
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      font-size: 14px;
    }
    .mermaid-expand-button:hover,
    .mermaid-lightbox-close:hover,
    .mermaid-lightbox-controls button:hover {
      border-color: var(--cp-accent);
      color: var(--cp-accent);
    }
    .mermaid-expand-button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    .bubble-body .mermaid-diagram svg {
      display: block;
      min-width: 320px;
      width: 100%;
      max-height: 360px;
    }
    .mermaid-node rect {
      fill: var(--cp-surface-soft);
      stroke: var(--cp-accent);
      stroke-width: 1.5;
    }
    .mermaid-node text,
    .mermaid-edge-label {
      fill: var(--cp-text);
      font-family: var(--font-sans, "Segoe UI", sans-serif);
      font-size: 12px;
      text-anchor: middle;
      dominant-baseline: middle;
    }
    .mermaid-edge {
      fill: none;
      stroke: var(--cp-text-muted);
      stroke-width: 1.6;
    }
    .mermaid-edge-dotted {
      stroke-dasharray: 4 4;
    }
    .mermaid-diagram marker path {
      fill: var(--cp-text-muted);
    }
    .mermaid-lightbox-backdrop {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: rgba(0, 0, 0, 0.5);
    }
    .mermaid-lightbox {
      width: min(90vw, 1120px);
      height: min(82vh, 860px);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid var(--cp-border);
      border-radius: 16px;
      background: var(--cp-panel);
      color: var(--cp-text);
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.25);
    }
    .mermaid-lightbox-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--cp-border);
    }
    .mermaid-lightbox-help {
      margin-top: 2px;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
    }
    .mermaid-lightbox-controls {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 6px;
    }
    .mermaid-lightbox-body {
      position: relative;
      flex: 1;
      min-height: 0;
      overflow: hidden;
      padding: 0;
      background: var(--cp-surface);
      cursor: grab;
      touch-action: none;
      user-select: none;
    }
    .mermaid-lightbox-body.is-panning {
      cursor: grabbing;
    }
    .mermaid-lightbox-canvas {
      position: absolute;
      top: 0;
      left: 0;
      transform-origin: 0 0;
      will-change: transform;
    }
    .mermaid-lightbox-canvas svg {
      display: block;
    }
    .mermaid-fallback figcaption {
      padding: 8px;
      border-bottom: 1px solid var(--cp-border);
      color: var(--cp-danger);
      font-weight: 600;
    }
    .bubble-body .mermaid-fallback pre {
      margin: 0;
      border-radius: 0;
      background: color-mix(in srgb, var(--cp-danger) 8%, transparent);
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
      gap: 8px;
      padding: 0 9px 6px;
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
      background: linear-gradient(180deg, var(--cp-accent), var(--cp-border-strong), var(--cp-link));
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
    .a365-logo {
      opacity: 0.88;
      filter: saturate(0.9) contrast(0.96);
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
      padding: 12px 16px 14px;
      background: var(--cp-bg-elevated);
    }
    .composer-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }
    .plugin-version {
      min-width: 0;
      color: var(--cp-text-muted);
      font-family: var(--font-mono, Consolas, "Courier New", Courier, monospace);
      font-size: 11px;
      line-height: 1;
      opacity: 0.72;
      white-space: nowrap;
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
      button {
        padding: 6px 9px;
      }
      textarea {
          min-height: 48px;
      }
      .hero {
          gap: 8px;
          padding: 10px 12px;
      }
      .journey {
        gap: 6px;
      }
      .step {
        min-height: 44px;
        gap: 8px;
        padding: 8px 10px;
        border-radius: 12px;
      }
      .step-index {
        width: 24px;
        height: 24px;
        font-size: 11px;
      }
      .step-title {
        font-size: 12px;
      }
      .step-subtitle {
        display: none;
      }
      .deploy-summary,
      .content {
        grid-template-columns: 1fr;
      }
      .action-card {
        grid-template-columns: minmax(0, 1fr) minmax(12rem, 0.8fr);
        gap: 8px;
        padding: 10px 12px;
        border-radius: 12px;
      }
      .action-heading {
        max-width: 100%;
      }
      .action-card > div:first-child {
        min-width: 0;
      }
      .action-title {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .action-copy { font-size: 12px; }
      .action-buttons {
        justify-content: flex-end;
        gap: 6px;
      }
      .hero-picker {
        flex-basis: 12rem;
        min-width: 11rem;
      }
      .content {
        gap: 6px;
        padding: 8px 14px;
      }
      .panel-header {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px 8px;
        padding: 4px 10px 6px;
      }
      .panel-meta {
        gap: 5px;
        margin-top: 2px;
      }
      .health {
        flex: 1 1 12rem;
        align-items: center;
        font-size: 12px;
      }
      .foundry-status,
      .activity-log {
        border-radius: 6px;
      }
      .activity-log {
        margin-inline-end: 10px;
      }
      .activity-log[open] {
        gap: 3px;
        padding: 4px 6px;
      }
      .activity-log[open] .activity-head {
        min-height: 40px;
      }
      .activity-log[open] .activity-icon {
        min-height: 32px;
        padding-inline: 10px;
      }
      .foundry-status-grid {
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 5px;
      }
      .foundry-status-item {
        padding: 5px;
      }
      .foundry-status-title,
      .activity-title {
        font-size: 13px;
      }
      .foundry-status-copy,
      .activity-summary {
        font-size: 12px;
      }
      .activity-log:not([open]) .activity-items {
        max-height: 56px;
      }
      .activity-log[open] .activity-items {
        max-height: min(440px, 58vh);
      }
      .activity-item {
        grid-template-columns: auto minmax(0, 1fr);
        padding: 6px;
      }
      .activity-time {
        display: none;
      }
      .transcript {
        padding: 6px 10px 28px;
      }
      .turn {
        gap: 8px;
        margin-bottom: 10px;
      }
      .bubble.user,
      .bubble.agent {
        max-width: calc(100% - 28px);
        width: calc(100% - 28px);
        border-radius: 12px;
      }
      .bubble.user {
        margin-left: auto;
        margin-right: 0;
      }
      .bubble.agent {
        margin-left: 0;
        margin-right: auto;
      }
      .bubble-head {
        padding: 8px 14px 0;
      }
      .bubble-body {
        padding: 9px 14px 12px;
      }
      .details-row {
        padding: 0 14px 8px;
      }
      .composer {
        gap: 8px;
        padding: 10px 12px 12px;
      }
      .composer-actions {
        flex-wrap: wrap;
      }
      .plugin-version {
        align-self: center;
      }
      .right-actions {
        flex-wrap: wrap;
        justify-content: flex-end;
        margin-left: auto;
      }
    }
    @media (max-height: 760px) {
      textarea {
        min-height: 40px;
      }
      .hero {
        gap: 6px;
        padding-block: 8px;
      }
      .step {
        min-height: 38px;
        padding-block: 7px;
      }
      .action-card {
        padding-block: 9px;
      }
      .foundry-status-grid {
        display: none;
      }
      .activity-items {
        display: none;
      }
      .activity-log[open] .activity-items {
        display: grid;
        max-height: min(340px, 50vh);
      }
      .activity-log {
        padding-block: 0;
      }
      .activity-log:not([open]) {
        padding-block: 0;
      }
      .content {
        padding-block: 8px;
      }
      .composer {
        padding-block: 10px;
      }
    }
    @media (max-width: 520px) {
      .foundry-status-grid {
        display: none;
      }
      .activity-items {
        display: none;
      }
      .foundry-status-head,
      .activity-head {
        align-items: center;
      }
      .foundry-status-copy,
      .activity-summary {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .action-card {
        grid-template-columns: 1fr;
      }
      .action-summary {
        grid-template-columns: 1fr;
        gap: 4px;
      }
      .action-buttons {
        flex-wrap: wrap;
        justify-content: flex-start;
      }
      .hero-picker {
        flex: 1 1 100%;
        width: 100%;
        max-width: none;
      }
      .action-buttons > button,
      .right-actions > button {
        flex: 1 1 auto;
      }
    }
`;
