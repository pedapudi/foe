//! Loopback HTTP/1.1 server: the page, the episode tree, and one
//! server-sent-events stream per episode. Written directly over tokio's
//! TCP types; it understands exactly the requests the bundle sends. Every
//! response closes its connection, so no request body or keep-alive
//! handling exists.

use crate::project::Store;
use crate::Error;
use std::collections::HashMap;
use std::io::Read;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::tcp::OwnedWriteHalf;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;

const POLL_INTERVAL: Duration = Duration::from_millis(250);
const KEEP_ALIVE: Duration = Duration::from_secs(15);
const MAX_HEADER_BYTES: u64 = 16 * 1024;

/// A running server. Its tasks live on the runtime that called [`serve`];
/// `wait` blocks until the accept loop ends, which is never in normal use.
pub struct Server {
    pub addr: SocketAddr,
    pub token: String,
    accept: tokio::task::JoinHandle<()>,
}

impl Server {
    pub async fn wait(self) {
        let _ = self.accept.await;
    }
}

struct Shared {
    store: Mutex<Store>,
    origin: String,
    token: String,
    /// Bumped by the tailing task whenever any log grew.
    changed: watch::Receiver<u64>,
}

impl Shared {
    fn store(&self) -> MutexGuard<'_, Store> {
        self.store.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// Binds `127.0.0.1:port`, or an ephemeral port when that one is taken,
/// prints the URL with its token to standard error once, and starts the
/// tailing task and the accept loop on the current runtime.
pub async fn serve(dir: &Path, port: u16) -> Result<Server, Error> {
    let listener = match TcpListener::bind(("127.0.0.1", port)).await {
        Ok(listener) => listener,
        Err(_) => TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| Error::Io("bind 127.0.0.1".into(), e))?,
    };
    let addr = listener.local_addr().map_err(|e| Error::Io("local address".into(), e))?;
    let mut bytes = [0u8; 16];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut bytes))
        .map_err(|e| Error::Io("read /dev/urandom".into(), e))?;
    let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    let (tx, changed) = watch::channel(0);
    let shared = Arc::new(Shared {
        store: Mutex::new(Store::new(dir)),
        origin: format!("http://{addr}"),
        token: token.clone(),
        changed,
    });
    eprintln!("foe viewer: {}/?token={token}", shared.origin);
    // Everything already on disk is read before the first request is
    // accepted, so an early `/episodes` never answers with an empty tree.
    // An error here is the same transient kind the tailing task tolerates.
    let _ = shared.store().poll();
    tokio::spawn(tail(shared.clone(), tx));
    let accept = tokio::spawn(accept_loop(listener, shared));
    Ok(Server { addr, token, accept })
}

async fn tail(shared: Arc<Shared>, tx: watch::Sender<u64>) {
    loop {
        let s = shared.clone();
        let polled = tokio::task::spawn_blocking(move || s.store().poll()).await;
        // An error is transient while a writer is mid-append or a child
        // directory has no log yet; it is retried on the next tick. Waking
        // clients on an error is harmless, so only a clean "nothing new"
        // skips the notification.
        if !matches!(polled, Ok(Ok(false))) {
            tx.send_modify(|generation| *generation += 1);
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
}

async fn accept_loop(listener: TcpListener, shared: Arc<Shared>) {
    loop {
        if let Ok((stream, _)) = listener.accept().await {
            let shared = shared.clone();
            tokio::spawn(async move {
                let _ = handle(stream, shared).await;
            });
        }
    }
}

struct Request {
    method: String,
    path: String,
    query: HashMap<String, String>,
    /// Header names lowercased.
    headers: HashMap<String, String>,
}

/// Parses the request line and headers. `None` when the input is not an
/// HTTP request or exceeds the header size limit.
async fn read_request<R: AsyncRead + Unpin>(rd: &mut BufReader<R>) -> std::io::Result<Option<Request>> {
    let mut line = String::new();
    rd.read_line(&mut line).await?;
    let mut parts = line.split_whitespace();
    let (Some(method), Some(target)) = (parts.next(), parts.next()) else {
        return Ok(None);
    };
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let query =
        query.split('&').filter_map(|kv| kv.split_once('=')).map(|(k, v)| (k.to_string(), v.to_string())).collect();
    let (method, path) = (method.to_string(), path.to_string());
    let mut headers = HashMap::new();
    loop {
        line.clear();
        if rd.read_line(&mut line).await? == 0 {
            return Ok(None);
        }
        let Some((name, value)) = line.trim_end().split_once(':') else {
            break;
        };
        headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_string());
    }
    Ok(Some(Request { method, path, query, headers }))
}

/// The bytes of an embedded font, or `None` when the name is unknown or the
/// file was absent at build time.
fn font(name: &str) -> Option<&'static [u8]> {
    let found = crate::FONTS.iter().find(|(n, bytes)| *n == name && !bytes.is_empty());
    found.map(|(_, bytes)| *bytes)
}

