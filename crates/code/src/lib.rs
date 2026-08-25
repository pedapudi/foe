//! Built-in coding tools: `read`, `grep`, `edit`, `bash`, `session`, and
//! `python`.
//!
//! Each tool implements `foe_core::Tool` and reaches files and processes
//! only through the capability handles in `CallCtx`. docs/tools.md states
//! each tool's arguments, limits, and canonical value. The constants below
//! are the single source of those limits: the code enforces them and every
//! tool description is formatted from them, so the two cannot drift.

#![forbid(unsafe_code)]

use foe_core::Tool;
use std::path::{Path, PathBuf};

#[cfg(feature = "exec")]
mod bash;
mod diff;
mod edit;
mod grep;
#[cfg(feature = "exec")]
mod python;
mod read;
#[cfg(feature = "exec")]
mod session;
#[cfg(test)]
#[path = "handles_test.rs"]
mod testing;

/// Longest output any tool inlines into a result, in lines.
pub const OUTPUT_MAX_LINES: usize = 2_000;
/// Longest output any tool collects into a result, in characters.
pub const OUTPUT_MAX_CHARS: usize = 50 * 1024;
/// Longest line `grep` returns before clamping it, in characters.
pub const GREP_LINE_MAX_CHARS: usize = 500;
/// Matches `grep` renders when the call names no `limit`.
pub const GREP_DEFAULT_LIMIT: usize = 100;
/// Matches `grep` collects into the canonical value before it stops searching.
pub const GREP_COLLECT_MAX: usize = 10_000;
/// Match and context lines `grep` retains before it stops searching.
pub const GREP_HIT_COLLECT_MAX: usize = 20_000;
/// Heap bytes one `grep` line buffer may use for a line and its context.
pub const GREP_SEARCH_BUFFER_MAX_BYTES: usize = 8 * 1024 * 1024;
/// Diff lines `edit` renders before one elision line stands in for the
/// rest. The canonical value keeps the complete diff.
pub const EDIT_DIFF_MAX_LINES: usize = 200;
/// Bytes one `read` stream buffer holds. Reading retains this buffer and
/// the kept window, so peak memory does not grow with the file.
pub const READ_BUFFER_BYTES: usize = 64 * 1024;
/// Seconds `bash` waits when the call names no `timeout_seconds`.
pub const BASH_DEFAULT_TIMEOUT_SECS: u64 = 120;
/// Process sessions the `session` tool may hold alive at once.
pub const SESSION_MAX_ALIVE: usize = 8;
/// Absolute path of the interpreter the `python` tool starts.
pub const PYTHON_BIN: &str = "/usr/bin/python3";
/// Longest `python` program source, in bytes.
pub const PYTHON_SOURCE_MAX_BYTES: usize = 64 * 1024;
/// Inner tool calls one `python` program may dispatch.
pub const PYTHON_INNER_CALL_MAX: u32 = 100;
/// Address-space limit of the interpreter process, in bytes.
pub const PYTHON_MEMORY_MAX_BYTES: u64 = 512 << 20;
/// Characters kept of each of the interpreter's own output streams.
pub const PYTHON_DIAGNOSTIC_MAX_CHARS: usize = 4_096;

/// The shell that runs every `bash` and `session` command line.
#[cfg(feature = "exec")]
pub(crate) const SHELL: &str = "/bin/bash";

/// The complete environment of the shell, identical for `bash` and
/// `session`. The runtime sets exactly what it is given and inherits
/// nothing, so the shell needs a search path to find programs; `HOME` is
/// the working directory, since the tools have no other writable location.
#[cfg(feature = "exec")]
pub(crate) fn shell_environment(cwd: &Path) -> std::collections::BTreeMap<String, String> {
    std::collections::BTreeMap::from([
        ("PATH".to_owned(), "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin".to_owned()),
        ("HOME".to_owned(), cwd.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
    ])
}

/// Every built-in coding tool, in the order `foe plan` lists them.
pub fn all() -> Vec<Box<dyn Tool>> {
    let mut tools = readonly();
    tools.push(Box::new(edit::Edit::new()));
    tools.extend(exec_tools());
    tools
}

/// The tools whose effect is `reads`: `read` and `grep`.
pub fn readonly() -> Vec<Box<dyn Tool>> {
    vec![Box::new(read::Read::new()), Box::new(grep::Grep::new())]
}

#[cfg(feature = "exec")]
fn exec_tools() -> Vec<Box<dyn Tool>> {
    vec![Box::new(bash::Bash::new()), Box::new(session::Session::new()), Box::new(python::Python::new())]
}

#[cfg(not(feature = "exec"))]
fn exec_tools() -> Vec<Box<dyn Tool>> {
    Vec::new()
}

/// Parses the model's arguments, naming the tool in the error.
fn parse_args<T: serde::de::DeserializeOwned>(tool: &str, args: serde_json::Value) -> Result<T, foe_core::ToolValue> {
    serde_json::from_value(args).map_err(|e| foe_core::ToolValue::error(format!("{tool}: invalid arguments: {e}")))
}

/// A path from the model. Absolute paths pass through; relative paths are
/// taken from the first root.
fn resolve(roots: &[PathBuf], path: &str) -> PathBuf {
    let p = Path::new(path);
    match (p.is_absolute(), roots.first()) {
        (false, Some(root)) => root.join(p),
        _ => p.to_path_buf(),
    }
}

/// The path as shown to the model: relative to the first root when below it,
/// and `.` for the root itself.
fn display(roots: &[PathBuf], path: &Path) -> String {
    match roots.first().and_then(|r| path.strip_prefix(r).ok()) {
        Some(rel) if rel.as_os_str().is_empty() => ".".to_owned(),
        Some(rel) => rel.display().to_string(),
        None => path.display().to_string(),
    }
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
