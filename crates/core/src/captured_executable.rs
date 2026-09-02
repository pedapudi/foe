//! Captured executables for configured tools and transports.

use foe_contract::document::{CapturedExecutable as CapturedExecutableSource, ContractTreeSelection, ResolvedContract};
use nix::fcntl::{fcntl, FcntlArg, SealFlag};
use nix::sys::memfd::{memfd_create, MFdFlags};
use nix::sys::statvfs::{statvfs, FsFlags};
use nix::unistd::close;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, OwnedFd};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;

const MANIFEST_FD: i32 = 63;
const FIRST_EXECUTABLE_FD: i32 = 64;
const MANIFEST_KIND: &str = "foe/inherited-executables";
const REQUIRED_SEALS: SealFlag =
    SealFlag::F_SEAL_SEAL.union(SealFlag::F_SEAL_SHRINK).union(SealFlag::F_SEAL_GROW).union(SealFlag::F_SEAL_WRITE);

/// A configured executable captured during execution-contract construction.
/// Foe records its bytes, digest, source path, and invocation name. Every
/// later invocation uses the captured copy, so source replacement or deletion
/// cannot change the run.
#[derive(Debug)]
pub struct CapturedExecutable {
    pub source_path: PathBuf,
    pub sha256: String,
    bytes: Arc<[u8]>,
    invocation_name: std::ffi::OsString,
    stored_path: PathBuf,
    fd: Arc<OwnedFd>,
    _store: Option<Arc<StoreRoot>>,
}

impl CapturedExecutable {
    fn store(captured: &CapturedExecutableSource, store: &mut Store) -> Result<Arc<Self>, String> {
        let (stored_path, fd) = store.write(captured)?;
        Ok(Arc::new(Self {
            source_path: captured.source_path.clone(),
            sha256: captured.sha256.clone(),
            bytes: captured.bytes.clone(),
            invocation_name: captured.invocation_name.clone().into(),
            stored_path,
            fd,
            _store: Some(store.root.clone()),
        }))
    }

    fn inherited(captured: &CapturedExecutableSource, inherited: &InheritedExecutable) -> Result<Arc<Self>, String> {
        if inherited.sha256 != captured.sha256 || inherited.bytes.as_ref() != captured.bytes.as_ref() {
            return Err(format!(
                "{}: inherited executable does not match the constructed bytes",
                captured.source_path.display()
            ));
        }
        Ok(Arc::new(Self {
            source_path: captured.source_path.clone(),
            sha256: captured.sha256.clone(),
            bytes: captured.bytes.clone(),
            invocation_name: inherited.invocation_name.clone(),
            stored_path: inherited.stored_path.clone(),
            fd: inherited.fd.clone(),
            _store: None,
        }))
    }

    pub fn fd(&self) -> &Arc<OwnedFd> {
        &self.fd
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn invocation_name(&self) -> &std::ffi::OsStr {
        &self.invocation_name
    }

    pub fn stored_path(&self) -> &Path {
        &self.stored_path
    }

    fn cleanup_root(&self) -> Option<&Path> {
        self._store.as_ref().map(|root| root.0.as_path())
    }

    /// Commits an executable for callers that construct an exec transport
    /// without a complete contract document.
    pub fn load(path: &Path) -> Result<Arc<Self>, String> {
        use std::os::unix::fs::PermissionsExt;
        if !path.is_absolute() {
            return Err("is not an absolute path".into());
        }
        let invocation_name = path.file_name().and_then(|n| n.to_str()).ok_or("has a UTF-8 file name")?.to_owned();
        let path = std::fs::canonicalize(path).map_err(|e| format!("names an existing path: {e}"))?;
        let mut file = File::open(&path).map_err(|e| format!("is readable for construction: {e}"))?;
        let metadata = file.metadata().map_err(|e| format!("has readable metadata: {e}"))?;
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            return Err("names an executable file".into());
        }
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| format!("is readable for construction: {e}"))?;
        let captured = CapturedExecutableSource {
            source_path: path,
            invocation_name,
            sha256: foe_contract::fingerprint::sha256_hex(&bytes),
            bytes: Arc::from(bytes),
        };
        let mut store = Store::create(Path::new("/tmp/foe-standalone-executable"), &[])?;
        Self::store(&captured, &mut store)
    }
}

