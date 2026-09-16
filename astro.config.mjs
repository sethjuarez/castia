import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  site: "https://sethjuarez.github.io",
  base: "/castia",
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "Castia",
      favicon: "/castia/assets/castia-mark.svg",
      customCss: ["./src/styles/starlight.css"],
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/sethjuarez/castia",
        },
      ],
      sidebar: [
        {
          label: "Start",
          items: [
            { label: "Install", slug: "install" },
            { label: "Copilot extras", slug: "copilot-extras" },
            { label: "Copilot App", slug: "copilot-app" },
          ],
        },
        {
          label: "Reference",
          items: [
            { label: "Framework map", slug: "framework" },
            { label: "Customize additions", slug: "customize" },
            { label: "Color tokens", slug: "color-tokens" },
          ],
        },
      ],
      head: [
        {
          tag: "script",
          content: `(() => {
  const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
  const theme =
    param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
})();`,
        },
      ],
    }),
  ],
});
