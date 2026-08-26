//! Captures source-change evidence and completes its lineage after a rebuilt
//! Foe binary reports the program it actually runs. An immutable controller
//! checkout builds this evaluator outside the writable candidate tree.

use foe_lineage::{
    build_manifest, check_ancestry, check_proposal, digest_of, require_manifest_path, state_identity, AdoptionRecord,
    ManifestFile, ProgramLineage, RetainedVerifier, StateDocument, MANIFEST_FILE,
};
use foe_log::fold;
use foe_program::{identity::canonical, LineageParent};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

const SOURCE_MANIFEST: &str = "source-candidate-manifest.json";
const ADOPTION_RECORD: &str = "adoption-record.json";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GitObject {
    object_type: String,
    mode: String,
    identity: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "kebab-case", deny_unknown_fields)]
enum SourceEntry {
    Present {
        path: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        base: Option<GitObject>,
        applied: GitObject,
        sha256: String,
        content: String,
    },
    Deleted {
        path: String,
        base: GitObject,
    },
}

impl SourceEntry {
    fn path(&self) -> &str {
        match self {
            Self::Present { path, .. } | Self::Deleted { path, .. } => path,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceManifest {
    schema_version: u32,
    candidate_identity: String,
    base_source_tree: String,
    entries: Vec<SourceEntry>,
    parent_identity_document: String,
    parent_program_identity: String,
    proposal_log: String,
    verification_log: String,
    verification_seq: u64,
    verification_tool: String,
    verification_executable: String,
    verification_executable_sha256: String,
    files: Vec<ManifestFile>,
}

fn fail(key: &str, rule: impl AsRef<str>) -> String {
    format!("{key}: {}", rule.as_ref())
}

fn canonical_bytes<T: Serialize>(value: &T) -> Result<Vec<u8>, String> {
    let value = serde_json::to_value(value).map_err(|e| e.to_string())?;
    Ok(canonical(&value).into_bytes())
}

fn checker_digest() -> Result<String, String> {
    let executable = std::env::current_exe().map_err(|e| format!("checker executable: {e}"))?;
    std::fs::read(&executable)
        .map(|bytes| digest_of(&bytes))
        .map_err(|e| format!("checker executable {}: {e}", executable.display()))
}

fn parse_tree(value: &str) -> Result<(&str, &str), String> {
    let Some((kind, object)) = value.strip_prefix("git-tree-").and_then(|v| v.split_once(':')) else {
        return Err(fail("source tree", "is git-tree-sha1:<40 hex> or git-tree-sha256:<64 hex>"));
    };
    let length = match kind {
        "sha1" => 40,
        "sha256" => 64,
        _ => return Err(fail("source tree", "uses sha1 or sha256")),
    };
    if object.len() != length || !object.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
        return Err(fail("source tree", "has a lowercase hexadecimal object identity of the required length"));
    }
    Ok((kind, object))
}

fn git(root: &Path, arguments: &[&str], input: Option<&[u8]>) -> Result<Vec<u8>, String> {
    let mut command = Command::new("/usr/bin/git");
    command.arg("-C").arg(root).args(arguments).stdout(Stdio::piped()).stderr(Stdio::piped());
    if input.is_some() {
        command.stdin(Stdio::piped());
    }
    let mut child = command.spawn().map_err(|e| format!("/usr/bin/git: {e}"))?;
    if let Some(bytes) = input {
        child.stdin.take().expect("stdin was requested").write_all(bytes).map_err(|e| format!("git stdin: {e}"))?;
    }
    let output = child.wait_with_output().map_err(|e| format!("/usr/bin/git: {e}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(format!("git {}: {}", arguments.join(" "), if detail.is_empty() { "failed" } else { &detail }));
    }
    Ok(output.stdout)
}

fn nul_paths(bytes: &[u8], key: &str) -> Result<Vec<String>, String> {
    bytes
        .split(|byte| *byte == 0)
        .filter(|part| !part.is_empty())
        .map(|part| {
            String::from_utf8(part.to_vec())
                .map_err(|_| fail(key, "contains a non-UTF-8 path, which source candidate manifests do not support"))
        })
        .collect()
}

fn object_at(root: &Path, tree: &str, path: &str) -> Result<Option<GitObject>, String> {
    let (_, object) = parse_tree(tree)?;
    let output = git(root, &["ls-tree", "-z", object, "--", path], None)?;
    if output.is_empty() {
        return Ok(None);
    }
    let records: Vec<_> = output.split(|byte| *byte == 0).filter(|row| !row.is_empty()).collect();
    if records.len() != 1 {
        return Err(fail(path, "resolves to one Git tree entry"));
    }
    let row = std::str::from_utf8(records[0]).map_err(|_| fail(path, "has a UTF-8 Git tree record"))?;
    let (metadata, observed_path) = row.split_once('\t').ok_or_else(|| fail(path, "has a valid Git tree record"))?;
    let fields: Vec<_> = metadata.split(' ').collect();
    if fields.len() != 3 || observed_path != path {
        return Err(fail(path, "has an exact Git tree record"));
    }
    let (mode, object_type, identity) = (fields[0], fields[1], fields[2]);
    if object_type != "blob" || !matches!(mode, "100644" | "100755") {
        return Err(fail(path, format!("is a regular Git blob; found {mode} {object_type}")));
    }
    let (algorithm, _) = parse_tree(tree)?;
    Ok(Some(GitObject {
        object_type: object_type.into(),
        mode: mode.into(),
        identity: format!("git-blob-{algorithm}:{identity}"),
    }))
}

fn regular_bytes(root: &Path, relative: &str, missing: bool) -> Result<Option<Vec<u8>>, String> {
    require_manifest_path("source path", relative).map_err(|e| e.to_string())?;
    let mut current = root.to_path_buf();
    let parts: Vec<_> = relative.split('/').collect();
    for (index, part) in parts.iter().enumerate() {
        current.push(part);
        match std::fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(fail(relative, format!("contains a symbolic link at {}", current.display())));
            }
            Ok(metadata) if index + 1 < parts.len() && !metadata.is_dir() => {
                return Err(fail(relative, format!("has a non-directory parent at {}", current.display())));
            }
            Ok(metadata) if index + 1 == parts.len() && !metadata.is_file() => {
                return Err(fail(relative, "is a regular file or an absent deletion"));
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound && index + 1 == parts.len() && missing => {
                return Ok(None);
            }
            Err(error) => return Err(fail(relative, format!("can be inspected: {error}"))),
            _ => {}
        }
    }
    std::fs::read(&current).map(Some).map_err(|e| fail(relative, format!("is readable: {e}")))
}

fn applied_object(root: &Path, tree: &str, path: &str, bytes: &[u8]) -> Result<GitObject, String> {
    let (algorithm, _) = parse_tree(tree)?;
    let identity = String::from_utf8(git(root, &["hash-object", "--stdin"], Some(bytes))?)
        .map_err(|_| fail(path, "git hash-object returns UTF-8"))?
        .trim()
        .to_string();
    let mode =
        if std::fs::metadata(root.join(path)).map_err(|e| fail(path, e.to_string()))?.permissions().mode() & 0o111 == 0
        {
            "100644"
        } else {
            "100755"
        };
    Ok(GitObject {
        object_type: "blob".into(),
        mode: mode.into(),
        identity: format!("git-blob-{algorithm}:{identity}"),
    })
}

fn changed_worktree_paths(root: &Path, base: &str) -> Result<Vec<String>, String> {
    let (_, object) = parse_tree(base)?;
    let mut paths =
        nul_paths(&git(root, &["diff", "--name-only", "--no-renames", "-z", object, "--"], None)?, "git diff")?;
    paths.extend(nul_paths(&git(root, &["ls-files", "--others", "--exclude-standard", "-z"], None)?, "git ls-files")?);
    paths.sort();
    paths.dedup();
    Ok(paths)
}

fn candidate_identity(base: &str, entries: &[SourceEntry]) -> Result<String, String> {
    let body = json!({"base_source_tree": base, "entries": entries});
    Ok(digest_of(canonical(&body).as_bytes()))
}

fn protected_build_path(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path);
    name.ends_with(".bzl")
        || matches!(
            name,
            ".bazelignore"
                | ".bazelrc"
                | ".bazelversion"
                | "BUILD"
                | "BUILD.bazel"
                | "Cargo.lock"
                | "Cargo.toml"
                | "MODULE.bazel"
                | "MODULE.bazel.lock"
                | "WORKSPACE"
                | "WORKSPACE.bazel"
                | "build.rs"
                | "package-lock.json"
                | "package.json"
                | "pnpm-lock.yaml"
                | "REPO.bazel"
                | "rust-toolchain"
                | "rust-toolchain.toml"
        )
}

fn source_entries(root: &Path, base: &str, bundle: &Path) -> Result<Vec<SourceEntry>, String> {
    let mut entries = Vec::new();
    for path in changed_worktree_paths(root, base)? {
        let base_object = object_at(root, base, &path)?;
        match regular_bytes(root, &path, true)? {
            Some(bytes) => {
                let applied = applied_object(root, base, &path, &bytes)?;
                if base_object.as_ref() == Some(&applied) {
                    continue;
                }
                let content = format!("candidate-files/{path}");
                let destination = bundle.join(&content);
                if destination.exists() {
                    return Err(fail(&content, "does not pre-exist in the source evidence bundle"));
                }
                std::fs::create_dir_all(destination.parent().expect("a candidate file has a parent"))
                    .map_err(|e| fail(&content, e.to_string()))?;
                std::fs::write(&destination, &bytes).map_err(|e| fail(&content, e.to_string()))?;
                entries.push(SourceEntry::Present {
                    path,
                    base: base_object,
                    applied,
                    sha256: digest_of(&bytes),
                    content,
                });
            }
            None => {
                let base = base_object.ok_or_else(|| fail(&path, "cannot delete a path absent from the base tree"))?;
                entries.push(SourceEntry::Deleted { path, base });
            }
        }
    }
    if entries.is_empty() {
        return Err("source candidate contains no Git object change".into());
    }
    Ok(entries)
}

fn walk_files(root: &Path, omit: &str) -> Result<Vec<ManifestFile>, String> {
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory).map_err(|e| fail("source evidence", e.to_string()))? {
            let path = entry.map_err(|e| fail("source evidence", e.to_string()))?.path();
            let metadata = std::fs::symlink_metadata(&path).map_err(|e| fail("source evidence", e.to_string()))?;
            if metadata.file_type().is_symlink() {
                return Err(fail("source evidence", format!("contains a symbolic link: {}", path.display())));
            }
            if metadata.is_dir() {
                pending.push(path);
                continue;
            }
            if !metadata.is_file() {
                return Err(fail("source evidence", format!("contains an unsupported entry: {}", path.display())));
            }
            let relative = path
                .strip_prefix(root)
                .expect("the walk stays below its root")
                .to_str()
                .ok_or_else(|| fail("source evidence", "contains a non-UTF-8 path"))?
                .replace(std::path::MAIN_SEPARATOR, "/");
            require_manifest_path("source evidence file", &relative).map_err(|e| e.to_string())?;
            if relative != omit {
                let bytes = std::fs::read(&path).map_err(|e| fail(&relative, e.to_string()))?;
                files.push(ManifestFile { path: relative, bytes: bytes.len() as u64, sha256: digest_of(&bytes) });
            }
        }
    }
    files.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(files)
}

