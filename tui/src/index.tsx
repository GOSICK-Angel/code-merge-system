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

const isTTY = process.stdout.isTTY === true;
const ENTER_ALT_SCREEN = "\x1b[?1049h\x1b[H";
const LEAVE_ALT_SCREEN = "\x1b[?1049l";

if (isTTY) {
  process.stdout.write(ENTER_ALT_SCREEN);
}

let screenRestored = false;
const restoreScreen = () => {
  if (isTTY && !screenRestored) {
    screenRestored = true;
    process.stdout.write(LEAVE_ALT_SCREEN);
  }
};

process.on("exit", restoreScreen);
process.on("SIGINT", () => {
  restoreScreen();
  process.exit(130);
});
process.on("SIGTERM", () => {
  restoreScreen();
  process.exit(143);
});

const { waitUntilExit } = render(<App wsUrl={wsUrl} />, {
  patchConsole: false,
});

waitUntilExit().then(() => {
  restoreScreen();
  process.exit(0);
});