/// Equality in time independent of where the strings first differ.
fn same_token(a: &str, b: &str) -> bool {
    let diff = a.bytes().zip(b.bytes()).fold(0, |acc, (x, y)| acc | (x ^ y));
    a.len() == b.len() && diff == 0
}

async fn handle(stream: TcpStream, shared: Arc<Shared>) -> std::io::Result<()> {
    let (rd, mut wr) = stream.into_split();
    let mut rd = BufReader::new(rd.take(MAX_HEADER_BYTES));
    let Some(req) = read_request(&mut rd).await? else {
        return respond(&mut wr, "400 Bad Request", TEXT, b"bad request").await;
    };
    if req.headers.get("origin").is_some_and(|o| *o != shared.origin) {
        return respond(&mut wr, "403 Forbidden", TEXT, b"origin not allowed").await;
    }
    // The page and the event stream are the two requests a browser cannot
    // attach a header to, so those two also accept the token as a query.
    let query_ok = req.path == "/" || req.path == "/events";
    let from_query = req.query.get("token").filter(|_| query_ok);
    let presented = req.headers.get("x-foe-token").or(from_query);
    if !presented.is_some_and(|t| same_token(t, &shared.token)) {
        return respond(&mut wr, "401 Unauthorized", TEXT, b"missing or wrong token").await;
    }
    if req.method != "GET" {
        return respond(&mut wr, "405 Method Not Allowed", TEXT, b"method not allowed").await;
    }
    let font = req.path.strip_prefix("/fonts/").and_then(font);
    let (content_type, body) = match (req.path.as_str(), font) {
        ("/", _) => {
            let boot = format!("{{\"mode\":\"live\",\"base\":\"\",\"token\":{:?}}}", shared.token);
            ("text/html; charset=utf-8", crate::page(&boot).into_bytes())
        }
        ("/episodes", _) => {
            let tree = serde_json::to_string(&shared.store().tree()).expect("tree serializes");
            ("application/json", tree.into_bytes())
        }
        ("/events", _) => {
            let id = req.query.get("episode").cloned().unwrap_or_default();
            let last = req.headers.get("last-event-id").and_then(|v| v.parse().ok());
            return stream_events(&mut wr, shared, &id, last).await;
        }
        (_, Some(bytes)) => (FONT, bytes.to_vec()),
        _ => return respond(&mut wr, "404 Not Found", TEXT, b"not found").await,
    };
    respond(&mut wr, "200 OK", content_type, &body).await
}

const TEXT: &str = "text/plain";
const FONT: &str = "font/woff2";

/// Writes one complete response and closes the connection. Fonts never
/// change, so they alone are marked cacheable; everything else is a view of
/// logs that grow.
async fn respond(wr: &mut OwnedWriteHalf, status: &str, content_type: &str, body: &[u8]) -> std::io::Result<()> {
    let cache = if content_type == FONT { "public, max-age=31536000, immutable" } else { "no-store" };
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\n\
         Cache-Control: {cache}\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n\
         Connection: close\r\n\r\n",
        body.len()
    );
    wr.write_all(head.as_bytes()).await?;
    wr.write_all(body).await?;
    wr.shutdown().await
}

/// Sends every line of episode `id` after `last`, then each new line as the
/// tailing task finds it, until the client closes the connection. A comment
/// line every [`KEEP_ALIVE`] surfaces a closed connection as a write error.
async fn stream_events(
    wr: &mut OwnedWriteHalf,
    shared: Arc<Shared>,
    id: &str,
    mut last: Option<u64>,
) -> std::io::Result<()> {
    wr.write_all(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n\
          X-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n",
    )
    .await?;
    let mut changed = shared.changed.clone();
    loop {
        // Mark the generation seen before reading, so a change that lands
        // between the read and the wait is not missed.
        changed.borrow_and_update();
        let lines = shared.store().lines(id, last);
        for line in lines {
            let seq = last.map_or(0, |s| s + 1);
            wr.write_all(format!("id: {seq}\ndata: {line}\n\n").as_bytes()).await?;
            last = Some(seq);
        }
        match tokio::time::timeout(KEEP_ALIVE, changed.changed()).await {
            Err(_) => wr.write_all(b": keep-alive\n\n").await?,
            Ok(Err(_)) => return Ok(()),
            Ok(Ok(())) => {}
        }
    }
}