/// Committed executables reachable from one episode, arranged like its
/// resolved child-contract tree.
#[derive(Debug, Clone, Default)]
pub struct CapturedExecutableTree {
    path: String,
    pub tools: BTreeMap<String, Arc<CapturedExecutable>>,
    pub transport: Option<Arc<CapturedExecutable>>,
    child_contracts: BTreeMap<String, (bool, CapturedExecutableTree)>,
    workflow: BTreeMap<String, CapturedExecutableTree>,
}

impl CapturedExecutableTree {
    pub fn materialize(contract: &ResolvedContract, preferred_parent: &Path) -> Result<Self, String> {
        if !needs_storage(contract) {
            return Self::build(contract, "contract", None, None);
        }
        let mut store = Store::create(preferred_parent, &contract.grants.write)?;
        let tree = Self::build(contract, "contract", None, Some(&mut store))?;
        Ok(tree)
    }

    pub fn from_inherited(contract: &ResolvedContract, inherited: &InheritedExecutables) -> Result<Self, String> {
        Self::build(contract, "contract", Some(inherited), None)
    }

    fn build(
        contract: &ResolvedContract,
        path: &str,
        inherited: Option<&InheritedExecutables>,
        mut store: Option<&mut Store>,
    ) -> Result<Self, String> {
        let path = path.to_string();
        let mut make = |key: String, captured: &CapturedExecutableSource| match inherited
            .and_then(|all| all.entries.get(&key))
        {
            Some(found) => CapturedExecutable::inherited(captured, found),
            None if inherited.is_some() => Err(format!("{key}: inherited executable is absent")),
            None => CapturedExecutable::store(captured, store.as_deref_mut().expect("a root construction has a store")),
        };
        let mut tools = BTreeMap::new();
        for (name, captured) in &contract.captured_executables {
            tools.insert(name.clone(), make(relative_key(&path, &format!("tool_defs.{name}.exec")), captured)?);
        }
        let transport = contract
            .captured_transport
            .as_ref()
            .map(|captured| make(relative_key(&path, "model.exec"), captured))
            .transpose()?;
        let mut child_contracts = BTreeMap::new();
        for (name, child) in &contract.child_contracts {
            let reachable = contract.grants.spawn.contains(name);
            let child_path = format!("{path}.child_contracts.{name}");
            child_contracts
                .insert(name.clone(), (reachable, Self::build(child, &child_path, inherited, store.as_deref_mut())?));
        }
        let mut workflow = BTreeMap::new();
        for (node_path, child) in &contract.workflow_contracts {
            let nodes = node_path.replace('/', ".workflow.nodes.");
            let child_path = format!("{path}.workflow.nodes.{nodes}.model");
            workflow.insert(node_path.clone(), Self::build(child, &child_path, inherited, store.as_deref_mut())?);
        }
        Ok(Self { path, tools, transport, child_contracts, workflow })
    }

    pub fn child(&self, name: &str) -> Option<&CapturedExecutableTree> {
        self.workflow.get(name).or_else(|| self.child_contracts.get(name).map(|(_, child)| child))
    }

    /// Every executable inode that the episode sandbox must authorize.
    pub fn reachable(&self) -> Vec<Arc<CapturedExecutable>> {
        let mut out: Vec<_> = self.reachable_entries().into_iter().map(|(_, executable)| executable).collect();
        let mut seen = BTreeSet::new();
        out.retain(|executable| seen.insert((executable.sha256.clone(), executable.invocation_name.clone())));
        out
    }

    /// Configuration keys and captured executables for every reachable executable.
    pub fn reachable_entries(&self) -> Vec<(String, Arc<CapturedExecutable>)> {
        let mut out = Vec::new();
        self.walk(ContractTreeSelection::ExecutableReachable, &mut |key, executable| {
            out.push((key, executable.clone()))
        });
        out
    }

    /// Configuration keys and captured executables that contribute to this contract's fingerprint.
    pub fn fingerprint_entries(&self) -> Vec<(String, Arc<CapturedExecutable>)> {
        let mut out = Vec::new();
        self.walk(ContractTreeSelection::AllDeclared, &mut |key, executable| {
            let relative = key.strip_prefix(&format!("{}.", self.path)).unwrap_or(&key).to_string();
            out.push((relative, executable.clone()));
        });
        out
    }

