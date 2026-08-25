use sha2::{Digest, Sha256};
use std::env;
use std::ffi::OsStr;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const MAX_CHECKER_BYTES: usize = 64 * 1024;
const MAX_OUTPUT_BYTES: usize = 1024 * 1024;
const CHECK_TIMEOUT: Duration = Duration::from_secs(300);

struct TemporaryDirectory(PathBuf);

impl Drop for TemporaryDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn state_paths() -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let runner = env::current_exe().map_err(|error| format!("cannot locate checker runner: {error}"))?;
    let name = runner
        .file_name()
        .and_then(OsStr::to_str)
        .ok_or_else(|| "checker runner path has no UTF-8 file name".to_string())?;
    Ok((
        runner.with_file_name(format!("{name}.generated.sh")),
        runner.with_file_name(format!("{name}.generated.sha256")),
        runner.with_file_name(format!("{name}.initial.sha256")),
    ))
}

fn update_field(hasher: &mut Sha256, bytes: &[u8]) {
    hasher.update((bytes.len() as u64).to_be_bytes());
    hasher.update(bytes);
}

fn collect_paths(directory: &Path, paths: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries = fs::read_dir(directory)
        .map_err(|error| format!("cannot read workspace directory {}: {error}", directory.display()))?;
    for entry in entries {
        let path = entry.map_err(|error| format!("cannot read workspace entry: {error}"))?.path();
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("cannot inspect workspace path {}: {error}", path.display()))?;
        paths.push(path.clone());
        if metadata.file_type().is_dir() {
            collect_paths(&path, paths)?;
        }
    }
    Ok(())
}

fn workspace_digest(root: &Path) -> Result<String, String> {
    let mut paths = Vec::new();
    collect_paths(root, &mut paths)?;
    paths.sort_by(|left, right| left.as_os_str().as_bytes().cmp(right.as_os_str().as_bytes()));
    let mut hasher = Sha256::new();
    for path in paths {
        let relative =
            path.strip_prefix(root).map_err(|error| format!("workspace path is outside its root: {error}"))?;
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("cannot inspect workspace path {}: {error}", path.display()))?;
        update_field(&mut hasher, relative.as_os_str().as_bytes());
        hasher.update((metadata.mode() & 0o7777).to_be_bytes());
        if metadata.file_type().is_symlink() {
            hasher.update(b"symlink\0");
            let target = fs::read_link(&path)
                .map_err(|error| format!("cannot read workspace symlink {}: {error}", path.display()))?;
            update_field(&mut hasher, target.as_os_str().as_bytes());
        } else if metadata.is_file() {
            hasher.update(b"file\0");
            let mut source =
                File::open(&path).map_err(|error| format!("cannot read workspace file {}: {error}", path.display()))?;
            let mut block = [0_u8; 1024 * 1024];
            loop {
                let read = source
                    .read(&mut block)
                    .map_err(|error| format!("cannot read workspace file {}: {error}", path.display()))?;
                if read == 0 {
                    break;
                }
                hasher.update(&block[..read]);
            }
        } else if metadata.is_dir() {
            hasher.update(b"directory\0");
        } else {
            hasher.update(b"special\0");
        }
    }
    Ok(hex::encode(hasher.finalize()))
}

fn copy_entry(source: &Path, target: &Path, workspace_root: &Path, ancestors: &mut Vec<PathBuf>) -> Result<(), String> {
    let metadata = fs::symlink_metadata(source)
        .map_err(|error| format!("cannot inspect {} while copying: {error}", source.display()))?;
    if metadata.file_type().is_symlink() {
        let resolved = fs::canonicalize(source)
            .map_err(|error| format!("cannot resolve workspace symlink {}: {error}", source.display()))?;
        if resolved.is_dir() && !resolved.starts_with(workspace_root) {
            return Err(format!("workspace symlink {} reaches an external directory", source.display()));
        }
        return copy_entry(&resolved, target, workspace_root, ancestors);
    }
    if metadata.is_file() {
        fs::copy(source, target)
            .map_err(|error| format!("cannot copy workspace file {}: {error}", source.display()))?;
        fs::set_permissions(target, metadata.permissions())
            .map_err(|error| format!("cannot preserve permissions for {}: {error}", target.display()))?;
        return Ok(());
    }
    if !metadata.is_dir() {
        return Err(format!("workspace contains unsupported special path {}", source.display()));
    }
    let canonical = fs::canonicalize(source)
        .map_err(|error| format!("cannot resolve workspace directory {}: {error}", source.display()))?;
    if ancestors.contains(&canonical) {
        return Err(format!("workspace symlink cycle reaches {}", source.display()));
    }
    ancestors.push(canonical);
    fs::create_dir(target).map_err(|error| format!("cannot create workspace copy {}: {error}", target.display()))?;
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("cannot read workspace directory {}: {error}", source.display()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("cannot read workspace entry: {error}"))?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        copy_entry(&entry.path(), &target.join(entry.file_name()), workspace_root, ancestors)?;
    }
    ancestors.pop();
    fs::set_permissions(target, metadata.permissions())
        .map_err(|error| format!("cannot preserve permissions for {}: {error}", target.display()))?;
    Ok(())
}

