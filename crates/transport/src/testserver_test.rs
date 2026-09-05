//! A scripted HTTP server on the loopback interface for tests.
//!
//! The server answers each connection with the next reply from its script,
//! in order, and records every request it receives. It speaks just enough
//! HTTP/1.1 to exercise the client in `http`: it reads a request head and a
//! `Content-Length` body, then writes a response framed with
//! `Content-Length` or `Transfer-Encoding: chunked`, or closes the socket
//! to simulate a failure.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::http::Url;

/// One scripted response.
#[derive(Debug, Clone)]
pub struct Reply {
    status: u16,
    headers: Vec<(String, String)>,
    body: ReplyBody,
}

#[derive(Debug, Clone)]
enum ReplyBody {
    /// Sent with `Content-Length`.
    Full(String),
    /// Each element is one chunk; the terminating zero chunk follows.
    Chunked(Vec<String>),
    /// Each element is one chunk; the socket closes with no zero chunk.
    ChunkedThenClose(Vec<String>),
    /// The socket closes after the request is read, before any byte of a
    /// response.
    CloseImmediately,
}

impl Reply {
    pub fn full(status: u16, body: &str) -> Reply {
        Reply { status, headers: Vec::new(), body: ReplyBody::Full(body.to_string()) }
    }

    pub fn chunked(status: u16, pieces: Vec<&str>) -> Reply {
        Reply { status, headers: Vec::new(), body: ReplyBody::Chunked(pieces.iter().map(|s| s.to_string()).collect()) }
    }

    pub fn chunked_then_close(status: u16, pieces: Vec<&str>) -> Reply {
        Reply {
            status,
            headers: Vec::new(),
            body: ReplyBody::ChunkedThenClose(pieces.iter().map(|s| s.to_string()).collect()),
        }
    }

    pub fn close_immediately() -> Reply {
        Reply { status: 0, headers: Vec::new(), body: ReplyBody::CloseImmediately }
    }

    /// A complete event stream, one chunk per event, as providers send it.
    pub fn sse(transcript: &str) -> Reply {
        Reply::chunked(200, events(transcript)).with_header("content-type", "text/event-stream")
    }

    /// The first `count` events of a stream, after which the connection
    /// drops without the chunked terminator.
    pub fn sse_cut_after(transcript: &str, count: usize) -> Reply {
        let pieces = events(transcript).into_iter().take(count).collect();
        Reply::chunked_then_close(200, pieces).with_header("content-type", "text/event-stream")
    }

    pub fn with_header(mut self, name: &str, value: &str) -> Reply {
        self.headers.push((name.to_string(), value.to_string()));
        self
    }
}

/// Splits a transcript at blank lines and re-attaches the event separator.
fn events(transcript: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let bytes = transcript.as_bytes();
    let mut i = 0;
    while i + 1 < bytes.len() {
        if bytes[i] == b'\n' && bytes[i + 1] == b'\n' {
            out.push(&transcript[start..i + 2]);
            start = i + 2;
            i += 2;
        } else {
            i += 1;
        }
    }
    if start < transcript.len() {
        out.push(&transcript[start..]);
    }
    out
}

/// One request as the server saw it.
#[derive(Debug, Clone)]
pub struct Recorded {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl Recorded {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::from_str(&self.body).expect("request body is JSON")
    }
}

pub struct Server {
    addr: SocketAddr,
    requests: Arc<Mutex<Vec<Recorded>>>,
}

impl Server {
    /// Binds an ephemeral port and serves `replies`, one per connection.
    pub fn start(replies: Vec<Reply>) -> Server {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback");
        let addr = listener.local_addr().expect("local addr");
        let requests = Arc::new(Mutex::new(Vec::new()));
        let recorded = requests.clone();
        thread::spawn(move || {
            for reply in replies {
                let Ok((stream, _)) = listener.accept() else {
                    return;
                };
                serve_one(stream, reply, &recorded);
            }
        });
        Server { addr, requests }
    }

