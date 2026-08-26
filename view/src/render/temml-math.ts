// The Temml renderer: TeX to MathML, which browsers lay out natively, so
// the page ships no math font.
//
// This module is the bundle's only third-party dependency, and it is the
// only module that imports Temml. A build that does not call
// `installTemmlRenderer` leaves Temml out of the bundle entirely, because
// nothing else reaches it.

import { h } from "../dom.js";
import { setMathRenderer } from "./math.js";
import temml from "../../vendor/temml.min.js";

/** Installs Temml as the mathematics renderer. */
export function installTemmlRenderer(): void {
  setMathRenderer((tex, display) => {
    const host = h("span", { class: display ? "math display" : "math inline" });
    try {
      temml.render(tex, host, { displayMode: display, throwOnError: true });
    } catch {
      return null;
    }
    return host;
  });
}