fn checked_manifest(bundle: &Path) -> Result<(SourceManifest, String), String> {
    let bundle_metadata = std::fs::symlink_metadata(bundle).map_err(|e| fail("source evidence", e.to_string()))?;
    if bundle_metadata.file_type().is_symlink() || !bundle_metadata.is_dir() {
        return Err(fail("source evidence", "is a real directory rather than a symbolic link"));
    }
    let path = bundle.join(SOURCE_MANIFEST);
    let bytes = std::fs::read(&path).map_err(|e| fail(SOURCE_MANIFEST, format!("is readable: {e}")))?;
    let manifest: SourceManifest = serde_json::from_slice(&bytes).map_err(|e| fail(SOURCE_MANIFEST, e.to_string()))?;
    if canonical_bytes(&manifest)? != bytes {
        return Err(fail(SOURCE_MANIFEST, "is the canonical serialization of its exact schema"));
    }
    if manifest.schema_version != 1 {
        return Err(fail("source manifest schema_version", "is 1"));
    }
    foe_lineage::require_digest("source manifest candidate_identity", &manifest.candidate_identity)
        .map_err(|e| e.to_string())?;
    foe_lineage::require_digest("source manifest parent_program_identity", &manifest.parent_program_identity)
        .map_err(|e| e.to_string())?;
    parse_tree(&manifest.base_source_tree)?;
    require_manifest_path("source manifest parent_identity_document", &manifest.parent_identity_document)
        .map_err(|e| e.to_string())?;
    require_manifest_path("source manifest proposal_log", &manifest.proposal_log).map_err(|e| e.to_string())?;
    require_manifest_path("source manifest verification_log", &manifest.verification_log).map_err(|e| e.to_string())?;
    require_manifest_path("source manifest verification_executable", &manifest.verification_executable)
        .map_err(|e| e.to_string())?;
    foe_lineage::require_digest(
        "source manifest verification_executable_sha256",
        &manifest.verification_executable_sha256,
    )
    .map_err(|e| e.to_string())?;
    let paths: Vec<_> = manifest.entries.iter().map(SourceEntry::path).collect();
    if paths.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(fail("source manifest entries", "are unique and ordered by path"));
    }
    if candidate_identity(&manifest.base_source_tree, &manifest.entries)? != manifest.candidate_identity {
        return Err(fail("source manifest candidate_identity", "matches the base tree and full Git entries"));
    }
    if manifest.files.windows(2).any(|pair| pair[0].path >= pair[1].path) {
        return Err(fail("source manifest files", "are unique and ordered by path"));
    }
    let observed = walk_files(bundle, SOURCE_MANIFEST)?;
    if observed != manifest.files {
        return Err(fail("source manifest files", "match every retained regular file and no other entry"));
    }
    for required in [
        &manifest.parent_identity_document,
        &manifest.proposal_log,
        &manifest.verification_log,
        &manifest.verification_executable,
    ] {
        if !manifest.files.iter().any(|file| &file.path == required) {
            return Err(fail("source manifest files", format!("retain {required}")));
        }
    }
    let verifier_file = manifest
        .files
        .iter()
        .find(|file| file.path == manifest.verification_executable)
        .expect("a required verifier file is retained");
    if verifier_file.sha256 != manifest.verification_executable_sha256 {
        return Err(fail("source manifest verification_executable_sha256", "matches the retained verifier bytes"));
    }
    for entry in &manifest.entries {
        require_manifest_path("source entry path", entry.path()).map_err(|e| e.to_string())?;
        if protected_build_path(entry.path()) {
            return Err(fail("source entry path", format!("{} preserves protected build metadata", entry.path())));
        }
        if let SourceEntry::Present { content, sha256, applied, .. } = entry {
            require_manifest_path("source entry content", content).map_err(|e| e.to_string())?;
            let file = manifest
                .files
                .iter()
                .find(|file| &file.path == content)
                .ok_or_else(|| fail("source entry content", format!("retains {content}")))?;
            if &file.sha256 != sha256
                || applied.object_type != "blob"
                || !matches!(applied.mode.as_str(), "100644" | "100755")
            {
                return Err(fail("source entry", "binds regular bytes, a regular Git mode, and a blob identity"));
            }
        }
    }
    verify_proposal(bundle, &manifest)?;
    Ok((manifest, digest_of(&bytes)))
}

