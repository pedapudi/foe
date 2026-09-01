//! Construction-committed executable images for tools and transports.

use foe_program::document::{ExecutableImage, ResolvedProgram};
use nix::fcntl::{fcntl, FcntlArg, SealFlag};
use nix::sys::memfd::{memfd_create, MFdFlags};
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

/// One source path with the bytes committed during program construction.
#[derive(Debug)]
pub struct Executable {
    pub source: PathBuf,
    pub sha256: String,
    image: Arc<[u8]>,
    stored_path: PathBuf,
    fd: Arc<OwnedFd>,
    _store: Option<Arc<StoreRoot>>,
}

impl Executable {
    fn store(image: &ExecutableImage, store: &mut Store) -> Result<Arc<Self>, String> {
        let (stored_path, fd) = store.write(image)?;
        Ok(Arc::new(Self {
            source: image.path.clone(),
            sha256: image.sha256.clone(),
            image: image.bytes.clone(),
            stored_path,
            fd,
            _store: Some(store.root.clone()),
        }))
    }

    fn inherited(image: &ExecutableImage, inherited: &InheritedExecutable) -> Result<Arc<Self>, String> {
        if inherited.sha256 != image.sha256 || inherited.bytes.as_ref() != image.bytes.as_ref() {
            return Err(format!("{}: inherited executable does not match the constructed bytes", image.path.display()));
        }
        Ok(Arc::new(Self {
            source: image.path.clone(),
            sha256: image.sha256.clone(),
            image: image.bytes.clone(),
            stored_path: inherited.stored_path.clone(),
            fd: inherited.fd.clone(),
            _store: None,
        }))
    }

    pub fn fd(&self) -> &Arc<OwnedFd> {
        &self.fd
    }

    pub fn image(&self) -> &[u8] {
        &self.image
    }

    pub fn stored_path(&self) -> &Path {
        &self.stored_path
    }

    fn cleanup_root(&self) -> Option<&Path> {
        self._store.as_ref().and_then(|root| root.0.parent())
    }

    /// Confirms that the held inode still contains the construction bytes
    /// and remains executable before an authority-bearing process starts.
    pub fn verify(&self) -> Result<(), String> {
        let path = parent_fd_path(&self.fd);
        let mut file = File::open(&path)
            .map_err(|e| format!("{}: cannot read committed executable: {e}", self.source.display()))?;
        let metadata = file
            .metadata()
            .map_err(|e| format!("{}: cannot inspect committed executable: {e}", self.source.display()))?;
        let mode = metadata.permissions().mode();
        if mode & 0o222 != 0 || mode & 0o111 == 0 {
            return Err(format!("{}: committed executable is writable or not executable", self.source.display()));
        }
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)
            .map_err(|e| format!("{}: cannot read committed executable: {e}", self.source.display()))?;
        if bytes.as_slice() != self.image.as_ref() || foe_program::identity::sha256_hex(&bytes) != self.sha256 {
            return Err(format!("{}: committed executable differs from sha256 {}", self.source.display(), self.sha256));
        }
        Ok(())
    }

    /// Commits an executable for callers that construct an exec transport
    /// without a complete program document.
    pub fn load(path: &Path) -> Result<Arc<Self>, String> {
        use std::os::unix::fs::PermissionsExt;
        if !path.is_absolute() {
            return Err("is not an absolute path".into());
        }
        let path = std::fs::canonicalize(path).map_err(|e| format!("names an existing path: {e}"))?;
        let mut file = File::open(&path).map_err(|e| format!("is readable for construction: {e}"))?;
        let metadata = file.metadata().map_err(|e| format!("has readable metadata: {e}"))?;
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            return Err("names an executable file".into());
        }
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| format!("is readable for construction: {e}"))?;
        let image =
            ExecutableImage { path, sha256: foe_program::identity::sha256_hex(&bytes), bytes: Arc::from(bytes) };
        let mut store = Store::create(Path::new("/tmp"))?;
        Self::store(&image, &mut store)
    }
}

/// Committed executables reachable from one episode, arranged like its
/// resolved child-program tree.
#[derive(Debug, Clone, Default)]
pub struct ExecutableTree {
    pub tools: BTreeMap<String, Arc<Executable>>,
    pub transport: Option<Arc<Executable>>,
    children: BTreeMap<String, (String, ExecutableTree)>,
}

impl ExecutableTree {
    pub fn materialize(program: &ResolvedProgram, preferred_parent: &Path) -> Result<Self, String> {
        if !needs_storage(program) {
            return Self::build(program, "", None, None);
        }
        let parent = storage_parent(program, preferred_parent)?;
        let mut store = Store::create(&parent)?;
        Self::build(program, "", None, Some(&mut store))
    }

