//! Cancellable HTTP/1.1 connections with bounded response buffering.
//!
//! Each response owns its connection task. Dropping the response or the
//! pending request aborts that task and closes the connection. Requests use
//! explicit headers and compiled trust roots. There is no proxy discovery,
//! redirect handling, compression, or ambient certificate lookup.

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper_util::rt::TokioIo;
use std::fmt;
use std::io;
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use tokio::io::{AsyncBufRead, AsyncRead, AsyncWrite};
use tokio::net::TcpStream;
use tokio_stream::StreamExt;

pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(30);
pub const READ_TIMEOUT: Duration = Duration::from_secs(600);
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

/// A response and the task that owns its socket.
pub struct Response {
    pub status: u16,
    headers: hyper::HeaderMap,
    pub body: Box<dyn AsyncBufRead + Send + Unpin>,
    _connection: Connection,
}

impl Response {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.get(name).and_then(|v| v.to_str().ok())
    }
}

struct Connection(tokio::task::JoinHandle<Result<(), hyper::Error>>);
impl Drop for Connection {
    fn drop(&mut self) {
        self.0.abort();
    }
}

pub async fn post(url: &Url, headers: &[(&str, &str)], body: &[u8]) -> Result<Response, HttpError> {
    request("POST", url, headers, body).await
}

pub async fn request(method: &str, url: &Url, headers: &[(&str, &str)], body: &[u8]) -> Result<Response, HttpError> {
    let stream =
        tokio::time::timeout(CONNECT_TIMEOUT, connect(url)).await.map_err(|_| HttpError::Connect(timeout()))??;
    let mut request = hyper::Request::builder()
        .method(method)
        .uri(&url.path)
        .header("host", url.host_header())
        .header("connection", "close")
        .header("user-agent", concat!("foe/", env!("CARGO_PKG_VERSION")));
    for (key, value) in [("accept", "text/event-stream"), ("content-type", "application/json")] {
        if !headers.iter().any(|(k, _)| k.eq_ignore_ascii_case(key)) {
            request = request.header(key, value);
        }
    }
    for (key, value) in headers {
        request = request.header(*key, *value);
    }
    let body = if method == "GET" { Bytes::new() } else { Bytes::copy_from_slice(body) };
    let request =
        request.body(Full::new(body)).map_err(|e| HttpError::Malformed(format!("request headers or URL: {e}")))?;
    let (mut sender, connection) = hyper::client::conn::http1::Builder::new()
        .max_buf_size(MAX_HEAD_BYTES)
        .handshake(TokioIo::new(stream))
        .await
        .map_err(http_error)?;
    let connection = Connection(tokio::spawn(connection));
    let response = tokio::time::timeout(READ_TIMEOUT, sender.send_request(request))
        .await
        .map_err(|_| HttpError::Io(timeout()))?
        .map_err(http_error)?;
    let (parts, body) = response.into_parts();
    let stream = body.into_data_stream().timeout(READ_TIMEOUT).map(|frame| match frame {
        Ok(frame) => frame.map_err(body_error),
        Err(_) => Err(timeout()),
    });
    let body = Box::new(tokio_util::io::StreamReader::new(Box::pin(stream)));
    Ok(Response { status: parts.status.as_u16(), headers: parts.headers, body, _connection: connection })
}

trait Socket: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Socket for T {}

async fn connect(url: &Url) -> Result<Box<dyn Socket>, HttpError> {
    let tcp = TcpStream::connect((url.host.as_str(), url.port)).await.map_err(HttpError::Connect)?;
    tcp.set_nodelay(true).map_err(HttpError::Connect)?;
    if !url.tls {
        return Ok(Box::new(tcp));
    }
    let name = rustls::pki_types::ServerName::try_from(url.host.clone()).map_err(|e| HttpError::Tls(e.to_string()))?;
    let tls = tokio_rustls::TlsConnector::from(tls_config()).connect(name, tcp).await.map_err(|e| {
        if e.get_ref().is_some_and(|inner| inner.is::<rustls::Error>()) {
            HttpError::Tls(e.to_string())
        } else {
            HttpError::Io(e)
        }
    })?;
    Ok(Box::new(tls))
}

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