fn verification_result(bundle: &Path, log: &str, seq: u64) -> Result<(String, String), String> {
    let path = bundle.join(log);
    let events = fold::read_all(path.parent().expect("a log path has a parent"))
        .map_err(|e| fail("verification log", e.to_string()))?;
    let event = events.iter().find(|event| event.seq == seq).ok_or_else(|| fail("verification_seq", "exists"))?;
    let foe_log::EventData::VerificationResult(result) = &event.data else {
        return Err(fail("verification_seq", "names a verification/result event"));
    };
    Ok((result.tool.clone(), result.verifier_identity.clone()))
}

fn verify_proposal(bundle: &Path, manifest: &SourceManifest) -> Result<(), String> {
    let parent_bytes = std::fs::read(bundle.join(&manifest.parent_identity_document))
        .map_err(|e| fail("parent identity document", e.to_string()))?;
    let parent: Value =
        serde_json::from_slice(&parent_bytes).map_err(|e| fail("parent identity document", e.to_string()))?;
    if canonical(&parent).as_bytes() != parent_bytes || digest_of(&parent_bytes) != manifest.parent_program_identity {
        return Err(fail("parent identity document", "is canonical and hashes to parent_program_identity"));
    }
    let open = check_proposal(
        bundle,
        manifest.files.iter().map(|file| file.path.as_str()),
        &manifest.proposal_log,
        &manifest.verification_log,
        manifest.verification_seq,
        &parent,
        Some(RetainedVerifier {
            tool: &manifest.verification_tool,
            executable_sha256: &manifest.verification_executable_sha256,
        }),
    )
    .map_err(|e| e.to_string())?;
    if !open.is_empty() {
        return Err(fail("verification log", format!("has open authorization checks: {}", open.join("; "))));
    }
    Ok(())
}