    pub fn from_inherited(program: &ResolvedProgram, inherited: &InheritedExecutables) -> Result<Self, String> {
        Self::build(program, "", Some(inherited), None)
    }

    fn build(
        program: &ResolvedProgram,
        prefix: &str,
        inherited: Option<&InheritedExecutables>,
        mut store: Option<&mut Store>,
    ) -> Result<Self, String> {
        let mut make = |key: String, image: &ExecutableImage| match inherited.and_then(|all| all.entries.get(&key)) {
            Some(found) => Executable::inherited(image, found),
            None if inherited.is_some() => Err(format!("{key}: inherited executable is absent")),
            None => Executable::store(image, store.as_deref_mut().expect("a root construction has a store")),
        };
        let mut tools = BTreeMap::new();
        for (name, image) in &program.executable_images {
            tools.insert(name.clone(), make(key(prefix, &format!("tool_defs.{name}.exec")), image)?);
        }
        let transport =
            program.transport_executable.as_ref().map(|image| make(key(prefix, "model.exec"), image)).transpose()?;
        let mut children = BTreeMap::new();
        for (name, child) in program.programs.iter().filter(|(name, _)| program.grants.spawn.contains(name)) {
            let edge = format!("programs.{name}");
            children.insert(
                name.clone(),
                (edge.clone(), Self::build(child, &key(prefix, &edge), inherited, store.as_deref_mut())?),
            );
        }
        for (path, child) in &program.workflow_programs {
            let dotted = path.replace('/', ".workflow.nodes.");
            let edge = format!("workflow.nodes.{dotted}.model");
            children.insert(
                path.clone(),
                (edge.clone(), Self::build(child, &key(prefix, &edge), inherited, store.as_deref_mut())?),
            );
        }
        Ok(Self { tools, transport, children })
    }

    pub fn child(&self, name: &str) -> Option<&ExecutableTree> {
        self.children.get(name).map(|(_, child)| child)
    }

    /// Every executable inode that the episode sandbox must authorize.
    pub fn reachable(&self) -> Vec<Arc<Executable>> {
        let mut out: Vec<_> = self.reachable_entries().into_iter().map(|(_, executable)| executable).collect();
        let mut seen = BTreeSet::new();
        out.retain(|executable| seen.insert(executable.stored_path.clone()));
        out
    }

    /// Configuration keys and committed images for every reachable executable.
    pub fn reachable_entries(&self) -> Vec<(String, Arc<Executable>)> {
        let mut out = Vec::new();
        self.walk("", &mut |key, executable| out.push((key, executable.clone())));
        out
    }

