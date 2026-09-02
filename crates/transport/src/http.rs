//! A minimal HTTP/1.1 client: one request per connection, with the response
//! body read as a stream.
//!
//! The clients in this crate need two HTTP shapes: POST a document, then
//! read a server-sent-event body line by line until the server finishes or
//! the connection drops; and a short GET or form POST against a token or
//! model-listing endpoint. A general-purpose client would bring proxy
//! discovery from the environment, redirects, compression, and connection
//! pools, none of which this crate wants. This module sends one request per
//! TCP connection with `Connection: close`, decodes `Transfer-Encoding:
//! chunked` and `Content-Length` framing, and nothing else.
//!
//! Invariants:
//! - No environment variable is read. There is no proxy support.
//! - TLS trusts Mozilla's root certificates compiled in through
//!   `webpki-roots`. The system certificate store is never opened.
//! - Every read on the socket times out after [`READ_TIMEOUT`] of silence,
//!   so a dead connection surfaces as an error rather than a hang.

use std::fmt;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

/// Longest wait for a TCP connection to be established.
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(30);

/// Longest silence tolerated between bytes of a response. Defined at the
/// crate root, because the `exec` transport honours the same limit and
/// builds without this module.
pub(crate) use crate::READ_TIMEOUT;

/// Largest response head (status line plus headers) accepted.
const MAX_HEAD_BYTES: usize = 64 * 1024;

/// A parsed `http://` or `https://` URL with only the parts this client uses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Url {
    pub tls: bool,
    pub host: String,
    pub port: u16,
    /// Always starts with `/`. The query string, when present, is kept here.
    pub path: String,
}

impl Url {
    /// Parses `scheme://host[:port][/path]`. The scheme must be `http` or
    /// `https`. IPv6 hosts are written in brackets, as in `http://[::1]:8080`.
    pub fn parse(text: &str) -> Result<Url, String> {
        let (tls, rest) = if let Some(rest) = text.strip_prefix("https://") {
            (true, rest)
        } else if let Some(rest) = text.strip_prefix("http://") {
            (false, rest)
        } else {
            return Err("scheme must be http or https".into());
        };
        let (authority, path) = match rest.find('/') {
            Some(i) => (&rest[..i], &rest[i..]),
            None => (rest, "/"),
        };
        if authority.contains('@') {
            return Err("credentials in the URL are not accepted".into());
        }
        let (host, port) = if let Some(rest) = authority.strip_prefix('[') {
            let end = rest.find(']').ok_or("unterminated IPv6 host")?;
            let host = &rest[..end];
            let port = match rest[end + 1..].strip_prefix(':') {
                Some(p) => Some(p),
                None if rest[end + 1..].is_empty() => None,
                None => return Err("unexpected text after IPv6 host".into()),
            };
            (host.to_string(), port)
        } else {
            match authority.rsplit_once(':') {
                Some((h, p)) => (h.to_string(), Some(p)),
                None => (authority.to_string(), None),
            }
        };
        if host.is_empty() {
            return Err("host is empty".into());
        }
        let port = match port {
            Some(p) => p.parse::<u16>().map_err(|_| format!("port {p:?} is not a number"))?,
            None if tls => 443,
            None => 80,
        };
        Ok(Url { tls, host, port, path: path.to_string() })
    }

    /// Appends `suffix`, which starts with `/`, to the path. A trailing `/`
    /// on the existing path is dropped first so that `http://h/v1/` and
    /// `http://h/v1` join identically.
    pub fn join(&self, suffix: &str) -> Url {
        let mut path = self.path.trim_end_matches('/').to_string();
        path.push_str(suffix);
        Url { path, ..self.clone() }
    }

    /// The `Host` header value: the port is included only when it differs
    /// from the scheme's default.
    fn host_header(&self) -> String {
        let default = if self.tls { 443 } else { 80 };
        let host = if self.host.contains(':') { format!("[{}]", self.host) } else { self.host.clone() };
        if self.port == default {
            host
        } else {
            format!("{host}:{}", self.port)
        }
    }
}