fn capture(args: &[String]) -> Result<Value, String> {
    let [bundle, candidate, base, parent_document, proposal_log, verification_log, seq, verifier] = args else {
        return Err("usage: source-adoption capture BUNDLE CANDIDATE BASE_TREE PARENT_DOCUMENT PROPOSAL_LOG VERIFICATION_LOG VERIFICATION_SEQ VERIFIER_EXECUTABLE".into());
    };
    let bundle = Path::new(bundle);
    let candidate = Path::new(candidate);
    parse_tree(base)?;
    let verification_seq = seq.parse::<u64>().map_err(|e| fail("VERIFICATION_SEQ", e.to_string()))?;
    if bundle.join(SOURCE_MANIFEST).exists() {
        return Err(fail(SOURCE_MANIFEST, "does not exist before capture"));
    }
    let entries = source_entries(candidate, base, bundle)?;
    let parent_bytes = std::fs::read(bundle.join(parent_document)).map_err(|e| fail(parent_document, e.to_string()))?;
    let parent: Value = serde_json::from_slice(&parent_bytes).map_err(|e| fail(parent_document, e.to_string()))?;
    if canonical(&parent).as_bytes() != parent_bytes {
        return Err(fail(parent_document, "is one canonical JSON value"));
    }
    require_manifest_path("VERIFIER_EXECUTABLE", verifier).map_err(|e| e.to_string())?;
    let verifier_metadata =
        std::fs::symlink_metadata(bundle.join(verifier)).map_err(|e| fail(verifier, e.to_string()))?;
    if verifier_metadata.file_type().is_symlink() || !verifier_metadata.is_file() {
        return Err(fail(
            "VERIFIER_EXECUTABLE",
            "is a regular file; symbolic links and other entry types are unsupported",
        ));
    }
    let verifier_bytes = std::fs::read(bundle.join(verifier)).map_err(|e| fail(verifier, e.to_string()))?;
    let verifier_sha256 = digest_of(&verifier_bytes);
    let (verification_tool, recorded_verifier) = verification_result(bundle, verification_log, verification_seq)?;
    if recorded_verifier != verifier_sha256 {
        return Err(fail("VERIFIER_EXECUTABLE", "hashes to the accepted verification/result.verifier_identity"));
    }
    let mut manifest = SourceManifest {
        schema_version: 1,
        candidate_identity: candidate_identity(base, &entries)?,
        base_source_tree: base.clone(),
        entries,
        parent_identity_document: parent_document.clone(),
        parent_program_identity: digest_of(&parent_bytes),
        proposal_log: proposal_log.clone(),
        verification_log: verification_log.clone(),
        verification_seq,
        verification_tool,
        verification_executable: verifier.clone(),
        verification_executable_sha256: verifier_sha256,
        files: walk_files(bundle, SOURCE_MANIFEST)?,
    };
    let bytes = canonical_bytes(&manifest)?;
    std::fs::write(bundle.join(SOURCE_MANIFEST), &bytes).map_err(|e| fail(SOURCE_MANIFEST, e.to_string()))?;
    let (checked, identity) = checked_manifest(bundle)?;
    manifest.files = checked.files;
    Ok(json!({
        "schema_version": 1,
        "source_bundle_identity": identity,
        "source_candidate_identity": manifest.candidate_identity,
        "base_source_tree": manifest.base_source_tree,
        "parent_program_identity": manifest.parent_program_identity,
    }))
}

