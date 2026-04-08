import React from "react";
import { render } from "ink";
import { App } from "./app.js";

const args = process.argv.slice(2);
let wsPort = 8765;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--ws-port" && args[i + 1]) {
    wsPort = parseInt(args[i + 1]!, 10);
    i++;
  }
}

const wsUrl = `ws://localhost:${wsPort}`;

const { waitUntilExit } = render(<App wsUrl={wsUrl} />, {
  patchConsole: true,
});

waitUntilExit().then(() => {
  process.exit(0);
});
