//! The viewer's server side: a projection of an episode directory into an
//! episode tree, a loopback HTTP server that streams logs to the browser
//! bundle, and a static export of the same page.
//!
//! `docs/viewer.md` specifies the behavior. The browser bundle built under
//! `view/` is embedded at compile time; see `build.rs`.

#![forbid(unsafe_code)]

mod communication;
mod project;
mod server;
mod terminal;

pub use terminal::conversation;

pub use project::{project, Node, Tree};
pub use server::{serve, Bound, Server};

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

// The bundle is embedded deflated, a quarter of its bytes, and inflated once
// on the first page this process builds. `view/build.mjs` writes the deflated
// copies and `build.rs` embeds them.
const JS: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/viewer.js.deflate"));
const CSS: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/viewer.css.deflate"));

fn inflate(deflated: &[u8]) -> String {
    let bytes = miniz_oxide::inflate::decompress_to_vec(deflated).expect("the build deflated this");
    String::from_utf8(bytes).expect("the bundle is UTF-8")
}

/// The bundle's script. Computed once.
fn js() -> &'static str {
    static TEXT: OnceLock<String> = OnceLock::new();
    TEXT.get_or_init(|| inflate(JS))
}

// `FONTS`: the self-hosted font files by the name the bundle's CSS requests
// under `/fonts/`. `build.rs` names the files and writes the array, so the
// list has one place. A font absent at build time is embedded empty.
include!(concat!(env!("OUT_DIR"), "/fonts.rs"));

#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A log under the episode directory could not be read or parsed.
    #[error("{0}: {1}")]
    Log(PathBuf, #[source] foe_log::LogError),
    /// A socket or `/dev/urandom` operation failed; the string names it.
    #[error("{0}: {1}")]
    Io(String, #[source] std::io::Error),
}

/// Renders every episode under `dir` into one self-contained HTML page: the
/// bundle inlined and every log inlined as JSON, booted in static mode.
pub fn export(dir: &Path) -> Result<String, Error> {
    let mut store = project::Store::new(dir);
    store.poll()?;
    let mut logs = Vec::new();
    for (id, lines) in store.logs() {
        let id = serde_json::to_string(id).expect("string serializes");
        logs.push(format!("{id}:[{}]", lines.join(",")));
    }
    let tree = serde_json::to_string(&store.tree()).expect("tree serializes");
    let logs = logs.join(",");
    Ok(page(&format!("{{\"mode\":\"static\",\"episodes\":{{{logs}}},\"tree\":{tree}}}")))
}

/// Wraps the bundle around the `window.__FOE__` boot object. `<` in the
/// boot JSON is written as the escape `<` so that no text inside an
/// event can close the script element.
fn page(boot: &str) -> String {
    let (boot, css) = (boot.replace('<', "\\u003c"), css());
    format!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\
         <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\
         <title>foe</title><style>{css}</style></head><body><div id=\"app\"></div>\
         <script>window.__FOE__={boot};</script><script>{}</script></body></html>\n",
        js()
    )
}

/// The bundle's CSS with every present font inlined as a `data:` URI, so
/// that neither page fetches a font. Computed once.
fn css() -> &'static str {
    static INLINED: OnceLock<String> = OnceLock::new();
    INLINED.get_or_init(|| {
        let mut css = inflate(CSS);
        for (name, bytes) in FONTS.iter().filter(|(_, bytes)| !bytes.is_empty()) {
            let uri = format!("data:font/woff2;base64,{}", base64(bytes));
            css = css.replace(&format!("/fonts/{name}"), &uri);
        }
        css
    })
}

fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().fold(0u32, |acc, &b| (acc << 8) | b as u32) << (8 * (3 - chunk.len()));
        let sextets = (0..4).map(|i| ALPHABET[(n >> (18 - 6 * i)) as usize & 63] as char);
        out.extend(sextets.take(chunk.len() + 1));
        out.push_str(&"=="[..3 - chunk.len()]);
    }
    out
}