fn diff_paths(root: &Path, base: &str, applied: &str) -> Result<BTreeSet<String>, String> {
    let (base_kind, base_object) = parse_tree(base)?;
    let (applied_kind, applied_object) = parse_tree(applied)?;
    if base_kind != applied_kind {
        return Err(fail("evaluated source tree", "uses the source bundle's Git object format"));
    }
    Ok(nul_paths(
        &git(root, &["diff", "--name-only", "--no-renames", "-z", base_object, applied_object], None)?,
        "git diff",
    )?
    .into_iter()
    .collect())
}

fn verify_clean_tree(root: &Path, applied: &str, manifest: &SourceManifest) -> Result<(), String> {
    let expected: BTreeSet<_> = manifest.entries.iter().map(|entry| entry.path().to_string()).collect();
    if diff_paths(root, &manifest.base_source_tree, applied)? != expected {
        return Err(fail("evaluated source tree", "changes exactly the source bundle paths"));
    }
    for entry in &manifest.entries {
        match entry {
            SourceEntry::Present { path, base, applied: expected_object, sha256, .. } => {
                if object_at(root, &manifest.base_source_tree, path)?.as_ref() != base.as_ref() {
                    return Err(fail(path, "matches its recorded base Git object"));
                }
                let bytes = regular_bytes(root, path, false)?.ok_or_else(|| fail(path, "is present"))?;
                let observed = object_at(root, applied, path)?
                    .ok_or_else(|| fail(path, "is present in the evaluated Git tree"))?;
                if &observed != expected_object
                    || digest_of(&bytes) != *sha256
                    || applied_object(root, applied, path, &bytes)? != observed
                {
                    return Err(fail(path, "matches the retained bytes, mode, blob, and evaluated Git tree"));
                }
            }
            SourceEntry::Deleted { path, base }
                if object_at(root, &manifest.base_source_tree, path)?.as_ref() != Some(base) =>
            {
                return Err(fail(path, "matches its recorded base Git object"));
            }
            SourceEntry::Deleted { path, .. } if object_at(root, applied, path)?.is_some() => {
                return Err(fail(path, "is absent from the evaluated Git tree"));
            }
            SourceEntry::Deleted { path, .. } => {
                let _ = regular_bytes(root, path, true)?;
                if root.join(path).exists() {
                    return Err(fail(path, "is absent from the evaluated checkout"));
                }
            }
        }
    }
    Ok(())
}