    pub fn base(&self) -> String {
        format!("http://{}", self.addr)
    }

    pub fn url(&self, path: &str) -> Url {
        Url::parse(&format!("{}{}", self.base(), path)).expect("loopback url")
    }

    pub fn requests(&self) -> Vec<Recorded> {
        self.requests.lock().expect("requests lock").clone()
    }
}

fn serve_one(mut stream: TcpStream, reply: Reply, recorded: &Mutex<Vec<Recorded>>) {
    stream.set_read_timeout(Some(Duration::from_secs(5))).expect("read timeout");
    let mut reader = BufReader::new(stream.try_clone().expect("clone socket"));
    let mut line = String::new();
    reader.read_line(&mut line).expect("request line");
    let mut parts = line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let path = parts.next().unwrap_or("").to_string();
    let mut headers = Vec::new();
    loop {
        line.clear();
        reader.read_line(&mut line).expect("header line");
        let text = line.trim_end_matches(['\r', '\n']);
        if text.is_empty() {
            break;
        }
        let (name, value) = text.split_once(':').expect("header has a colon");
        headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
    }
    let length: usize =
        headers.iter().find(|(k, _)| k == "content-length").and_then(|(_, v)| v.parse().ok()).unwrap_or(0);
    let mut body = vec![0; length];
    reader.read_exact(&mut body).expect("request body");
    recorded.lock().expect("requests lock").push(Recorded {
        method,
        path,
        headers,
        body: String::from_utf8(body).expect("utf-8 body"),
    });

    let mut head = format!("HTTP/1.1 {} Scripted\r\n", reply.status);
    for (name, value) in &reply.headers {
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    match reply.body {
        ReplyBody::CloseImmediately => {}
        ReplyBody::Full(body) => {
            head.push_str(&format!("content-length: {}\r\nconnection: close\r\n\r\n", body.len()));
            let _ = stream.write_all(head.as_bytes()).and_then(|_| stream.write_all(body.as_bytes()));
        }
        ReplyBody::Chunked(pieces) => {
            head.push_str("transfer-encoding: chunked\r\nconnection: close\r\n\r\n");
            let _ = stream.write_all(head.as_bytes());
            write_chunks(&mut stream, &pieces);
            let _ = stream.write_all(b"0\r\n\r\n");
        }
        ReplyBody::ChunkedThenClose(pieces) => {
            head.push_str("transfer-encoding: chunked\r\nconnection: close\r\n\r\n");
            let _ = stream.write_all(head.as_bytes());
            write_chunks(&mut stream, &pieces);
        }
    }
    let _ = stream.flush();
    let _ = stream.shutdown(std::net::Shutdown::Both);
}

fn write_chunks(stream: &mut TcpStream, pieces: &[String]) {
    for piece in pieces {
        let _ = stream.write_all(format!("{:x}\r\n", piece.len()).as_bytes());
        let _ = stream.write_all(piece.as_bytes());
        let _ = stream.write_all(b"\r\n");
        let _ = stream.flush();
    }
}

/// Accepts a complete request while retaining the socket for cancellation assertions.
pub async fn accept_request(listener: &tokio::net::TcpListener) -> tokio::net::TcpStream {
    use tokio::io::AsyncReadExt;
    let (mut stream, _) = listener.accept().await.unwrap();
    let mut head = Vec::new();
    while !head.ends_with(b"\r\n\r\n") {
        head.push(stream.read_u8().await.unwrap());
        assert!(head.len() < 16 * 1024, "fixture request head exceeds 16 KiB");
    }
    let head = String::from_utf8(head).unwrap();
    let length = head
        .lines()
        .find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case("content-length").then(|| value.trim().parse::<usize>().unwrap())
        })
        .unwrap_or(0);
    stream.read_exact(&mut vec![0; length]).await.unwrap();
    stream
}
