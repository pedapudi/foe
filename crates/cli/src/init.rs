//! `foe init --repository PATH`: write a repository's starting execution
//! contract to `PATH/.foe/contract.json` and a placeholder verifier to
//! `PATH/.foe/verify`. A later run started in that directory reads the
//! contract when its command line names no document, which docs/design.md
//! "The command line" states. The document names the verifier by absolute
//! path like any configured tool.

use crate::plan::{configuration_warnings, ConfigurationWarning};
use crate::run;
use foe_contract::document::resolve;
use foe_contract::ContractDocument;
use std::fmt::Write as _;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

/// Root budget backstops written into the generated document: safety floors
/// against runaway spend, never capability targets. Token allowances stay
/// unlimited and the loop threshold keeps its default, so the backstops are
/// the dimensions a person reasons about before a first run: calls, wall
/// clock, and episode count.
const INIT_MODEL_CALLS: u64 = 600;
const INIT_SECONDS: u64 = 14_400;

/// Writes both files and returns the report the command prints.
pub fn init(repository: &Path) -> Result<String, String> {
    let root = repository.canonicalize().map_err(|e| format!("--repository {}: {e}", repository.display()))?;
    if !root.is_dir() {
        return Err(format!("--repository {}: is not a directory", root.display()));
    }
    let dir = root.join(".foe");
    let contract_path = dir.join("contract.json");
    let verify_path = dir.join("verify");
    for path in [&contract_path, &verify_path] {
        if path.symlink_metadata().is_ok() {
            return Err(format!(
                "{} already exists; foe init writes a starting configuration and never replaces one",
                path.display()
            ));
        }
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    write_atomic(&verify_path, verifier_script(&verify_path).as_bytes(), 0o755)?;
    let built = document(&root, &contract_path, &verify_path);
    let (document, warnings) = match built {
        Ok(built) => built,
        Err(e) => {
            // The verifier alone would make the next run refuse; a failed
            // init leaves nothing behind.
            let _ = std::fs::remove_file(&verify_path);
            return Err(e);
        }
    };
    let text = serde_json::to_string_pretty(&document).map_err(|e| e.to_string())?;
    write_atomic(&contract_path, format!("{text}\n").as_bytes(), 0o644)?;
    Ok(report(&contract_path, &verify_path, &document, &warnings))
}

/// The generated document: the built-in coding workflow over the repository
/// root, gated by the placeholder verifier, under the default model `foe
/// login` recorded when one exists. Resolving it here proves the document a
/// later `foe plan` or run will accept.
fn document(
    root: &Path,
    contract_path: &Path,
    verify_path: &Path,
) -> Result<(ContractDocument, Vec<ConfigurationWarning>), String> {
    let task = format!(
        "Placeholder task written by `foe init`. Run `foe \"the task\" --config {}`: the command-line task replaces \
         this text. An episode that reads this text was started without a task; do nothing and report blocked.",
        contract_path.display()
    );
    let mut document = run::coding_contract_document(root, task, run::default_model()?, Some(verify_path), None)?;
    document.budget.model_calls = INIT_MODEL_CALLS;
    document.budget.seconds = Some(INIT_SECONDS);
    document.grants.execute.push(root.to_path_buf());
    for node in document.workflow.as_mut().into_iter().flat_map(|wf| wf.nodes.values_mut()) {
        if let Some(contract) = &mut node.model {
            contract.grants.execute.push(root.to_path_buf());
        }
    }
    let contract = resolve(&document).map_err(|e| format!("the generated document does not resolve: {e}"))?;
    let warnings = configuration_warnings(&contract);
    Ok((document, warnings))
}

/// The placeholder completion gate. docs/config.md `done_when`: a verifier
/// reports findings by exiting zero and printing one finding per line; a
/// nonzero exit would end the episode as failed instead of showing the
/// model the finding, so the placeholder rejects by finding, not by status.
fn verifier_script(verify_path: &Path) -> String {
    format!(
        "#!/bin/sh
# Placeholder verifier written by `foe init`. It rejects every completion
# candidate. Replace this file with the repository's real completion check;
# docs/config.md `done_when` states the contract it must follow.
while IFS= read -r _candidate; do :; done
echo \"completion cannot be verified: {} is the placeholder verifier foe init wrote, and it rejects every \
candidate. A person must replace it with a real completion check before completion can be judged. Do not keep \
retrying: report blocked and cite this finding.\"
exit 0
",
        verify_path.display()
    )
}

/// Writes the whole file by renaming a completed temporary in the same
/// directory, so no reader sees a partial file and a failure leaves no
/// half-written target.
fn write_atomic(path: &Path, bytes: &[u8], mode: u32) -> Result<(), String> {
    let name = path.file_name().and_then(|n| n.to_str()).expect("init names its own files");
    let tmp = path.with_file_name(format!(".{name}.tmp"));
    let in_tmp = |e: std::io::Error| format!("{}: {e}", tmp.display());
    std::fs::write(&tmp, bytes).map_err(in_tmp)?;
    std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(mode)).map_err(in_tmp)?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

/// What was decided for the person to read: every grant as the document
/// declares it, stated without narrowing it after the fact. `foe plan`
/// reports how the grants bind once resolved.
fn report(
    contract_path: &Path,
    verify_path: &Path,
    document: &ContractDocument,
    warnings: &[ConfigurationWarning],
) -> String {
    let paths = |paths: &[PathBuf]| paths.iter().map(|p| p.display().to_string()).collect::<Vec<_>>().join(", ");
    let model = match &document.model {
        Some(model) => format!("{}/{}, the default `foe login` recorded", model.provider, model.model),
        None => "none: run `foe login`, add a `model` block to the document, or run under --host".to_string(),
    };
    let mut out = String::new();
    writeln!(out, "wrote {}  the starting execution contract", contract_path.display()).ok();
    writeln!(out, "wrote {}  a placeholder verifier that rejects every candidate", verify_path.display()).ok();
    writeln!(out).ok();
    writeln!(out, "read      {}", paths(&document.grants.read)).ok();
    writeln!(out, "write     {}", paths(&document.grants.write)).ok();
    writeln!(out, "          .git lies inside this write grant: grants are additive allow lists with").ok();
    writeln!(out, "          no exclusion syntax, so episodes can rewrite version-control state.").ok();
    writeln!(out, "execute   {}", paths(&document.grants.execute)).ok();
    writeln!(out, "          each entry is a directory: the grant covers executing and reading every").ok();
    writeln!(out, "          file below it, a usable starting breadth to narrow later.").ok();
    writeln!(out, "model     {model}").ok();
    writeln!(
        out,
        "budget    {} model calls, {}s, {} episodes: safety backstops, not targets",
        document.budget.model_calls,
        document.budget.seconds.expect("init writes a wall-clock backstop"),
        document.budget.max_episodes
    )
    .ok();
    writeln!(out, "verify    {} gates completion and rejects every candidate.", verify_path.display()).ok();
    writeln!(out, "          It lies inside the write grant. A run captures its bytes while").ok();
    writeln!(out, "          constructing the contract: the active episode judges candidates by the").ok();
    writeln!(out, "          captured bytes, and a future run reads the file as it then exists.").ok();
    match warnings.is_empty() {
        true => writeln!(out, "warnings  none, from the readiness analysis `foe plan` prints").ok(),
        false => writeln!(out, "{}", crate::plan::warnings_report(warnings).trim_end()).ok(),
    };
    writeln!(out, "\nnext").ok();
    writeln!(out, "  replace {} with the repository's real completion check", verify_path.display()).ok();
    writeln!(out, "  foe plan --config {}", contract_path.display()).ok();
    writeln!(out, "  foe \"the task\" --config {}", contract_path.display()).ok();
    writeln!(out, "      the command-line task replaces the document's placeholder task").ok();
    out
}

#[cfg(test)]
#[path = "init_test.rs"]
mod tests;