    /// Private storage directories the episode process removes after confinement.
    pub fn cleanup_roots(&self) -> Vec<PathBuf> {
        self.fingerprint_entries()
            .into_iter()
            .filter_map(|(_, executable)| executable.cleanup_root().map(Path::to_path_buf))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    /// Descriptor mappings and a sealed manifest for launching `child_id`.
    pub fn child_descriptors(&self, child_id: &str) -> Result<Vec<(i32, Arc<OwnedFd>)>, String> {
        let found = self.fingerprint_entries();
        let mut entries = Vec::new();
        let mut executable_ids = BTreeMap::new();
        let mut executables = Vec::new();
        let mut mappings = Vec::new();
        for (key, executable) in found {
            let fingerprint = (executable.sha256.clone(), executable.invocation_name.clone());
            let executable_index = match executable_ids.get(&fingerprint) {
                Some(index) => *index,
                None => {
                    let index = executables.len();
                    let offset: i32 = index.try_into().map_err(|_| "too many inherited executables".to_string())?;
                    let fd = FIRST_EXECUTABLE_FD.checked_add(offset).ok_or("too many inherited executables")?;
                    executables.push(ManifestExecutable {
                        fd,
                        sha256: executable.sha256.clone(),
                        invocation_name: executable.invocation_name.clone(),
                        stored_path: executable.stored_path.clone(),
                    });
                    mappings.push((fd, executable.fd.clone()));
                    executable_ids.insert(fingerprint, index);
                    index
                }
            };
            entries.push(ManifestEntry { key, executable: executable_index });
        }
        let manifest = Manifest { kind: MANIFEST_KIND.into(), episode_id: child_id.into(), executables, entries };
        let bytes = serde_json::to_vec(&manifest).map_err(|e| e.to_string())?;
        mappings.push((MANIFEST_FD, sealed_file("foe-executable-manifest", &bytes)?));
        Ok(mappings)
    }

    fn walk<'a>(
        &'a self,
        selection: ContractTreeSelection,
        visit: &mut impl FnMut(String, &'a Arc<CapturedExecutable>),
    ) {
        for (name, executable) in &self.tools {
            visit(format!("{}.tool_defs.{name}.exec", self.path), executable);
        }
        if let Some(executable) = &self.transport {
            visit(format!("{}.model.exec", self.path), executable);
        }
        for (reachable, child) in self.child_contracts.values() {
            if selection == ContractTreeSelection::AllDeclared || *reachable {
                child.walk(selection, visit);
            }
        }
        for child in self.workflow.values() {
            child.walk(selection, visit);
        }
    }
}

fn needs_storage(contract: &ResolvedContract) -> bool {
    contract
        .contract_tree(ContractTreeSelection::AllDeclared)
        .into_iter()
        .any(|(_, contract)| contract.captured_transport.is_some() || !contract.captured_executables.is_empty())
}

fn relative_key(path: &str, field: &str) -> String {
    let key = format!("{path}.{field}");
    key.strip_prefix("contract.").unwrap_or(&key).to_string()
}

fn sealed_file(name: &str, bytes: &[u8]) -> Result<Arc<OwnedFd>, String> {
    let fd = memfd_create(name, MFdFlags::MFD_ALLOW_SEALING | MFdFlags::MFD_CLOEXEC)
        .map_err(|e| format!("immutable executable storage: {e}"))?;
    let mut file = File::from(fd);
    file.write_all(bytes).map_err(|e| format!("immutable executable storage: {e}"))?;
    fcntl(&file, FcntlArg::F_ADD_SEALS(REQUIRED_SEALS)).map_err(|e| format!("immutable executable sealing: {e}"))?;
    Ok(Arc::new(OwnedFd::from(file)))
}

#[derive(Debug)]
struct StoreRoot(PathBuf);

impl Drop for StoreRoot {
    fn drop(&mut self) {
        if let Err(error) = std::fs::remove_dir_all(&self.0) {
            if error.kind() != std::io::ErrorKind::NotFound {
                eprintln!("foe: {}: remove private executable store: {error}", self.0.display());
            }
        }
    }
}

struct Store {
    root: Arc<StoreRoot>,
    executables: BTreeMap<(String, std::ffi::OsString), (PathBuf, Arc<OwnedFd>)>,
}

impl Store {
    fn create(preferred: &Path, write_roots: &[PathBuf]) -> Result<Self, String> {
        let beside_episode = preferred.parent().unwrap_or(preferred);
        let mut candidates = Vec::new();
        for candidate in [beside_episode, Path::new("/tmp"), Path::new("/var/tmp")] {
            if let Ok(candidate) = std::fs::canonicalize(candidate) {
                if !candidates.contains(&candidate) {
                    candidates.push(candidate);
                }
            }
        }
        let mut failures = Vec::new();
        for parent in candidates {
            if write_roots.iter().any(|root| parent.starts_with(root)) {
                failures.push(format!("{}: lies under a declared write root", parent.display()));
                continue;
            }
            match Self::create_under(&parent) {
                Ok(store) => return Ok(store),
                Err(error) => failures.push(error),
            }
        }
        Err(format!("configured executable storage: {}", failures.join("; ")))
    }

