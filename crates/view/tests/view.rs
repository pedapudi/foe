//! Tests for the projection, the loopback server's token and Origin rules,
//! server-sent-events resume and tailing, and the static export. The server
//! is driven with blocking `std::net::TcpStream` connections on a runtime
//! owned by each test.

use foe_log::{Event, ExhaustedLimit, Outcome};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::time::Duration;

struct ScratchDir(Option<tempfile::TempDir>);

impl ScratchDir {
    fn new(name: &str) -> Self {
        assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
        Self(Some(tempfile::Builder::new().prefix(&format!("foe-view-{name}-")).tempdir().unwrap()))
    }

    fn path(&self) -> &Path {
        self.0.as_ref().unwrap().path()
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.0.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", dir.path().display());
            dir.disable_cleanup(true);
            return;
        }
        let path = dir.path().to_path_buf();
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
    }
}

fn fixture() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/run")
}

/// A directory of episode directories: two independent runs of one
/// contract, one of which spawned a child, beside a directory that holds no
/// log at all.
fn collection() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/collection")
}

/// Starts a server on an ephemeral port. The runtime is returned so the
/// caller keeps it alive for the test's duration.
fn start(dir: &Path, port: u16) -> (tokio::runtime::Runtime, SocketAddr, String) {
    let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
    let server = rt.block_on(foe_view::serve(dir, port)).unwrap();
    let (addr, token) = (server.addr, server.token.clone());
    rt.spawn(server.wait());
    (rt, addr, token)
}

fn connect(addr: SocketAddr, target: &str, headers: &[(&str, &str)]) -> BufReader<TcpStream> {
    let mut stream = TcpStream::connect(addr).unwrap();
    stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    let mut req = format!("GET {target} HTTP/1.1\r\nHost: {addr}\r\n");
    for (name, value) in headers {
        req.push_str(&format!("{name}: {value}\r\n"));
    }
    req.push_str("\r\n");
    stream.write_all(req.as_bytes()).unwrap();
    BufReader::new(stream)
}

/// Reads the status line and the response headers, returning the status
/// code and the header block.
fn status(reader: &mut BufReader<TcpStream>) -> (u16, String) {
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let code = line.split_whitespace().nth(1).unwrap().parse().unwrap();
    let mut headers = String::new();
    loop {
        line.clear();
        reader.read_line(&mut line).unwrap();
        if line.trim_end().is_empty() {
            return (code, headers);
        }
        headers.push_str(&line);
    }
}

fn get_bytes(addr: SocketAddr, target: &str, headers: &[(&str, &str)]) -> (u16, String, Vec<u8>) {
    let mut reader = connect(addr, target, headers);
    let (code, head) = status(&mut reader);
    let mut body = Vec::new();
    reader.read_to_end(&mut body).unwrap();
    (code, head, body)
}

fn get(addr: SocketAddr, target: &str, headers: &[(&str, &str)]) -> (u16, String) {
    let (code, _, body) = get_bytes(addr, target, headers);
    (code, String::from_utf8(body).unwrap())
}

const FONTS: [&str; 6] = [
    "Inconsolata-Regular.woff2",
    "Inconsolata-Bold.woff2",
    "iAWriterMonoS-Regular.woff2",
    "iAWriterMonoS-Bold.woff2",
    "JetBrainsMono-Regular.woff2",
    "JetBrainsMono-Bold.woff2",
];

/// The font files present in the checkout, with their bytes.
fn present_fonts() -> Vec<(&'static str, Vec<u8>)> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../view/fonts");
    FONTS.iter().filter_map(|name| Some((*name, std::fs::read(dir.join(name)).ok()?))).collect()
}

/// Reads one server-sent event, returning its `id` and `data` lines.
fn next_event(reader: &mut BufReader<TcpStream>) -> (u64, String) {
    let (mut id, mut data) = (None, None);
    let mut line = String::new();
    loop {
        line.clear();
        reader.read_line(&mut line).unwrap();
        let l = line.trim_end();
        if let Some(v) = l.strip_prefix("id: ") {
            id = Some(v.parse().unwrap());
        } else if let Some(v) = l.strip_prefix("data: ") {
            data = Some(v.to_string());
        } else if let (true, Some(data)) = (l.is_empty(), &data) {
            return (id.unwrap(), data.clone());
        }
    }
}

#[test]
fn projects_tree_with_spawned_and_forked_children() {
    let tree = foe_view::project(&fixture()).unwrap();
    assert_eq!(tree.roots.len(), 1);
    let root = &tree.roots[0];
    assert_eq!(root.id, "ep_root");
    assert_eq!(root.name.as_deref(), Some("fixer"));
    assert_eq!(root.parent_id, None);
    assert!(matches!(root.outcome, Some(Outcome::Completed { .. })));
    assert_eq!((root.usage.input, root.usage.output, root.usage.cache_read), (9120, 100, 8000));
    let ids: Vec<&str> = root.children.iter().map(|c| c.id.as_str()).collect();
    assert_eq!(ids, ["ep_child", "ep_fork"]);
    let child = &root.children[0];
    assert_eq!(child.parent_id.as_deref(), Some("ep_root"));
    assert_eq!(child.fork_origin, None);
    assert!(matches!(child.outcome, Some(Outcome::Blocked { .. })));
    let fork = &root.children[1];
    let origin = fork.fork_origin.as_ref().unwrap();
    assert_eq!((origin.episode_id.as_str(), origin.seq), ("ep_root", 7));
    assert_eq!(fork.outcome, Some(Outcome::Exhausted { limit: ExhaustedLimit::ModelCalls }));
    assert!(fork.children.is_empty());
}