/// Why a request produced no usable response.
#[derive(Debug)]
pub enum HttpError {
    /// The TCP connection could not be established.
    Connect(io::Error),
    /// The TLS handshake failed, including certificate verification.
    Tls(String),
    /// The connection failed after it was established.
    Io(io::Error),
    /// The server's response head was not HTTP/1.x.
    Malformed(String),
}

impl HttpError {
    /// Whether a later attempt could plausibly succeed. A refused connection
    /// or a reset is transient; a certificate that does not verify or a
    /// server that does not speak HTTP is not.
    pub fn retryable(&self) -> bool {
        matches!(self, HttpError::Connect(_) | HttpError::Io(_))
    }
}

impl fmt::Display for HttpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            HttpError::Connect(e) => write!(f, "connect: {e}"),
            HttpError::Tls(e) => write!(f, "tls: {e}"),
            HttpError::Io(e) => write!(f, "io: {e}"),
            HttpError::Malformed(e) => write!(f, "malformed response: {e}"),
        }
    }
}

/// A response whose head has been read and whose body is still on the wire.
pub struct Response {
    pub status: u16,
    headers: Vec<(String, String)>,
    pub body: BufReader<Body>,
}

impl Response {
    /// The first header with this name, compared case-insensitively.
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }
}

/// Sends one POST of a JSON body and returns once the response head has
/// arrived. `headers` are sent verbatim after the fixed headers this client
/// always sends.
pub fn post(url: &Url, headers: &[(&str, &str)], body: &[u8]) -> Result<Response, HttpError> {
    request("POST", url, headers, body)
}

/// Sends one request and returns once the response head has arrived. The
/// body is sent as JSON unless `headers` carries its own `content-type`; a
/// GET sends no body. `accept` defaults to `text/event-stream`, which every
/// endpoint this crate talks to also answers with plain JSON.
pub fn request(method: &str, url: &Url, headers: &[(&str, &str)], body: &[u8]) -> Result<Response, HttpError> {
    let mut stream = connect(url)?;
    let given = |name: &str| headers.iter().any(|(k, _)| k.eq_ignore_ascii_case(name));
    let mut head = format!(
        "{method} {} HTTP/1.1\r\nhost: {}\r\nconnection: close\r\nuser-agent: foe/{}\r\n",
        url.path,
        url.host_header(),
        env!("CARGO_PKG_VERSION"),
    );
    if method != "GET" {
        if !given("content-type") {
            head.push_str("content-type: application/json\r\n");
        }
        head.push_str(&format!("content-length: {}\r\n", body.len()));
    }
    if !given("accept") {
        head.push_str("accept: text/event-stream\r\n");
    }
    for (name, value) in headers {
        head.push_str(name);
        head.push_str(": ");
        head.push_str(value);
        head.push_str("\r\n");
    }
    head.push_str("\r\n");
    let body = if method == "GET" { &[][..] } else { body };
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(body))
        .and_then(|_| stream.flush())
        .map_err(wrap_io)?;

    let mut reader = BufReader::new(stream);
    let status_line = read_head_line(&mut reader)?;
    let status = parse_status(&status_line)?;
    let mut headers = Vec::new();
    let mut total = status_line.len();
    loop {
        let line = read_head_line(&mut reader)?;
        total += line.len();
        if total > MAX_HEAD_BYTES {
            return Err(HttpError::Malformed(format!("response head exceeds {MAX_HEAD_BYTES} bytes")));
        }
        if line.is_empty() {
            break;
        }
        let (name, value) =
            line.split_once(':').ok_or_else(|| HttpError::Malformed(format!("header line {line:?}")))?;
        headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
    }
    let framing = framing(&headers)?;
    Ok(Response { status, headers, body: BufReader::new(Body { inner: reader, framing }) })
}