fn evaluated_pair(runtime: &Path, applied: &str) -> Result<Value, String> {
    let binary = std::fs::read(runtime).map_err(|e| fail("runtime binary", e.to_string()))?;
    Ok(json!({"source_tree": applied, "runtime_binary": digest_of(&binary)}))
}

fn preflight_value(bundle: &Path, source: &Path, applied: &str, runtime: &Path) -> Result<Value, String> {
    let (manifest, bundle_identity) = checked_manifest(bundle)?;
    verify_clean_tree(source, applied, &manifest)?;
    Ok(json!({
        "schema_version": 1,
        "source_bundle_identity": bundle_identity,
        "source_candidate_identity": manifest.candidate_identity,
        "base_source_tree": manifest.base_source_tree,
        "parent_program_identity": manifest.parent_program_identity,
        "checker_sha256": checker_digest()?,
        "evaluated_pair": evaluated_pair(runtime, applied)?,
        "provenance": "source and binary digests computed independently; no build attestation",
    }))
}

fn preflight(args: &[String]) -> Result<Value, String> {
    let [bundle, source, applied, runtime] = args else {
        return Err("usage: source-adoption preflight BUNDLE SOURCE_ROOT APPLIED_TREE RUNTIME_BINARY".into());
    };
    preflight_value(Path::new(bundle), Path::new(source), applied, Path::new(runtime))
}

