//! Construction-committed executable images for tools and transports.

use foe_program::document::{ExecutableImage, ProgramTreeSelection, ResolvedProgram};
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

/// One source path with the bytes committed during program construction.
#[derive(Debug)]
pub struct Executable {
    pub source: PathBuf,
    pub sha256: String,
    image: Arc<[u8]>,
    basename: std::ffi::OsString,
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
            basename: image.basename.clone(),
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
            basename: inherited.basename.clone(),
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

    pub fn basename(&self) -> &std::ffi::OsStr {
        &self.basename
    }

    pub fn stored_path(&self) -> &Path {
        &self.stored_path
    }

    fn cleanup_root(&self) -> Option<&Path> {
        self._store.as_ref().and_then(|root| root.0.parent())
    }

    /// Commits an executable for callers that construct an exec transport
    /// without a complete program document.
    pub fn load(path: &Path) -> Result<Arc<Self>, String> {
        use std::os::unix::fs::PermissionsExt;
        if !path.is_absolute() {
            return Err("is not an absolute path".into());
        }
        let basename = path.file_name().unwrap_or_else(|| std::ffi::OsStr::new("executable")).to_owned();
        let path = std::fs::canonicalize(path).map_err(|e| format!("names an existing path: {e}"))?;
        let mut file = File::open(&path).map_err(|e| format!("is readable for construction: {e}"))?;
        let metadata = file.metadata().map_err(|e| format!("has readable metadata: {e}"))?;
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            return Err("names an executable file".into());
        }
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| format!("is readable for construction: {e}"))?;
        let image = ExecutableImage {
            path,
            basename,
            sha256: foe_program::identity::sha256_hex(&bytes),
            bytes: Arc::from(bytes),
        };
        let mut store = Store::create(Path::new("/tmp/foe-standalone-executable"), &[])?;
        Self::store(&image, &mut store)
    }
}

/// Committed executables reachable from one episode, arranged like its
/// resolved child-program tree.
#[derive(Debug, Clone, Default)]
pub struct ExecutableTree {
    path: String,
    pub tools: BTreeMap<String, Arc<Executable>>,
    pub transport: Option<Arc<Executable>>,
    programs: BTreeMap<String, (bool, ExecutableTree)>,
    workflow: BTreeMap<String, ExecutableTree>,
}

impl ExecutableTree {
    pub fn materialize(program: &ResolvedProgram, preferred_parent: &Path) -> Result<Self, String> {
        if !needs_storage(program) {
            return Self::build(program, "program", None, None);
        }
        let mut store = Store::create(preferred_parent, &program.grants.write)?;
        let tree = Self::build(program, "program", None, Some(&mut store))?;
        Ok(tree)
    }

    pub fn from_inherited(program: &ResolvedProgram, inherited: &InheritedExecutables) -> Result<Self, String> {
        Self::build(program, "program", Some(inherited), None)
    }

    fn build(
        program: &ResolvedProgram,
        path: &str,
        inherited: Option<&InheritedExecutables>,
        mut store: Option<&mut Store>,
    ) -> Result<Self, String> {
        let path = path.to_string();
        let mut make = |key: String, image: &ExecutableImage| match inherited.and_then(|all| all.entries.get(&key)) {
            Some(found) => Executable::inherited(image, found),
            None if inherited.is_some() => Err(format!("{key}: inherited executable is absent")),
            None => Executable::store(image, store.as_deref_mut().expect("a root construction has a store")),
        };
        let mut tools = BTreeMap::new();
        for (name, image) in &program.executable_images {
            tools.insert(name.clone(), make(relative_key(&path, &format!("tool_defs.{name}.exec")), image)?);
        }
        let transport = program
            .transport_executable
            .as_ref()
            .map(|image| make(relative_key(&path, "model.exec"), image))
            .transpose()?;
        let mut programs = BTreeMap::new();
        for (name, child) in &program.programs {
            let reachable = program.grants.spawn.contains(name);
            let child_path = format!("{path}.programs.{name}");
            programs
                .insert(name.clone(), (reachable, Self::build(child, &child_path, inherited, store.as_deref_mut())?));
        }
        let mut workflow = BTreeMap::new();
        for (node_path, child) in &program.workflow_programs {
            let nodes = node_path.replace('/', ".workflow.nodes.");
            let child_path = format!("{path}.workflow.nodes.{nodes}.model");
            workflow.insert(node_path.clone(), Self::build(child, &child_path, inherited, store.as_deref_mut())?);
        }
        Ok(Self { path, tools, transport, programs, workflow })
    }

