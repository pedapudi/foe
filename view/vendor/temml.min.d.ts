// The part of Temml's interface this bundle calls. The full interface is
// declared in the published package under dist/temml.d.ts.

declare const temml: {
  version: string;
  render(expression: string, target: Element, options?: { displayMode?: boolean; throwOnError?: boolean; wrap?: "none" | "tex" | "=" }): void;
};

export default temml;
