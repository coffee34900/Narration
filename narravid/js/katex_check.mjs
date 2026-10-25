// Compile LaTeX strings with the locally bundled KaTeX (throwOnError) and report failures.
// stdin: {"items": ["latex", ...]}   stdout: {"results": [{"ok": true} | {"ok": false, "error": "..."}]}
import { createRequire } from "module";
const require = createRequire(process.cwd() + "/");
const katex = require("katex");

let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const { items } = JSON.parse(input || "{}");
  const results = (items || []).map((tex) => {
    try {
      const html = katex.renderToString(tex, { throwOnError: true, displayMode: true, strict: "error", output: "html" });
      return { ok: true, html };
    } catch (e) {
      return { ok: false, error: String(e.message || e).slice(0, 300) };
    }
  });
  process.stdout.write(JSON.stringify({ results }));
});