    fn create_under(parent: &Path) -> Result<Self, String> {
        let flags =
            statvfs(parent).map_err(|e| format!("{}: inspect executable filesystem: {e}", parent.display()))?.flags();
        if flags.contains(FsFlags::ST_NOEXEC) {
            return Err(format!("{}: filesystem has noexec", parent.display()));
        }
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        for attempt in 0..100u32 {
            let root = parent.join(format!("foe-executables-{}-{stamp}-{attempt}", std::process::id()));
            match std::fs::create_dir(&root) {
                Ok(()) => {
                    let root = Arc::new(StoreRoot(root));
                    std::fs::set_permissions(&root.0, std::fs::Permissions::from_mode(0o700))
                        .map_err(|e| format!("{}: executable store permissions: {e}", root.0.display()))?;
                    return Ok(Self { root, executables: BTreeMap::new() });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("{}: create executable store: {error}", parent.display())),
            }
        }
        Err(format!("{}: cannot allocate a unique executable store", parent.display()))
    }

    fn write(&mut self, captured: &CapturedExecutableSource) -> Result<(PathBuf, Arc<OwnedFd>), String> {
        let fingerprint = (captured.sha256.clone(), captured.invocation_name.clone().into());
        if let Some((path, fd)) = self.executables.get(&fingerprint) {
            return Ok((path.clone(), fd.clone()));
        }
        let directory = self.root.0.join(self.executables.len().to_string());
        std::fs::create_dir(&directory)
            .map_err(|e| format!("{}: create captured-executable directory: {e}", directory.display()))?;
        let path = directory.join(&captured.invocation_name);
        let mut output = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| format!("{}: create captured executable: {e}", path.display()))?;
        output
            .write_all(&captured.bytes)
            .and_then(|_| output.sync_all())
            .and_then(|_| output.set_permissions(std::fs::Permissions::from_mode(0o500)))
            .map_err(|e| format!("{}: write captured executable: {e}", path.display()))?;
        let metadata =
            output.metadata().map_err(|e| format!("{}: inspect captured executable: {e}", path.display()))?;
        let flags =
            statvfs(&path).map_err(|e| format!("{}: inspect executable filesystem: {e}", path.display()))?.flags();
        if metadata.permissions().mode() & 0o111 == 0 || flags.contains(FsFlags::ST_NOEXEC) {
            return Err(format!("{}: captured file is not executable", path.display()));
        }
        let mut file = File::open(&path).map_err(|e| format!("{}: open captured executable: {e}", path.display()))?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| format!("{}: read captured executable: {e}", path.display()))?;
        if bytes.as_slice() != captured.bytes.as_ref() {
            return Err(format!("{}: stored bytes differ from sha256 {}", path.display(), captured.sha256));
        }
        let fd = Arc::new(OwnedFd::from(file));
        self.executables.insert(fingerprint, (path.clone(), fd.clone()));
        Ok((path, fd))
    }
}

#[derive(Debug)]
struct InheritedExecutable {
    sha256: String,
    bytes: Arc<[u8]>,
    invocation_name: std::ffi::OsString,
    stored_path: PathBuf,
    fd: Arc<OwnedFd>,
}

/// CapturedExecutable descriptors inherited from the parent process of a spawned
/// episode. The manifest is optional so an interrupted child can be resumed
/// independently from its recorded configuration.
#[derive(Debug, Default)]
pub struct InheritedExecutables {
    entries: BTreeMap<String, Arc<InheritedExecutable>>,
}