fn connect(url: &Url) -> Result<Stream, HttpError> {
    let addrs = (url.host.as_str(), url.port).to_socket_addrs().map_err(HttpError::Connect)?;
    let mut last = io::Error::new(io::ErrorKind::NotFound, "host resolved to no address");
    let mut tcp = None;
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
            Ok(s) => {
                tcp = Some(s);
                break;
            }
            Err(e) => last = e,
        }
    }
    let tcp = tcp.ok_or(HttpError::Connect(last))?;
    tcp.set_nodelay(true).map_err(HttpError::Connect)?;
    tcp.set_read_timeout(Some(READ_TIMEOUT)).map_err(HttpError::Connect)?;
    tcp.set_write_timeout(Some(READ_TIMEOUT)).map_err(HttpError::Connect)?;
    if !url.tls {
        return Ok(Stream::Plain(tcp));
    }
    let name = rustls::pki_types::ServerName::try_from(url.host.clone()).map_err(|e| HttpError::Tls(e.to_string()))?;
    let conn = rustls::ClientConnection::new(tls_config(), name).map_err(|e| HttpError::Tls(e.to_string()))?;
    Ok(Stream::Tls(Box::new(rustls::StreamOwned::new(conn, tcp))))
}

/// One client configuration shared by every connection. Built on first use
/// from the compiled-in Mozilla roots.
fn tls_config() -> Arc<rustls::ClientConfig> {
    static CONFIG: OnceLock<Arc<rustls::ClientConfig>> = OnceLock::new();
    CONFIG
        .get_or_init(|| {
            let mut roots = rustls::RootCertStore::empty();
            roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
            Arc::new(rustls::ClientConfig::builder().with_root_certificates(roots).with_no_client_auth())
        })
        .clone()
}

/// Distinguishes a TLS failure surfaced through `io::Error` from a plain
/// socket failure, so that a certificate problem is not retried.
fn wrap_io(e: io::Error) -> HttpError {
    let is_tls = e.get_ref().is_some_and(|inner| inner.is::<rustls::Error>());
    if is_tls {
        HttpError::Tls(e.to_string())
    } else {
        HttpError::Io(e)
    }
}

fn read_head_line(reader: &mut BufReader<Stream>) -> Result<String, HttpError> {
    let mut line = String::new();
    let n = reader.read_line(&mut line).map_err(wrap_io)?;
    if n == 0 {
        return Err(HttpError::Io(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "connection closed before the response head",
        )));
    }
    while line.ends_with('\n') || line.ends_with('\r') {
        line.pop();
    }
    Ok(line)
}

fn parse_status(line: &str) -> Result<u16, HttpError> {
    let mut parts = line.splitn(3, ' ');
    let version = parts.next().unwrap_or("");
    if !version.starts_with("HTTP/1.") {
        return Err(HttpError::Malformed(format!("status line {line:?}")));
    }
    parts.next().and_then(|s| s.parse().ok()).ok_or_else(|| HttpError::Malformed(format!("status line {line:?}")))
}

fn framing(headers: &[(String, String)]) -> Result<Framing, HttpError> {
    let find = |name: &str| headers.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str());
    if find("transfer-encoding").is_some_and(|v| v.to_ascii_lowercase().contains("chunked")) {
        return Ok(Framing::Chunked { remaining: 0, done: false });
    }
    match find("content-length") {
        Some(v) => {
            v.trim().parse().map(Framing::Length).map_err(|_| HttpError::Malformed(format!("content-length {v:?}")))
        }
        None => Ok(Framing::UntilClose),
    }
}

enum Stream {
    Plain(TcpStream),
    Tls(Box<rustls::StreamOwned<rustls::ClientConnection, TcpStream>>),
}

impl Read for Stream {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            Stream::Plain(s) => s.read(buf),
            Stream::Tls(s) => s.read(buf),
        }
    }
}

impl Write for Stream {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        match self {
            Stream::Plain(s) => s.write(buf),
            Stream::Tls(s) => s.write(buf),
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        match self {
            Stream::Plain(s) => s.flush(),
            Stream::Tls(s) => s.flush(),
        }
    }
}

