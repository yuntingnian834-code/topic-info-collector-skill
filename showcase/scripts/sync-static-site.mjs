import { copyFile, mkdir, unlink } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.resolve(scriptDirectory, "..");
const publicDirectory = path.join(projectDirectory, "public");

const files = [
  "shu-signal-draft.html",
  "intelligence.html",
  "intelligence-detail.html",
  "event-timeline.html",
  "daily-brief.html",
  "shu-signal-pages.css",
  "analytics.js",
  "monitoring-dashboard.html",
  "monitoring-dashboard.js",
];

await mkdir(publicDirectory, { recursive: true });
// Production serves this path dynamically from Feishu. Remove an older copied
// snapshot so it cannot shadow the server route during a build.
await unlink(path.join(publicDirectory, "shu-signal-data.js")).catch(
  (error) => {
    if (error.code !== "ENOENT") throw error;
  },
);
await Promise.all(
  files.map((filename) =>
    copyFile(
      path.join(projectDirectory, filename),
      path.join(publicDirectory, filename),
    ),
  ),
);

console.log(`Synced ${files.length} SHU SIGNAL public files; data stays dynamic.`);