    pub fn child(&self, name: &str) -> Option<&ExecutableTree> {
        self.workflow.get(name).or_else(|| self.programs.get(name).map(|(_, child)| child))
    }

    /// Every executable inode that the episode sandbox must authorize.
    pub fn reachable(&self) -> Vec<Arc<Executable>> {
        let mut out: Vec<_> = self.reachable_entries().into_iter().map(|(_, executable)| executable).collect();
        let mut seen = BTreeSet::new();
        out.retain(|executable| seen.insert((executable.sha256.clone(), executable.basename.clone())));
        out
    }

    /// Configuration keys and committed images for every reachable executable.
    pub fn reachable_entries(&self) -> Vec<(String, Arc<Executable>)> {
        let mut out = Vec::new();
        self.walk(ProgramTreeSelection::ExecutableReachable, &mut |key, executable| {
            out.push((key, executable.clone()))
        });
        out
    }

    /// Configuration keys and images that contribute to this program's identity.
    pub fn identity_entries(&self) -> Vec<(String, Arc<Executable>)> {
        let mut out = Vec::new();
        self.walk(ProgramTreeSelection::AllDeclared, &mut |key, executable| {
            let relative = key.strip_prefix(&format!("{}.", self.path)).unwrap_or(&key).to_string();
            out.push((relative, executable.clone()));
        });
        out
    }

    /// Private storage parents the episode process removes after confinement.
    pub fn cleanup_roots(&self) -> Vec<PathBuf> {
        self.identity_entries()
            .into_iter()
            .filter_map(|(_, executable)| executable.cleanup_root().map(Path::to_path_buf))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    /// Descriptor mappings and a sealed manifest for launching `child_id`.
    pub fn child_descriptors(&self, child_id: &str) -> Result<Vec<(i32, Arc<OwnedFd>)>, String> {
        let found = self.identity_entries();
        let mut entries = Vec::new();
        let mut image_ids = BTreeMap::new();
        let mut images = Vec::new();
        let mut mappings = Vec::new();
        for (key, executable) in found {
            let identity = (executable.sha256.clone(), executable.basename.clone());
            let image = match image_ids.get(&identity) {
                Some(index) => *index,
                None => {
                    let index = images.len();
                    let offset: i32 = index.try_into().map_err(|_| "too many inherited executables".to_string())?;
                    let fd = FIRST_EXECUTABLE_FD.checked_add(offset).ok_or("too many inherited executables")?;
                    images.push(ManifestImage {
                        fd,
                        sha256: executable.sha256.clone(),
                        basename: executable.basename.clone(),
                        stored_path: executable.stored_path.clone(),
                    });
                    mappings.push((fd, executable.fd.clone()));
                    image_ids.insert(identity, index);
                    index
                }
            };
            entries.push(ManifestEntry { key, image });
        }
        let manifest = Manifest { kind: MANIFEST_KIND.into(), episode_id: child_id.into(), images, entries };
        let bytes = serde_json::to_vec(&manifest).map_err(|e| e.to_string())?;
        mappings.push((MANIFEST_FD, sealed_file("foe-executable-manifest", &bytes)?));
        Ok(mappings)
    }

    fn walk<'a>(&'a self, selection: ProgramTreeSelection, visit: &mut impl FnMut(String, &'a Arc<Executable>)) {
        for (name, executable) in &self.tools {
            visit(format!("{}.tool_defs.{name}.exec", self.path), executable);
        }
        if let Some(executable) = &self.transport {
            visit(format!("{}.model.exec", self.path), executable);
        }
        for (reachable, child) in self.programs.values() {
            if selection == ProgramTreeSelection::AllDeclared || *reachable {
                child.walk(selection, visit);
            }
        }
        for child in self.workflow.values() {
            child.walk(selection, visit);
        }
    }
}

fn needs_storage(program: &ResolvedProgram) -> bool {
    program
        .program_tree(ProgramTreeSelection::AllDeclared)
        .into_iter()
        .any(|(_, program)| program.transport_executable.is_some() || !program.executable_images.is_empty())
}

fn relative_key(path: &str, field: &str) -> String {
    let key = format!("{path}.{field}");
    key.strip_prefix("program.").unwrap_or(&key).to_string()
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
    images: BTreeMap<(String, std::ffi::OsString), (PathBuf, Arc<OwnedFd>)>,
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
                    return Ok(Self { root, images: BTreeMap::new() });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("{}: create executable store: {error}", parent.display())),
            }
        }
        Err(format!("{}: cannot allocate a unique executable store", parent.display()))
    }

    fn write(&mut self, image: &ExecutableImage) -> Result<(PathBuf, Arc<OwnedFd>), String> {
        let identity = (image.sha256.clone(), image.basename.clone());
        if let Some((path, fd)) = self.images.get(&identity) {
            return Ok((path.clone(), fd.clone()));
        }
        let directory = self.root.0.join(self.images.len().to_string());
        std::fs::create_dir(&directory)
            .map_err(|e| format!("{}: create executable image directory: {e}", directory.display()))?;
        let path = directory.join(&image.basename);
        let mut output = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| format!("{}: create executable image: {e}", path.display()))?;
        output
            .write_all(&image.bytes)
            .and_then(|_| output.sync_all())
            .and_then(|_| output.set_permissions(std::fs::Permissions::from_mode(0o500)))
            .map_err(|e| format!("{}: write executable image: {e}", path.display()))?;
        let metadata = output.metadata().map_err(|e| format!("{}: inspect executable image: {e}", path.display()))?;
        let flags =
            statvfs(&path).map_err(|e| format!("{}: inspect executable filesystem: {e}", path.display()))?.flags();
        if metadata.permissions().mode() & 0o111 == 0 || flags.contains(FsFlags::ST_NOEXEC) {
            return Err(format!("{}: stored image is not executable", path.display()));
        }
        let mut file = File::open(&path).map_err(|e| format!("{}: open executable image: {e}", path.display()))?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| format!("{}: read executable image: {e}", path.display()))?;
        if bytes.as_slice() != image.bytes.as_ref() {
            return Err(format!("{}: stored bytes differ from sha256 {}", path.display(), image.sha256));
        }
        let fd = Arc::new(OwnedFd::from(file));
        self.images.insert(identity, (path.clone(), fd.clone()));
        Ok((path, fd))
    }
}

