//! Every model-visible string the runtime itself contributes, as versioned
//! constants. Identity hashes all of them, so a change to any text here
//! changes the identity of every program, which is the intended effect:
//! the model would see different text.
//!
//! Templates carry `{name}` placeholders that [`fill`] substitutes. The
//! template text, with the placeholders, is what identity hashes.

/// Bumped whenever a constant in this module changes meaning rather than
/// wording. Hashed with the texts.
pub const VERSION: u32 = 1;

pub const BLOCK_NAME: &str = "block";
pub const BLOCK_DESCRIPTION: &str = "Report that the task cannot proceed, and end the episode. Use it when the \
task cannot be completed as stated (code goal-unreachable), when the task admits incompatible readings (code \
ambiguous-task), or when the task needs a tool or permission this program lacks (code missing-capability). Give \
the code and one paragraph stating what is missing or unclear.";

pub const RETURN_NAME: &str = "return";
pub const RETURN_DESCRIPTION: &str = "Finish the task by returning its result. The value must conform to the \
declared schema. A conforming value ends the episode; a value that does not conform is rejected with the \
reason, and the episode continues.";

/// Shown as a system inbox item when the model finishes a turn without
/// calling `return` although the program requires a returned value.
pub const RETURN_REQUIRED: &str = "This task is finished by calling the `return` tool with a value that \
conforms to its schema. Call `return` when the result is ready.";

/// Frames verifier findings fed back as an inbox item with source `verify`.
pub const VERIFY_FINDINGS: &str = "Verification by `{tool}` reported the findings below. Resolve each finding, \
then finish again.\n\n{findings}";

/// Result text for every tool call in a response that hit the provider's
/// output length limit. None of those calls ran.
pub const LENGTH_LIMIT_ERROR: &str = "The response reached the output length limit before it ended, so no tool \
call in it ran. Reissue each call in a shorter response.";

/// Result text for a tool call whose request was interrupted before the
/// call ran.
pub const INTERRUPTED_RESULT: &str = "The request was interrupted before this call ran; no result was recorded.";

/// Result text when the canonical value was written to the spill directory.
pub const SPILL_FRAME: &str = "The result was {bytes} bytes, which exceeds the inline limit. The complete value \
is stored in the file {path}. The first {head_bytes} bytes follow.\n\n{head}";

/// Appended to a configured executable's standard output when it wrote to
/// standard error, when it exited, and when it was killed at its timeout.
pub const EXEC_STDERR: &str = "\n[stderr]\n{stderr}";
pub const EXEC_EXIT: &str = "\n[exit code {code}]";
pub const EXEC_TIMED_OUT: &str = "\n[killed after {seconds} seconds]";

pub const UNKNOWN_TOOL: &str = "No tool named `{name}` is available to this program.";
pub const INVALID_ARGS: &str = "The arguments for `{name}` are invalid: {reason}";

/// Instruction sections are joined by this separator, in key order.
pub const SECTION_SEPARATOR: &str = "\n\n";
/// Precedes the per-tool instructions when any tool declares one.
pub const TOOL_INSTRUCTIONS_HEADING: &str = "# Tool instructions";
pub const TOOL_INSTRUCTION_TEMPLATE: &str = "## {name}\n\n{instruction}";

/// Every constant above, by name, in a fixed order. Identity hashes this
/// list together with the result text seeding writes, which lives in the
/// log crate.
pub fn all() -> Vec<(&'static str, &'static str)> {
    vec![
        ("block.description", BLOCK_DESCRIPTION),
        ("return.description", RETURN_DESCRIPTION),
        ("return.required", RETURN_REQUIRED),
        ("verify.findings", VERIFY_FINDINGS),
        ("length_limit_error", LENGTH_LIMIT_ERROR),
        ("interrupted_result", INTERRUPTED_RESULT),
        ("orphan_result", foe_log::seed::ORPHAN_RENDERED),
        ("spill_frame", SPILL_FRAME),
        ("exec_stderr", EXEC_STDERR),
        ("exec_exit", EXEC_EXIT),
        ("exec_timed_out", EXEC_TIMED_OUT),
        ("unknown_tool", UNKNOWN_TOOL),
        ("invalid_args", INVALID_ARGS),
        ("section_separator", SECTION_SEPARATOR),
        ("tool_instructions_heading", TOOL_INSTRUCTIONS_HEADING),
        ("tool_instruction_template", TOOL_INSTRUCTION_TEMPLATE),
    ]
}

/// Substitutes `{key}` placeholders in `template`.
pub fn fill(template: &str, values: &[(&str, &str)]) -> String {
    values.iter().fold(template.to_string(), |text, (key, value)| text.replace(&format!("{{{key}}}"), value))
}
