export function resolveWsUrl(): string {
  const params = new URLSearchParams(window.location.search);
  const wsPort = params.get("ws") ?? "8765";
  return `ws://${window.location.hostname}:${wsPort}`;
}