    /// Private directories the episode process removes after confinement.
    pub fn cleanup_roots(&self) -> Vec<PathBuf> {
        self.reachable()
            .into_iter()
            .filter_map(|executable| executable.cleanup_root().map(Path::to_path_buf))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    /// Descriptor mappings and a sealed manifest for launching `child_id`.
    pub fn child_descriptors(&self, child_id: &str) -> Result<Vec<(i32, Arc<OwnedFd>)>, String> {
        let mut found = Vec::new();
        self.walk("", &mut |key, executable| found.push((key, executable)));
        let mut entries = Vec::new();
        let mut mappings = Vec::new();
        for (offset, (key, executable)) in found.into_iter().enumerate() {
            let offset: i32 = offset.try_into().map_err(|_| "too many inherited executables".to_string())?;
            let fd = FIRST_EXECUTABLE_FD.checked_add(offset).ok_or("too many inherited executables")?;
            entries.push(ManifestEntry { key, fd, sha256: executable.sha256.clone() });
            mappings.push((fd, executable.fd.clone()));
        }
        let manifest = Manifest { kind: MANIFEST_KIND.into(), episode_id: child_id.into(), entries };
        let bytes = serde_json::to_vec(&manifest).map_err(|e| e.to_string())?;
        mappings.push((MANIFEST_FD, sealed_file("foe-executable-manifest", &bytes)?));
        Ok(mappings)
    }

    fn walk<'a>(&'a self, prefix: &str, visit: &mut impl FnMut(String, &'a Arc<Executable>)) {
        for (name, executable) in &self.tools {
            visit(key(prefix, &format!("tool_defs.{name}.exec")), executable);
        }
        if let Some(executable) = &self.transport {
            visit(key(prefix, "model.exec"), executable);
        }
        for (edge, child) in self.children.values() {
            let prefix = key(prefix, edge);
            child.walk(&prefix, visit);
        }
    }
}

fn needs_storage(program: &ResolvedProgram) -> bool {
    program.transport_executable.is_some()
        || !program.executable_images.is_empty()
        || program.reachable_programs().any(needs_storage)
}

fn storage_parent(program: &ResolvedProgram, preferred: &Path) -> Result<PathBuf, String> {
    let beside_episode = preferred.parent().unwrap_or(preferred);
    for candidate in [beside_episode, Path::new("/tmp"), Path::new("/var/tmp")] {
        let Ok(candidate) = std::fs::canonicalize(candidate) else {
            continue;
        };
        if program.grants.write.iter().all(|root| !candidate.starts_with(root)) {
            return Ok(candidate);
        }
    }
    Err(format!(
        "configured executable storage: no writable runtime directory lies outside grants.write {:?}",
        program.grants.write
    ))
}

fn key(prefix: &str, field: &str) -> String {
    format!("{prefix}.{field}").trim_start_matches('.').to_string()
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
}

impl Store {
    fn create(parent: &Path) -> Result<Self, String> {
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
                    return Ok(Self { root });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("{}: create executable store: {error}", root.display())),
            }
        }
        Err(format!("{}: cannot allocate a unique executable store", parent.display()))
    }

    fn write(&mut self, image: &ExecutableImage) -> Result<(PathBuf, Arc<OwnedFd>), String> {
        let directory = self.root.0.join(&image.sha256);
        match std::fs::create_dir(&directory) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(format!("{}: create executable digest directory: {error}", directory.display())),
        }
        let name = image.path.file_name().unwrap_or_else(|| std::ffi::OsStr::new("executable"));
        let path = directory.join(name);
        if !path.exists() {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .map_err(|e| format!("{}: create executable image: {e}", path.display()))?;
            file.write_all(&image.bytes)
                .and_then(|_| file.sync_all())
                .and_then(|_| file.set_permissions(std::fs::Permissions::from_mode(0o500)))
                .map_err(|e| format!("{}: write executable image: {e}", path.display()))?;
        }
        let mut file = File::open(&path).map_err(|e| format!("{}: open executable image: {e}", path.display()))?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| format!("{}: read executable image: {e}", path.display()))?;
        if bytes.as_slice() != image.bytes.as_ref() {
            return Err(format!("{}: stored bytes differ from sha256 {}", path.display(), image.sha256));
        }
        Ok((path, Arc::new(OwnedFd::from(file))))
    }
}

#[derive(Debug)]
struct InheritedExecutable {
    sha256: String,
    bytes: Arc<[u8]>,
    stored_path: PathBuf,
    fd: Arc<OwnedFd>,
}

/// Executable descriptors inherited from the parent process of a spawned
/// episode. The manifest is optional so an interrupted child can be resumed
/// independently from its recorded configuration.
#[derive(Debug, Default)]
pub struct InheritedExecutables {
    entries: BTreeMap<String, InheritedExecutable>,
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
        let mut keys = BTreeSet::new();
        let mut fds = BTreeSet::new();
        let mut entries = BTreeMap::new();
        for entry in manifest.entries {
            if !keys.insert(entry.key.clone()) || !fds.insert(entry.fd) || entry.fd < FIRST_EXECUTABLE_FD {
                return Err(format!("{}: contains a duplicate or reserved descriptor", path.display()));
            }
            let fd_path = PathBuf::from(format!("/proc/self/fd/{}", entry.fd));
            let mut file = File::open(&fd_path).map_err(|e| format!("{}: {e}", fd_path.display()))?;
            close(entry.fd)
                .map_err(|e| format!("{}: cannot close inherited executable descriptor: {e}", fd_path.display()))?;
            let metadata = file.metadata().map_err(|e| format!("{}: {e}", fd_path.display()))?;
            if metadata.permissions().mode() & 0o222 != 0 || metadata.permissions().mode() & 0o111 == 0 {
                return Err(format!("{}: inherited executable is writable or not executable", fd_path.display()));
            }
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).map_err(|e| format!("{}: {e}", fd_path.display()))?;
            let actual = foe_program::identity::sha256_hex(&bytes);
            if actual != entry.sha256 {
                return Err(format!("{}: has sha256 {actual}; expected {}", fd_path.display(), entry.sha256));
            }
            entries.insert(
                entry.key,
                InheritedExecutable {
                    sha256: entry.sha256,
                    bytes: Arc::from(bytes),
                    stored_path: std::fs::read_link(&fd_path).unwrap_or(fd_path),
                    fd: Arc::new(OwnedFd::from(file)),
                },
            );
        }
        Ok(Some(Self { entries }))
    }

    pub fn bytes(&self) -> BTreeMap<String, Arc<[u8]>> {
        self.entries.iter().map(|(key, executable)| (key.clone(), executable.bytes.clone())).collect()
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
    entries: Vec<ManifestEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ManifestEntry {
    key: String,
    fd: i32,
    sha256: String,
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