fn timeout() -> io::Error {
    io::Error::new(io::ErrorKind::TimedOut, "HTTP connection exceeded its time limit")
}

fn body_error(error: hyper::Error) -> io::Error {
    use std::error::Error;
    let mut source = error.source();
    while let Some(cause) = source {
        if let Some(cause) = cause.downcast_ref::<io::Error>() {
            return match cause.kind() {
                io::ErrorKind::InvalidInput | io::ErrorKind::InvalidData => {
                    io::Error::new(io::ErrorKind::InvalidData, cause.to_string())
                }
                io::ErrorKind::UnexpectedEof => {
                    io::Error::new(io::ErrorKind::UnexpectedEof, "connection closed before the body was complete")
                }
                kind => io::Error::new(kind, cause.to_string()),
            };
        }
        source = cause.source();
    }
    io::Error::other(error)
}

fn http_error(error: hyper::Error) -> HttpError {
    if error.is_parse() {
        HttpError::Malformed(error.to_string())
    } else {
        HttpError::Io(body_error(error))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testserver::{Reply, Server};
    use tokio::io::AsyncReadExt;

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

    #[tokio::test]
    async fn chunked_body_is_reassembled_across_chunks_and_reads() {
        let server =
            Server::start(vec![Reply::chunked(200, vec!["hel", "lo\r\n", "wor", "ld"]).with_header("x-test", "1")]);
        let mut resp = post(&server.url("/p"), &[("x-custom", "v")], b"{}").await.unwrap();
        assert_eq!(resp.status, 200);
        assert_eq!(resp.header("X-Test"), Some("1"));
        let mut text = String::new();
        resp.body.read_to_string(&mut text).await.unwrap();
        assert_eq!(text, "hello\r\nworld");
        let seen = server.requests();
        assert_eq!(seen[0].path, "/p");
        assert_eq!(seen[0].body, "{}");
        assert_eq!(seen[0].header("x-custom"), Some("v"));
        assert_eq!(seen[0].header("content-type"), Some("application/json"));
    }

    #[tokio::test]
    async fn get_sends_no_body_and_form_post_keeps_its_content_type() {
        let server = Server::start(vec![Reply::full(200, "{}"), Reply::full(200, "{}")]);
        let mut resp =
            request("GET", &server.url("/v1/models"), &[("authorization", "Bearer k")], b"ignored").await.unwrap();
        assert_eq!(resp.status, 200);
        let mut text = String::new();
        resp.body.read_to_string(&mut text).await.unwrap();
        let form = [("content-type", "application/x-www-form-urlencoded")];
        request("POST", &server.url("/token"), &form, b"a=1&b=2").await.unwrap();
        let seen = server.requests();
        assert_eq!((seen[0].method.as_str(), seen[0].path.as_str(), seen[0].body.as_str()), ("GET", "/v1/models", ""));
        assert_eq!(seen[0].header("content-length"), None);
        assert_eq!(seen[0].header("authorization"), Some("Bearer k"));
        assert_eq!(seen[1].header("content-type"), Some("application/x-www-form-urlencoded"));
        assert_eq!(seen[1].body, "a=1&b=2");
    }

    #[tokio::test]
    async fn content_length_body_stops_at_length() {
        let server = Server::start(vec![Reply::full(404, "nope")]);
        let mut resp = post(&server.url("/"), &[], b"").await.unwrap();
        assert_eq!(resp.status, 404);
        let mut text = String::new();
        resp.body.read_to_string(&mut text).await.unwrap();
        assert_eq!(text, "nope");
    }

    #[tokio::test]
    async fn truncated_chunked_body_is_unexpected_eof() {
        let server = Server::start(vec![Reply::chunked_then_close(200, vec!["partial"])]);
        let mut resp = post(&server.url("/"), &[], b"").await.unwrap();
        let mut prefix = [0; 7];
        resp.body.read_exact(&mut prefix).await.unwrap();
        assert_eq!(&prefix, b"partial");
        let err = resp.body.read_to_end(&mut Vec::new()).await.unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::UnexpectedEof);
    }

    #[tokio::test]
    async fn closed_before_head_is_retryable_io() {
        let server = Server::start(vec![Reply::close_immediately()]);
        let err = post(&server.url("/"), &[], b"").await.err().expect("a closed socket fails");
        assert!(matches!(err, HttpError::Io(_)), "{err}");
        assert!(err.retryable());
    }

    #[tokio::test]
    async fn refused_connection_is_retryable_connect_error() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let url = Url::parse(&format!("http://127.0.0.1:{port}")).unwrap();
        let err = post(&url, &[], b"").await.err().expect("a refused connection fails");
        assert!(matches!(err, HttpError::Connect(_)), "{err}");
        assert!(err.retryable());
    }

    #[tokio::test]
    async fn cancellation_closes_the_socket_before_and_after_response_headers() {
        // docs/models.md: cancellation closes model and credential HTTP connections.
        use tokio::io::AsyncWriteExt;
        for send_headers in [false, true] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let url = Url::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
            let mut pending = Box::pin(post(&url, &[], b""));
            let mut peer = tokio::select! {
                response = &mut pending => panic!("request finished before the server replied: {:?}", response.err()),
                stream = crate::testserver::accept_request(&listener) => stream,
            };
            if send_headers {
                peer.write_all(b"HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n").await.unwrap();
                let response = pending.await.unwrap();
                drop(response);
            } else {
                drop(pending);
            }
            assert_eq!(peer.read(&mut [0; 1]).await.unwrap(), 0);
        }
    }

    #[tokio::test]
    async fn oversized_headers_and_chunk_framing_are_nonretryable() {
        // docs/models.md: response heads and chunk framing have finite limits.
        use tokio::io::AsyncWriteExt;
        let oversized = "x".repeat(128 * 1024);
        for (wire, body_failure) in [
            (format!("HTTP/1.1 200 OK\r\nx-large: {oversized}"), false),
            (format!("HTTP/1.1 200 OK\r\n{}\r\n", "x-item: a\r\n".repeat(101)), false),
            (format!("HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n1;{oversized}"), true),
            (format!("HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n0\r\nx-large: {oversized}"), true),
            ("HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\nwrong\r\n".into(), true),
        ] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let url = Url::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
            let server = tokio::spawn(async move {
                let mut peer = crate::testserver::accept_request(&listener).await;
                let _ = peer.write_all(wire.as_bytes()).await;
                let _ = peer.read(&mut [0; 1]).await;
            });
            match post(&url, &[], b"").await {
                Ok(mut response) => {
                    assert!(body_failure);
                    let error = response.body.read_to_end(&mut Vec::new()).await.unwrap_err();
                    assert_eq!(error.kind(), io::ErrorKind::InvalidData, "{error}");
                }
                Err(error) => {
                    assert!(!body_failure, "{error}");
                    assert!(!error.retryable(), "{error}");
                }
            }
            server.await.unwrap();
        }
    }

    #[tokio::test(start_paused = true)]
    async fn response_body_idle_limit_uses_elapsed_time() {
        // docs/models.md: a stalled response body fails after 600 seconds.
        use tokio::io::AsyncWriteExt;
        // Socket setup uses the reactor before the elapsed-time assertion starts.
        tokio::time::resume();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = Url::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let mut pending = Box::pin(post(&url, &[], b""));
        let mut peer = tokio::select! {
            response = &mut pending => panic!("request finished before the server replied: {:?}", response.err()),
            stream = crate::testserver::accept_request(&listener) => stream,
        };
        peer.write_all(b"HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n").await.unwrap();
        let mut response = pending.await.unwrap();
        tokio::time::pause();
        let start = tokio::time::Instant::now();
        let error = response.body.read(&mut [0; 1]).await.unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        let elapsed = tokio::time::Instant::now() - start;
        assert!((READ_TIMEOUT - Duration::from_millis(1)..=READ_TIMEOUT + Duration::from_millis(1)).contains(&elapsed));
    }
}
