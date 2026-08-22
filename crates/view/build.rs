//! Embeds the browser bundle. `view/dist/viewer.js`, `view/dist/viewer.css`,
//! and the font files under `view/fonts/` are copied into `OUT_DIR` when
//! present. An absent script or stylesheet is replaced by a placeholder that
//! names the build command; an absent font is replaced by an empty file,
//! which the crate treats as "not embedded". The crate therefore compiles
//! without Node installed.
//!
//! Cargo reruns this script whenever a watched path changes. A watched path
//! that does not exist counts as changed on every build, so a checkout
//! without the bundle recompiles this crate each time; the cost is small.

use std::fs;
use std::path::{Path, PathBuf};

const JS_PLACEHOLDER: &str = "document.getElementById(\"app\").textContent = \
    \"The viewer bundle was not built. Run `pnpm install && pnpm build` in view/, then rebuild foe.\";";
const CSS_PLACEHOLDER: &str = "body{font-family:system-ui,sans-serif;margin:2rem}";
const FONTS: [&str; 6] = [
    "Inconsolata-Regular.woff2",
    "Inconsolata-Bold.woff2",
    "iAWriterMonoS-Regular.woff2",
    "iAWriterMonoS-Bold.woff2",
    "JetBrainsMono-Regular.woff2",
    "JetBrainsMono-Bold.woff2",
];

fn main() {
    let out = PathBuf::from(std::env::var_os("OUT_DIR").expect("cargo sets OUT_DIR for build scripts"));
    let manifest = std::env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is absent");
    let view = Path::new(&manifest).join("../../view");
    println!("cargo:rerun-if-changed=build.rs");
    let copy = |src: &Path, placeholder: &[u8]| {
        println!("cargo:rerun-if-changed={}", src.display());
        let body = fs::read(src).unwrap_or_else(|_| placeholder.to_vec());
        fs::write(out.join(src.file_name().expect("file name")), body).expect("write into OUT_DIR");
    };
    copy(&view.join("dist/viewer.js"), JS_PLACEHOLDER.as_bytes());
    copy(&view.join("dist/viewer.css"), CSS_PLACEHOLDER.as_bytes());
    for name in FONTS {
        copy(&view.join("fonts").join(name), b"");
    }
    // The crate includes this array rather than repeating the list, so
    // FONTS above is the one place a self-hosted font is named.
    let entries: String = FONTS
        .iter()
        .map(|name| format!("    ({name:?}, include_bytes!(concat!(env!(\"OUT_DIR\"), \"/{name}\")) as &[u8]),\n"))
        .collect();
    let array = format!("const FONTS: [(&str, &[u8]); {}] = [\n{entries}];\n", FONTS.len());
    fs::write(out.join("fonts.rs"), array).expect("write fonts.rs into OUT_DIR");
}