#[derive(Debug)]
struct InheritedExecutable {
    sha256: String,
    bytes: Arc<[u8]>,
    basename: std::ffi::OsString,
    stored_path: PathBuf,
    fd: Arc<OwnedFd>,
}

/// Executable descriptors inherited from the parent process of a spawned
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
        let mut images = Vec::new();
        for image in manifest.images {
            if !fds.insert(image.fd) || image.fd < FIRST_EXECUTABLE_FD {
                return Err(format!("{}: contains a duplicate or reserved descriptor", path.display()));
            }
            let fd_path = PathBuf::from(format!("/proc/self/fd/{}", image.fd));
            let mut file = File::open(&fd_path).map_err(|e| format!("{}: {e}", fd_path.display()))?;
            close(image.fd)
                .map_err(|e| format!("{}: cannot close inherited executable descriptor: {e}", fd_path.display()))?;
            let metadata = file.metadata().map_err(|e| format!("{}: {e}", fd_path.display()))?;
            if metadata.permissions().mode() & 0o222 != 0 || metadata.permissions().mode() & 0o111 == 0 {
                return Err(format!("{}: inherited executable is writable or not executable", fd_path.display()));
            }
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).map_err(|e| format!("{}: {e}", fd_path.display()))?;
            let actual = foe_program::identity::sha256_hex(&bytes);
            if actual != image.sha256 {
                return Err(format!("{}: has sha256 {actual}; expected {}", fd_path.display(), image.sha256));
            }
            images.push(Arc::new(InheritedExecutable {
                sha256: image.sha256,
                bytes: Arc::from(bytes),
                basename: image.basename,
                stored_path: image.stored_path,
                fd: Arc::new(OwnedFd::from(file)),
            }));
        }
        let mut keys = BTreeSet::new();
        let mut entries = BTreeMap::new();
        for entry in manifest.entries {
            if !keys.insert(entry.key.clone()) {
                return Err(format!("{}: contains a duplicate configuration key", path.display()));
            }
            let image = images
                .get(entry.image)
                .ok_or_else(|| format!("{}: entry {} names absent image {}", path.display(), entry.key, entry.image))?;
            entries.insert(entry.key, image.clone());
        }
        Ok(Some(Self { entries }))
    }

    pub fn images(&self) -> BTreeMap<String, (Arc<[u8]>, std::ffi::OsString)> {
        self.entries
            .iter()
            .map(|(key, executable)| (key.clone(), (executable.bytes.clone(), executable.basename.clone())))
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
    images: Vec<ManifestImage>,
    entries: Vec<ManifestEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ManifestImage {
    fd: i32,
    sha256: String,
    basename: std::ffi::OsString,
    stored_path: PathBuf,
}

#[derive(Debug, Serialize, Deserialize)]
struct ManifestEntry {
    key: String,
    image: usize,
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
