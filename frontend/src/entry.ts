import { start } from "./main.js";
import { registerServiceWorker } from "./serviceWorker.js";

registerServiceWorker();
const root = document.getElementById("app");
if (root) void start(root).catch(console.error);