#[test]
fn projects_one_root_per_episode_directory_of_a_collection() {
    let tree = foe_view::project(&collection()).unwrap();
    let ids: Vec<&str> = tree.roots.iter().map(|r| r.id.as_str()).collect();
    assert_eq!(ids, ["ep_first", "ep_second"], "each entry holding a log is a root, in directory order");
    assert!(tree.roots.iter().all(|r| r.parent_id.is_none() && r.fork_origin.is_none()));
    // A root of a collection keeps the descendants under its own
    // `children/`, so nesting and collection are the same projection.
    let second = &tree.roots[1];
    assert_eq!(second.children.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(), ["ep_second_child"]);
    assert!(matches!(second.outcome, Some(Outcome::Blocked { .. })));
    assert_eq!((second.usage.input, second.usage.output), (2400, 60));
}

#[test]
fn projects_an_episode_directory_of_a_collection_on_its_own() {
    let tree = foe_view::project(&collection().join("ep_first")).unwrap();
    assert_eq!(tree.roots.len(), 1, "a directory with a log of its own is one episode directory");
    assert_eq!(tree.roots[0].id, "ep_first");
}

#[test]
fn exports_every_root_of_a_collection() {
    let html = foe_view::export(&collection()).unwrap();
    for id in ["ep_first", "ep_second", "ep_second_child"] {
        assert!(html.contains(&format!("\"id\":\"{id}\"")), "{id} missing from the export");
    }
    assert!(html.contains("\"tree\":{\"roots\":[{\"id\":\"ep_first\""));
}

#[test]
fn project_names_the_unreadable_log() {
    let missing = fixture().join("children/ep_child/children/none");
    let err = foe_view::project(&missing).unwrap_err().to_string();
    assert!(err.contains("none/episode.jsonl"), "{err}");
}

#[test]
fn a_directory_with_no_log_anywhere_names_the_log_it_lacks() {
    // The third case of the discovery rule: a directory that is neither an
    // episode directory nor a collection is read as an episode directory,
    // so the failure names the file it could not open rather than
    // reporting an empty tree.
    let dir = ScratchDir::new("empty");
    std::fs::create_dir_all(dir.join("notes")).unwrap();
    let err = foe_view::project(&dir).unwrap_err().to_string();
    assert!(err.contains(&format!("{}/episode.jsonl", dir.display())), "{err}");
}

#[test]
fn binds_an_ephemeral_port_when_the_requested_one_is_taken() {
    let taken = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = taken.local_addr().unwrap().port();
    let (_rt, addr, _) = start(&fixture(), port);
    assert_ne!(addr.port(), port);
    assert!(addr.ip().is_loopback());
}

#[test]
fn rejects_missing_or_wrong_token() {
    let (_rt, addr, token) = start(&fixture(), 0);
    assert_eq!(get(addr, "/episodes", &[]).0, 401);
    assert_eq!(get(addr, "/episodes", &[("X-Foe-Token", "0000")]).0, 401);
    assert_eq!(get(addr, "/", &[]).0, 401);
    // The query form is accepted only where a browser cannot send a header.
    assert_eq!(get(addr, &format!("/episodes?token={token}"), &[]).0, 401);
    let (code, body) = get(addr, "/episodes", &[("X-Foe-Token", &token)]);
    assert_eq!(code, 200);
    assert!(body.contains("\"id\":\"ep_root\"") && body.contains("\"roots\""), "{body}");
    let (code, body) = get(addr, &format!("/?token={token}"), &[]);
    assert_eq!(code, 200);
    assert!(body.contains("\"mode\":\"live\"") && body.contains(&token), "{body}");
    assert_eq!(get(addr, "/nothing", &[("X-Foe-Token", &token)]).0, 404);
}

#[test]
fn rejects_foreign_origin() {
    let (_rt, addr, token) = start(&fixture(), 0);
    let auth = ("X-Foe-Token", token.as_str());
    assert_eq!(get(addr, "/episodes", &[auth, ("Origin", "http://evil.example")]).0, 403);
    assert_eq!(get(addr, "/episodes", &[auth, ("Origin", "null")]).0, 403);
    let own = format!("http://{addr}");
    assert_eq!(get(addr, "/episodes", &[auth, ("Origin", &own)]).0, 200);
}