/// How the server delimits the body. See RFC 9112 section 6.
enum Framing {
    /// `Transfer-Encoding: chunked`. `remaining` counts bytes left in the
    /// current chunk; `done` is set once the zero-length chunk and its
    /// trailers have been consumed.
    Chunked { remaining: u64, done: bool },
    /// `Content-Length`: bytes still to read.
    Length(u64),
    /// Neither header: the body ends when the server closes the connection.
    UntilClose,
}

/// The response body with transfer framing removed. Yields `Ok(0)` at the
/// end of a complete body and `UnexpectedEof` when the connection closes
/// before the framing says the body is complete.
pub struct Body {
    inner: BufReader<Stream>,
    framing: Framing,
}

impl Read for Body {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if buf.is_empty() {
            return Ok(0);
        }
        match &mut self.framing {
            Framing::UntilClose => self.inner.read(buf),
            Framing::Length(remaining) => {
                if *remaining == 0 {
                    return Ok(0);
                }
                let want = buf.len().min(usize::try_from(*remaining).unwrap_or(usize::MAX));
                let got = self.inner.read(&mut buf[..want])?;
                if got == 0 {
                    return Err(truncated());
                }
                *remaining -= got as u64;
                Ok(got)
            }
            Framing::Chunked { remaining, done } => {
                if *done {
                    return Ok(0);
                }
                if *remaining == 0 {
                    let size_line = read_crlf_line(&mut self.inner)?;
                    let digits = size_line.split(';').next().unwrap_or("").trim();
                    let size = u64::from_str_radix(digits, 16)
                        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, format!("chunk size {size_line:?}")))?;
                    if size == 0 {
                        // Trailer fields, if any, end with an empty line.
                        while !read_crlf_line(&mut self.inner)?.is_empty() {}
                        *done = true;
                        return Ok(0);
                    }
                    *remaining = size;
                }
                let want = buf.len().min(usize::try_from(*remaining).unwrap_or(usize::MAX));
                let got = self.inner.read(&mut buf[..want])?;
                if got == 0 {
                    return Err(truncated());
                }
                *remaining -= got as u64;
                if *remaining == 0 {
                    // The CRLF that terminates every chunk's data.
                    read_crlf_line(&mut self.inner)?;
                }
                Ok(got)
            }
        }
    }
}

fn truncated() -> io::Error {
    io::Error::new(io::ErrorKind::UnexpectedEof, "connection closed before the body was complete")
}

