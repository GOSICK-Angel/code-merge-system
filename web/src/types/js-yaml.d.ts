// Minimal local declaration for the subset of js-yaml we use, so we don't
// pull in @types/js-yaml. js-yaml@4 ships no bundled types.
declare module "js-yaml" {
  export function load(input: string): unknown;
  export function dump(input: unknown, opts?: Record<string, unknown>): string;
}