fn temporary_directory() -> Result<TemporaryDirectory, String> {
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    for suffix in 0..100 {
        let path = PathBuf::from(format!("/tmp/foe-task-check-{}-{stamp}-{suffix}", std::process::id()));
        match fs::create_dir(&path) {
            Ok(()) => return Ok(TemporaryDirectory(path)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("cannot create checker workspace: {error}")),
        }
    }
    Err("cannot allocate a unique checker workspace".to_string())
}

fn read_output<R: Read>(mut input: R) -> io::Result<(Vec<u8>, bool)> {
    let mut stored = Vec::new();
    let mut overflow = false;
    let mut block = [0_u8; 8192];
    loop {
        let read = input.read(&mut block)?;
        if read == 0 {
            return Ok((stored, overflow));
        }
        let remaining = MAX_OUTPUT_BYTES.saturating_sub(stored.len());
        stored.extend_from_slice(&block[..read.min(remaining)]);
        overflow |= read > remaining;
    }
}

fn run_checker(checker: &Path, task_root: &Path) -> Result<Vec<String>, String> {
    let temporary = temporary_directory()?;
    let snapshot = temporary.0.join("workspace");
    let workspace_root =
        fs::canonicalize(task_root).map_err(|error| format!("cannot resolve task workspace: {error}"))?;
    copy_entry(task_root, &snapshot, &workspace_root, &mut Vec::new())?;
    let mut child = Command::new("/bin/bash")
        .arg(checker)
        .arg(&snapshot)
        .current_dir(&snapshot)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|error| format!("cannot start task-derived checker: {error}"))?;
    let stdout = child.stdout.take().ok_or_else(|| "checker standard output is unavailable".to_string())?;
    let stderr = child.stderr.take().ok_or_else(|| "checker standard error is unavailable".to_string())?;
    let stdout_reader = thread::spawn(move || read_output(stdout));
    let stderr_reader = thread::spawn(move || read_output(stderr));
    let deadline = Instant::now() + CHECK_TIMEOUT;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|error| format!("cannot wait for checker: {error}"))? {
            break status;
        }
        if Instant::now() >= deadline {
            let pid = child.id().to_string();
            let _ = Command::new("/bin/bash")
                .args(["-c", "kill -KILL -- -\"$1\" 2>/dev/null || true", "foe-kill", &pid])
                .status();
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("task-derived checker exceeded {} seconds", CHECK_TIMEOUT.as_secs()));
        }
        thread::sleep(Duration::from_millis(25));
    };
    let (stdout, stdout_overflow) = stdout_reader
        .join()
        .map_err(|_| "checker standard-output reader panicked".to_string())?
        .map_err(|error| format!("cannot read checker standard output: {error}"))?;
    let (stderr, stderr_overflow) = stderr_reader
        .join()
        .map_err(|_| "checker standard-error reader panicked".to_string())?
        .map_err(|error| format!("cannot read checker standard error: {error}"))?;
    if stdout_overflow || stderr_overflow {
        return Err(format!("task-derived checker output exceeded {MAX_OUTPUT_BYTES} bytes"));
    }
    let stdout = String::from_utf8_lossy(&stdout);
    let stderr = String::from_utf8_lossy(&stderr);
    if !status.success() {
        let detail = stderr.trim().split('\n').next().unwrap_or("");
        let detail = if detail.is_empty() { stdout.trim().split('\n').next().unwrap_or("") } else { detail };
        return Err(format!(
            "task-derived checker exited with {}: {}",
            status.code().map_or_else(|| "a signal".to_string(), |code| format!("status {code}")),
            if detail.is_empty() { "no diagnostic output" } else { detail }
        ));
    }
    if !stderr.trim().is_empty() {
        return Err(format!("task-derived checker wrote to standard error: {}", stderr.trim()));
    }
    Ok(stdout.lines().map(str::trim).filter(|line| !line.is_empty()).map(str::to_string).collect())
}