fn read_crlf_line(reader: &mut BufReader<Stream>) -> io::Result<String> {
    let mut line = String::new();
    if reader.read_line(&mut line)? == 0 {
        return Err(truncated());
    }
    while line.ends_with('\n') || line.ends_with('\r') {
        line.pop();
    }
    Ok(line)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testserver::{Reply, Server};

    #[test]
    fn url_parse_defaults_port_and_path() {
        let u = Url::parse("https://api.anthropic.com").unwrap();
        assert_eq!(u, Url { tls: true, host: "api.anthropic.com".into(), port: 443, path: "/".into() });
        let u = Url::parse("http://localhost:11434/v1").unwrap();
        assert_eq!(u, Url { tls: false, host: "localhost".into(), port: 11434, path: "/v1".into() });
        let u = Url::parse("http://[::1]:8080/x?y=1").unwrap();
        assert_eq!(u, Url { tls: false, host: "::1".into(), port: 8080, path: "/x?y=1".into() });
    }

    #[test]
    fn url_parse_rejects_other_schemes_and_credentials() {
        assert!(Url::parse("ftp://x").is_err());
        assert!(Url::parse("api.anthropic.com").is_err());
        assert!(Url::parse("https://user:pw@host/").is_err());
        assert!(Url::parse("https://host:abc/").is_err());
        assert!(Url::parse("https:///path").is_err());
    }

    #[test]
    fn url_join_ignores_trailing_slash() {
        let a = Url::parse("http://h/v1/").unwrap().join("/chat/completions");
        let b = Url::parse("http://h/v1").unwrap().join("/chat/completions");
        assert_eq!(a.path, "/v1/chat/completions");
        assert_eq!(a, b);
        assert_eq!(Url::parse("http://h").unwrap().join("/v1/messages").path, "/v1/messages");
    }

    #[test]
    fn host_header_omits_default_port() {
        assert_eq!(Url::parse("https://h").unwrap().host_header(), "h");
        assert_eq!(Url::parse("https://h:8443").unwrap().host_header(), "h:8443");
        assert_eq!(Url::parse("http://h:80").unwrap().host_header(), "h");
        assert_eq!(Url::parse("http://[::1]:8080").unwrap().host_header(), "[::1]:8080");
    }

    #[test]
    fn chunked_body_is_reassembled_across_chunks_and_reads() {
        let server =
            Server::start(vec![Reply::chunked(200, vec!["hel", "lo\r\n", "wor", "ld"]).with_header("x-test", "1")]);
        let mut resp = post(&server.url("/p"), &[("x-custom", "v")], b"{}").unwrap();
        assert_eq!(resp.status, 200);
        assert_eq!(resp.header("X-Test"), Some("1"));
        let mut text = String::new();
        resp.body.read_to_string(&mut text).unwrap();
        assert_eq!(text, "hello\r\nworld");
        let seen = server.requests();
        assert_eq!(seen[0].path, "/p");
        assert_eq!(seen[0].body, "{}");
        assert_eq!(seen[0].header("x-custom"), Some("v"));
        assert_eq!(seen[0].header("content-type"), Some("application/json"));
    }

    #[test]
    fn get_sends_no_body_and_form_post_keeps_its_content_type() {
        let server = Server::start(vec![Reply::full(200, "{}"), Reply::full(200, "{}")]);
        let mut resp = request("GET", &server.url("/v1/models"), &[("authorization", "Bearer k")], b"ignored").unwrap();
        assert_eq!(resp.status, 200);
        let mut text = String::new();
        resp.body.read_to_string(&mut text).unwrap();
        let form = [("content-type", "application/x-www-form-urlencoded")];
        request("POST", &server.url("/token"), &form, b"a=1&b=2").unwrap();
        let seen = server.requests();
        assert_eq!((seen[0].method.as_str(), seen[0].path.as_str(), seen[0].body.as_str()), ("GET", "/v1/models", ""));
        assert_eq!(seen[0].header("content-length"), None);
        assert_eq!(seen[0].header("authorization"), Some("Bearer k"));
        assert_eq!(seen[1].header("content-type"), Some("application/x-www-form-urlencoded"));
        assert_eq!(seen[1].body, "a=1&b=2");
    }

    #[test]
    fn content_length_body_stops_at_length() {
        let server = Server::start(vec![Reply::full(404, "nope")]);
        let mut resp = post(&server.url("/"), &[], b"").unwrap();
        assert_eq!(resp.status, 404);
        let mut text = String::new();
        resp.body.read_to_string(&mut text).unwrap();
        assert_eq!(text, "nope");
    }

    #[test]
    fn truncated_chunked_body_is_unexpected_eof() {
        let server = Server::start(vec![Reply::chunked_then_close(200, vec!["partial"])]);
        let mut resp = post(&server.url("/"), &[], b"").unwrap();
        let mut text = String::new();
        let err = resp.body.read_to_string(&mut text).unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::UnexpectedEof);
        assert_eq!(text, "partial");
    }

    #[test]
    fn closed_before_head_is_retryable_io() {
        let server = Server::start(vec![Reply::close_immediately()]);
        let err = post(&server.url("/"), &[], b"").err().expect("a closed socket fails");
        assert!(matches!(err, HttpError::Io(_)), "{err}");
        assert!(err.retryable());
    }

    #[test]
    fn refused_connection_is_retryable_connect_error() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let url = Url::parse(&format!("http://127.0.0.1:{port}")).unwrap();
        let err = post(&url, &[], b"").err().expect("a refused connection fails");
        assert!(matches!(err, HttpError::Connect(_)), "{err}");
        assert!(err.retryable());
    }
}