#[test]
fn sse_resumes_from_last_event_id() {
    let (_rt, addr, token) = start(&fixture(), 0);
    let target = format!("/events?episode=ep_root&token={token}");
    let mut reader = connect(addr, &target, &[]);
    let (code, head) = status(&mut reader);
    assert_eq!(code, 200);
    assert!(head.contains("Content-Type: text/event-stream"), "{head}");
    let (id, data) = next_event(&mut reader);
    assert_eq!(id, 0);
    assert!(data.contains("\"type\":\"episode/start\""), "{data}");

    let mut reader = connect(addr, &target, &[("Last-Event-ID", "5")]);
    assert_eq!(status(&mut reader).0, 200);
    let (id, data) = next_event(&mut reader);
    assert_eq!(id, 6);
    let event: Event = serde_json::from_str(&data).unwrap();
    assert_eq!(event.seq, 6);
    assert_eq!(next_event(&mut reader).0, 7);
}

#[test]
fn sse_delivers_events_appended_while_connected() {
    let dir = ScratchDir::new("tail");
    let lines: Vec<String> =
        std::fs::read_to_string(fixture().join("episode.jsonl")).unwrap().lines().map(str::to_string).collect();
    let log = dir.join("episode.jsonl");
    std::fs::write(&log, format!("{}\n{}\n", lines[0], lines[1])).unwrap();

    let (_rt, addr, token) = start(&dir, 0);
    let mut reader = connect(addr, &format!("/events?episode=ep_root&token={token}"), &[]);
    assert_eq!(status(&mut reader).0, 200);
    assert_eq!(next_event(&mut reader).0, 0);
    assert_eq!(next_event(&mut reader).0, 1);

    // A partial line is not an event until its newline arrives.
    let mut file = std::fs::OpenOptions::new().append(true).open(&log).unwrap();
    let (head, tail) = lines[2].split_at(20);
    file.write_all(head.as_bytes()).unwrap();
    file.flush().unwrap();
    std::thread::sleep(Duration::from_millis(600));
    file.write_all(format!("{tail}\n").as_bytes()).unwrap();
    file.flush().unwrap();
    let (id, data) = next_event(&mut reader);
    assert_eq!(id, 2);
    assert!(data.contains("\"type\":\"request/header\""), "{data}");
}

#[test]
fn export_contains_every_event() {
    let html = foe_view::export(&fixture()).unwrap();
    assert!(html.contains("\"mode\":\"static\""));
    assert!(html.contains("<div id=\"app\"></div>"));
    let boot = html.split("window.__FOE__=").nth(1).unwrap().split(";</script>").next().unwrap();
    assert!(!boot.contains('<') && boot.contains("\\u003cfirst>"), "event text must not close the script");
    for rel in ["episode.jsonl", "children/ep_child/episode.jsonl", "children/ep_fork/episode.jsonl"] {
        for line in std::fs::read_to_string(fixture().join(rel)).unwrap().lines() {
            let event: Event = serde_json::from_str(line).unwrap();
            let wire = serde_json::to_string(&event).unwrap().replace('<', "\\u003c");
            assert!(html.contains(&wire), "missing from export: {wire}");
        }
    }
    assert_eq!(html.matches("\"type\":\"episode/start\"").count(), 3);
    assert!(html.contains("\"tree\":{\"roots\":[{\"id\":\"ep_root\""));
}

#[test]
fn serves_embedded_fonts_to_header_token_only() {
    let (_rt, addr, token) = start(&fixture(), 0);
    let auth = ("X-Foe-Token", token.as_str());
    for (name, bytes) in present_fonts() {
        let (code, head, body) = get_bytes(addr, &format!("/fonts/{name}"), &[auth]);
        assert_eq!(code, 200, "{name}");
        assert!(head.contains("Content-Type: font/woff2") && head.contains("immutable"), "{head}");
        assert_eq!(body, bytes, "{name}");
        assert_eq!(get(addr, &format!("/fonts/{name}?token={token}"), &[]).0, 401);
    }
    assert_eq!(get(addr, "/fonts/Missing.woff2", &[auth]).0, 404);
    for name in FONTS.iter().filter(|n| !present_fonts().iter().any(|(p, _)| p == *n)) {
        assert_eq!(get(addr, &format!("/fonts/{name}"), &[auth]).0, 404, "{name}");
    }
}

#[test]
fn export_inlines_every_present_font() {
    // The CSS embedded at build time decides how many references exist.
    let css = include_str!(concat!(env!("OUT_DIR"), "/viewer.css"));
    let html = foe_view::export(&fixture()).unwrap();
    let mut expected = 0;
    for (name, _) in present_fonts() {
        let path = format!("/fonts/{name}");
        expected += css.matches(&path).count();
        assert!(!html.contains(&path), "{path} left unresolved in export");
    }
    assert_eq!(html.matches("data:font/woff2;base64,").count(), expected);
    if present_fonts().len() == FONTS.len() && FONTS.iter().all(|n| css.contains(&format!("/fonts/{n}"))) {
        assert!(expected >= FONTS.len(), "every font inlined at least once");
    }
}
