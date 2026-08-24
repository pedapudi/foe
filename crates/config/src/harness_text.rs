//! Every model-visible string the runtime itself contributes, as versioned
//! constants. Identity hashes all of them, so a change to any text here
//! changes the identity of every program, which is the intended effect:
//! the model would see different text.
//!
//! Templates carry `{name}` placeholders that [`fill`] substitutes. The
//! template text, with the placeholders, is what identity hashes.

/// Bumped whenever a constant in this module changes meaning rather than
/// wording. Hashed with the texts.
pub const VERSION: u32 = 2;

pub const BLOCK_NAME: &str = "block";
pub const BLOCK_DESCRIPTION: &str = "Report that the task cannot proceed, and end the episode. Use it when the \
task cannot be completed as stated (code goal-unreachable), when the task admits incompatible readings (code \
ambiguous-task), or when the task needs a tool or permission this program lacks (code missing-capability). Give \
the code and one paragraph stating what is missing or unclear.";

pub const RETURN_NAME: &str = "return";
pub const RETURN_DESCRIPTION: &str = "Finish the task by returning its result. The value must conform to the \
declared schema. A conforming value ends the episode; a value that does not conform is rejected with the \
reason, and the episode continues.";

/// Ends a numbered window of a file that the turn budget shortened. `read`
/// produced it, so `read` is what shows the rest.
pub const CUT_WINDOW: &str = "[Cut to fit this turn's result budget: {omitted} more lines, {characters} \
characters in all. Read the same path again with offset={next} to continue from here.]";

/// Ends any other rendering that the turn budget shortened. The tool that
/// produced it is what shows the rest.
pub const CUT_OUTPUT: &str = "[Cut to fit this turn's result budget: {omitted} of {total} lines omitted here, \
{characters} characters in all. Issue the call again, narrowed, for the part you need.]";

/// Shown as a system inbox item when the model finishes a turn without
/// calling `return` although the program requires a returned value.
pub const RETURN_REQUIRED: &str = "This task is finished by calling the `return` tool with a value that \
conforms to its schema. Call `return` when the result is ready.";

/// Shown as a system inbox item on the last model request available to an
/// episode. The configured completion rule remains authoritative.
pub const FINAL_REQUEST: &str = "One model request remains in this episode. Use it for the highest-priority \
unfinished work rather than repeated exploration. Synthesize the available evidence. When it establishes \
completion, use the program's declared completion signal in this response.";

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
pub const SPILL_FRAME: &str = "The canonical value was {bytes} bytes, which exceeds the inline limit, and is \
stored in the file {path}. The rendering of the result follows.\n\n{head}";

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

/// Frames one section of a workflow node's task: a predecessor's name and
/// its rendered output, or `findings` and `recovery` with their text. See
/// docs/workflow.md "Model nodes".
pub const WORKFLOW_SECTION: &str = "## {name}\n\n{body}";

pub const RECOVER_NAME: &str = "recover";
pub const RECOVER_DESCRIPTION: &str = "Choose the one action the workflow performs after the failure described in \
the message. Call it exactly once.";

/// The system prompt of a workflow recovery decision. An author cannot
/// change it; identity hashes it. See docs/workflow.md "Recovery".
pub const WORKFLOW_RECOVERY_INSTRUCTION: &str = "A node of a declared workflow failed, and you decide how the \
workflow proceeds. The message shows the failed node's inputs, its output or error, any verifier findings, and \
the nodes you may act on. Respond with one call to `recover`. `retry` re-fires the named node and everything \
downstream of it; `amend` does the same and appends your note to that node's inputs as a section labeled \
`recovery`; `skip` lets the failed node contribute its declared empty value; `abort` ends the episode as blocked \
with your code and message. Only the nodes listed are offered. Abort when no action can let the workflow complete.";

/// The section of a recovery message that states the failure and what may
/// be done about it.
pub const WORKFLOW_RECOVERY_FAILURE: &str = "Node `{node}` failed on firing {fire}: {cause}.\n\n{detail}\n\nretry \
and amend may name: {targets}. skip is {skip}.";

/// Bumped whenever the summarization prompt, the transcript rendering, or
/// the continuation state's fields or rendering change in meaning. Hashed
/// into identity beside the texts. See docs/compaction.md.
pub const COMPACTION_POLICY_VERSION: u32 = 1;

/// The system prompt of a summarization request. An author cannot change
/// it; identity hashes it.
pub const COMPACTION_INSTRUCTION: &str = "A coding agent's conversation is being condensed so that the agent can \
continue in a smaller context. The message holds the transcript to condense as labeled plain text, preceded by the \
summary written at the previous condensation when there was one. Write the summary the agent will continue from, \
folding the earlier summary into it. Use exactly these headings, in this order: Goal, Progress, Decisions, Open \
items, Next step. State only what the transcript supports: what was asked, what was done and how it was verified, \
what was decided and why, what remains, and the single next action. Name files, commands, symbols, and error text \
exactly as they appear. Do not continue the conversation, call tools, or address anyone; output the summary alone.";

/// The two sections of a summarization request's message, and the
/// rendering of one transcript entry and of one tool call within it.
pub const COMPACTION_PRIOR: &str = "# Earlier summary\n\n{summary}";
pub const COMPACTION_TRANSCRIPT: &str = "# Transcript\n\n{transcript}";
pub const COMPACTION_TURN: &str = "[{label}]\n{body}";
pub const COMPACTION_CALL: &str = "[call {name} {args}]";

/// The texts above that only a workflow episode shows the model. The
/// workflow section of the identity document hashes them, so rewording one
/// changes the identity of every workflow and of nothing else.
pub fn workflow_texts() -> Vec<(&'static str, &'static str)> {
    vec![
        ("section", WORKFLOW_SECTION),
        ("recover.description", RECOVER_DESCRIPTION),
        ("recovery.instruction", WORKFLOW_RECOVERY_INSTRUCTION),
        ("recovery.failure", WORKFLOW_RECOVERY_FAILURE),
    ]
}

/// Every constant above except the workflow texts, by name, in a fixed
/// order. Identity hashes this list together with the result text seeding
/// writes, which lives in the log crate.
pub fn all() -> Vec<(&'static str, &'static str)> {
    vec![
        ("block.description", BLOCK_DESCRIPTION),
        ("return.description", RETURN_DESCRIPTION),
        ("cut.window", CUT_WINDOW),
        ("cut.output", CUT_OUTPUT),
        ("return.required", RETURN_REQUIRED),
        ("final_request", FINAL_REQUEST),
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
        ("compaction.instruction", COMPACTION_INSTRUCTION),
        ("compaction.prior", COMPACTION_PRIOR),
        ("compaction.transcript", COMPACTION_TRANSCRIPT),
        ("compaction.turn", COMPACTION_TURN),
        ("compaction.call", COMPACTION_CALL),
        ("continuation.message", foe_log::fold::CONTINUATION_MESSAGE),
        ("continuation.item", foe_log::fold::STATE_ITEM),
        ("continuation.none", foe_log::fold::STATE_NONE),
    ]
}

/// Substitutes `{key}` placeholders in `template`.
pub fn fill(template: &str, values: &[(&str, &str)]) -> String {
    values.iter().fold(template.to_string(), |text, (key, value)| text.replace(&format!("{{{key}}}"), value))
}