fn copy_bundle(source: &Path, manifest: &SourceManifest, destination: &Path) -> Result<(), String> {
    std::fs::create_dir_all(destination).map_err(|e| fail("lineage evidence", e.to_string()))?;
    for file in &manifest.files {
        let bytes = std::fs::read(source.join(&file.path)).map_err(|e| fail(&file.path, e.to_string()))?;
        let path = destination.join(&file.path);
        std::fs::create_dir_all(path.parent().expect("a retained file has a parent"))
            .map_err(|e| fail(&file.path, e.to_string()))?;
        std::fs::write(path, bytes).map_err(|e| fail(&file.path, e.to_string()))?;
    }
    std::fs::copy(source.join(SOURCE_MANIFEST), destination.join(SOURCE_MANIFEST))
        .map_err(|e| fail(SOURCE_MANIFEST, e.to_string()))?;
    Ok(())
}

fn adopt(args: &[String]) -> Result<Value, String> {
    let [bundle, source, applied, runtime, plan_path, episode, lineage] = args else {
        return Err("usage: source-adoption adopt BUNDLE SOURCE_ROOT APPLIED_TREE RUNTIME_BINARY PLAN_JSON EPISODE_DIR LINEAGE_DIR".into());
    };
    let bundle = Path::new(bundle);
    let source = Path::new(source);
    let runtime = Path::new(runtime);
    let preflight = preflight_value(bundle, source, applied, runtime)?;
    let (source_manifest, _) = checked_manifest(bundle)?;
    let plan: Value = serde_json::from_slice(&std::fs::read(plan_path).map_err(|e| fail("foe plan", e.to_string()))?)
        .map_err(|e| fail("foe plan", e.to_string()))?;
    let plan_object = plan.as_object().ok_or_else(|| fail("foe plan", "is one JSON object"))?;
    let identity =
        plan_object.get("identity").and_then(Value::as_str).ok_or_else(|| fail("foe plan identity", "is a string"))?;
    let identity_document =
        plan_object.get("identity_document").ok_or_else(|| fail("foe plan", "contains identity_document"))?;
    let planned_program = plan_object.get("program").ok_or_else(|| fail("foe plan", "contains program"))?;
    if digest_of(canonical(identity_document).as_bytes()) != identity {
        return Err(fail("foe plan identity", "is the canonical identity_document digest"));
    }
    let events = fold::read_all(Path::new(episode)).map_err(|e| fail("evaluated episode", e.to_string()))?;
    let state = fold::fold(&events).map_err(|e| fail("evaluated episode", e.to_string()))?;
    let start = state.start.as_ref().ok_or_else(|| fail("evaluated episode", "has episode/start"))?;
    let mut normalized_program = planned_program.clone();
    if let Some(object) = normalized_program.as_object_mut() {
        object.remove("task");
    }
    let runtime_identity =
        preflight["evaluated_pair"]["runtime_binary"].as_str().expect("preflight emits a runtime identity");
    if start.identity != identity || start.program != normalized_program || start.runtime.build != runtime_identity {
        return Err(fail(
            "evaluated episode",
            "starts with the planned program identity, planned resolved program, and evaluated binary digest",
        ));
    }
    let lineage = Path::new(lineage);
    if lineage.exists() {
        return Err(fail("lineage directory", "does not already exist"));
    }
    let build = lineage.join("bundle-build");
    copy_bundle(bundle, &source_manifest, &build)?;
    let child_bytes = canonical(identity_document).into_bytes();
    std::fs::write(build.join("child-identity.json"), &child_bytes)
        .map_err(|e| fail("child identity document", e.to_string()))?;
    let source_manifest_bytes = std::fs::read(bundle.join(SOURCE_MANIFEST)).map_err(|e| e.to_string())?;
    let record = AdoptionRecord {
        schema_version: 1,
        program_identity: identity.into(),
        identity_document_sha256: digest_of(&child_bytes),
        artifact_manifest_sha256: digest_of(&source_manifest_bytes),
        verification_log: source_manifest.verification_log.clone(),
        verification_seq: source_manifest.verification_seq,
    };
    let record_data = canonical_bytes(&record)?;
    std::fs::write(build.join(ADOPTION_RECORD), &record_data).map_err(|e| fail(ADOPTION_RECORD, e.to_string()))?;
    let evidence_manifest =
        build_manifest(&build, &source_manifest.proposal_log, ADOPTION_RECORD).map_err(|e| e.to_string())?;
    let evidence_bytes = canonical_bytes(&evidence_manifest)?;
    std::fs::write(build.join(MANIFEST_FILE), &evidence_bytes).map_err(|e| fail(MANIFEST_FILE, e.to_string()))?;
    let evidence_identity = digest_of(&evidence_bytes);
    let evidence_root = lineage.join("evidence");
    std::fs::create_dir_all(&evidence_root).map_err(|e| fail("evidence directory", e.to_string()))?;
    let evidence_dir = evidence_root.join(evidence_identity.trim_start_matches("sha256:"));
    std::fs::rename(&build, &evidence_dir).map_err(|e| fail("evidence directory", e.to_string()))?;
    let parent_bytes =
        std::fs::read(bundle.join(&source_manifest.parent_identity_document)).map_err(|e| e.to_string())?;
    let parent_document: Value = serde_json::from_slice(&parent_bytes).map_err(|e| e.to_string())?;
    let parent_state_identity = state_identity(&source_manifest.parent_program_identity, None);
    let claim = ProgramLineage {
        parent: LineageParent {
            program_identity: source_manifest.parent_program_identity.clone(),
            state_identity: parent_state_identity.clone(),
        },
        evidence: evidence_identity.clone(),
        verification_log: source_manifest.verification_log.clone(),
        verification_seq: source_manifest.verification_seq,
    };
    let child_state_identity = state_identity(identity, Some(&claim));
    let states = lineage.join("states");
    std::fs::create_dir_all(&states).map_err(|e| fail("states directory", e.to_string()))?;
    let parent_state = StateDocument { identity_document: parent_document, program_lineage: None };
    let child_state = StateDocument { identity_document: identity_document.clone(), program_lineage: Some(claim) };
    std::fs::write(
        states.join(format!("{}.json", parent_state_identity.trim_start_matches("sha256:"))),
        canonical_bytes(&parent_state)?,
    )
    .map_err(|e| fail("parent state", e.to_string()))?;
    let child_path = states.join(format!("{}.json", child_state_identity.trim_start_matches("sha256:")));
    std::fs::write(&child_path, canonical_bytes(&child_state)?).map_err(|e| fail("child state", e.to_string()))?;
    let resolve_state = |state: &str| -> Result<StateDocument, String> {
        let path = states.join(format!("{}.json", state.trim_start_matches("sha256:")));
        serde_json::from_slice(&std::fs::read(&path).map_err(|e| format!("{}: {e}", path.display()))?)
            .map_err(|e| format!("{}: {e}", path.display()))
    };
    let resolve_evidence = |address: &str| -> Result<PathBuf, String> {
        let path = evidence_root.join(address.trim_start_matches("sha256:"));
        if path.is_dir() {
            Ok(path)
        } else {
            Err(format!("{} is not a directory", path.display()))
        }
    };
    let checked = check_ancestry(&child_state, &resolve_state, &resolve_evidence).map_err(|e| e.to_string())?;
    if checked.chain.first().map(|entry| entry.program_identity.as_str()) != Some(identity) {
        return Err(fail("lineage ancestry", "starts with the launched program identity"));
    }
    Ok(json!({
        "schema_version": 1,
        "source_bundle_identity": preflight["source_bundle_identity"],
        "source_candidate_identity": source_manifest.candidate_identity,
        "adoption_identity": digest_of(&record_data),
        "evidence_identity": evidence_identity,
        "program_identity": identity,
        "state_identity": child_state_identity,
        "parent_program_identity": source_manifest.parent_program_identity,
        "parent_state_identity": parent_state_identity,
        "checker_sha256": checker_digest()?,
        "evaluated_pair": preflight["evaluated_pair"],
        "plan_identity": identity,
        "launched_program_verified": true,
        "lineage_directory": lineage,
    }))
}

fn run(arguments: &[String]) -> Result<Value, String> {
    let Some((command, rest)) = arguments.split_first() else {
        return Err("usage: source-adoption {capture|preflight|adopt} ...".into());
    };
    match command.as_str() {
        "capture" => capture(rest),
        "preflight" => preflight(rest),
        "adopt" => adopt(rest),
        _ => Err("usage: source-adoption {capture|preflight|adopt} ...".into()),
    }
}

fn main() -> ExitCode {
    match run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        Ok(value) => {
            println!("{}", canonical(&value));
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("source-adoption: {error}");
            ExitCode::FAILURE
        }
    }
}