impl InheritedExecutables {
    pub fn read(child_id: &str) -> Result<Option<Self>, String> {
        let path = PathBuf::from(format!("/proc/self/fd/{MANIFEST_FD}"));
        let mut manifest_file = match File::open(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(format!("{}: {error}", path.display())),
        };
        require_sealed(&manifest_file, "inherited executable manifest")?;
        let mut bytes = Vec::new();
        manifest_file.read_to_end(&mut bytes).map_err(|e| format!("{}: {e}", path.display()))?;
        let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", path.display()))?;
        if manifest.kind != MANIFEST_KIND || manifest.episode_id != child_id {
            return Err(format!("{}: does not describe episode {child_id}", path.display()));
        }
        close(MANIFEST_FD)
            .map_err(|e| format!("{}: cannot close inherited manifest descriptor: {e}", path.display()))?;
        let mut fds = BTreeSet::new();
        let mut executables = Vec::new();
        for captured in manifest.executables {
            if !fds.insert(captured.fd) || captured.fd < FIRST_EXECUTABLE_FD {
                return Err(format!("{}: contains a duplicate or reserved descriptor", path.display()));
            }
            let fd_path = PathBuf::from(format!("/proc/self/fd/{}", captured.fd));
            let mut file = File::open(&fd_path).map_err(|e| format!("{}: {e}", fd_path.display()))?;
            close(captured.fd)
                .map_err(|e| format!("{}: cannot close inherited executable descriptor: {e}", fd_path.display()))?;
            let metadata = file.metadata().map_err(|e| format!("{}: {e}", fd_path.display()))?;
            if metadata.permissions().mode() & 0o222 != 0 || metadata.permissions().mode() & 0o111 == 0 {
                return Err(format!("{}: inherited executable is writable or not executable", fd_path.display()));
            }
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).map_err(|e| format!("{}: {e}", fd_path.display()))?;
            let actual = foe_contract::fingerprint::sha256_hex(&bytes);
            if actual != captured.sha256 {
                return Err(format!("{}: has sha256 {actual}; expected {}", fd_path.display(), captured.sha256));
            }
            executables.push(Arc::new(InheritedExecutable {
                sha256: captured.sha256,
                bytes: Arc::from(bytes),
                invocation_name: captured.invocation_name,
                stored_path: captured.stored_path,
                fd: Arc::new(OwnedFd::from(file)),
            }));
        }
        let mut keys = BTreeSet::new();
        let mut entries = BTreeMap::new();
        for entry in manifest.entries {
            if !keys.insert(entry.key.clone()) {
                return Err(format!("{}: contains a duplicate configuration key", path.display()));
            }
            let captured = executables.get(entry.executable).ok_or_else(|| {
                format!("{}: entry {} names absent executable {}", path.display(), entry.key, entry.executable)
            })?;
            entries.insert(entry.key, captured.clone());
        }
        Ok(Some(Self { entries }))
    }

    pub fn captured_bytes(&self) -> BTreeMap<String, (Arc<[u8]>, std::ffi::OsString)> {
        self.entries
            .iter()
            .map(|(key, executable)| (key.clone(), (executable.bytes.clone(), executable.invocation_name.clone())))
            .collect()
    }
}

fn require_sealed(fd: &File, name: &str) -> Result<(), String> {
    let seals = fcntl(fd, FcntlArg::F_GET_SEALS).map_err(|e| format!("{name}: cannot inspect seals: {e}"))?;
    if seals & REQUIRED_SEALS.bits() != REQUIRED_SEALS.bits() {
        return Err(format!("{name}: executable descriptor is not immutable"));
    }
    Ok(())
}

#[derive(Debug, Serialize, Deserialize)]
struct Manifest {
    kind: String,
    episode_id: String,
    executables: Vec<ManifestExecutable>,
    entries: Vec<ManifestEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ManifestExecutable {
    fd: i32,
    sha256: String,
    invocation_name: std::ffi::OsString,
    stored_path: PathBuf,
}

#[derive(Debug, Serialize, Deserialize)]
struct ManifestEntry {
    key: String,
    executable: usize,
}

/// `/proc` path used only to invoke an already-held executable descriptor.
pub fn process_fd_path(fd: i32) -> PathBuf {
    PathBuf::from(format!("/proc/self/fd/{fd}"))
}

pub fn next_child_fd(used: impl Iterator<Item = i32>) -> i32 {
    let used: BTreeSet<i32> = used.collect();
    (3..).find(|fd| !used.contains(fd)).expect("a child descriptor is available")
}

pub fn parent_fd_path(fd: &Arc<OwnedFd>) -> PathBuf {
    PathBuf::from(format!("/proc/self/fd/{}", fd.as_raw_fd()))
}