fn write_atomic(path: &Path, contents: &[u8], mode: u32) -> Result<(), String> {
    let name = path.file_name().and_then(OsStr::to_str).ok_or_else(|| "state path has no UTF-8 name".to_string())?;
    let temporary = path.with_file_name(format!(".{name}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("cannot create temporary state {}: {error}", temporary.display()))?;
    let result = (|| {
        file.write_all(contents).map_err(|error| format!("cannot write temporary state: {error}"))?;
        file.sync_all().map_err(|error| format!("cannot sync temporary state: {error}"))?;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(mode))
            .map_err(|error| format!("cannot set state permissions: {error}"))?;
        fs::rename(&temporary, path).map_err(|error| format!("cannot install state {}: {error}", path.display()))
    })();
    let _ = fs::remove_file(&temporary);
    result
}

fn capture_initial_state(task_root: &Path) -> Result<(), String> {
    let (_, _, initial) = state_paths()?;
    write_atomic(&initial, format!("{}\n", workspace_digest(task_root)?).as_bytes(), 0o444)
}

fn install(source: &str, task_root: &Path) -> Result<(), String> {
    let (checker, receipt, initial) = state_paths()?;
    let expected_initial = fs::read_to_string(&initial)
        .map_err(|error| format!("initial task workspace digest is unavailable: {error}"))?;
    let observed_initial = workspace_digest(task_root)?;
    if expected_initial.trim() != observed_initial {
        return Err(format!(
            "task workspace changed before checker installation: expected sha256:{}; observed sha256:{observed_initial}",
            expected_initial.trim()
        ));
    }
    if source.is_empty() || source.len() > MAX_CHECKER_BYTES {
        return Err(format!("checker source must contain between 1 and {MAX_CHECKER_BYTES} UTF-8 bytes"));
    }
    let syntax = Command::new("/bin/bash")
        .arg("-n")
        .arg("-c")
        .arg(source)
        .output()
        .map_err(|error| format!("cannot validate checker syntax: {error}"))?;
    if !syntax.status.success() {
        let detail = String::from_utf8_lossy(&syntax.stderr);
        return Err(format!("checker source has invalid Bash syntax: {}", detail.trim()));
    }
    write_atomic(&checker, source.as_bytes(), 0o400)?;
    let source_digest = hex::encode(Sha256::digest(source.as_bytes()));
    write_atomic(&receipt, format!("{source_digest}\n").as_bytes(), 0o400)?;
    let findings = run_checker(&checker, task_root).map_err(|error| format!("negative control failed: {error}"))?;
    if findings.is_empty() {
        return Err("negative control failed: checker accepted the untouched task workspace".to_string());
    }
    println!("Installed task-derived checker sha256:{source_digest}.");
    println!("Negative control produced {} finding(s).", findings.len());
    for finding in findings.iter().take(10) {
        println!("- {finding}");
    }
    Ok(())
}

fn verify(task_root: &Path) -> Vec<String> {
    let Ok((checker, receipt, _)) = state_paths() else {
        return vec!["task-derived checker state paths are unavailable".to_string()];
    };
    let Ok(contents) = fs::read(&checker) else {
        return vec!["task-derived checker is not installed".to_string()];
    };
    let Ok(expected) = fs::read_to_string(&receipt) else {
        return vec!["task-derived checker digest is unavailable".to_string()];
    };
    let observed = hex::encode(Sha256::digest(&contents));
    if expected.trim() != observed {
        return vec![format!(
            "task-derived checker changed after installation: expected sha256:{}; observed sha256:{observed}",
            expected.trim()
        )];
    }
    run_checker(&checker, task_root).unwrap_or_else(|error| vec![error])
}

fn main() {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    let task_root = env::current_dir().unwrap_or_else(|error| {
        eprintln!("cannot identify task workspace: {error}");
        std::process::exit(2);
    });
    let result = match arguments.as_slice() {
        [flag] if flag == "--capture-initial-state" => capture_initial_state(&task_root),
        [source] => install(source, &task_root),
        [] => {
            for finding in verify(&task_root) {
                println!("{finding}");
            }
            Ok(())
        }
        _ => Err("expected either no arguments or one checker source argument".to_string()),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(2);
    }
}
