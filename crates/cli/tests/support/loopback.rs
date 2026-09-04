//! A scripted loopback endpoint for built-binary integration tests.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

#[derive(Clone)]
pub struct Reply {
    content_type: &'static str,
    body: String,
}

impl Reply {
    pub fn json(body: serde_json::Value) -> Self {
        Self { content_type: "application/json", body: body.to_string() }
    }

    pub fn event_stream(body: &str) -> Self {
        Self { content_type: "text/event-stream", body: body.to_string() }
    }
}

#[derive(Clone, Debug)]
pub struct Recorded {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl Recorded {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::from_str(&self.body).expect("request body is JSON")
    }
}

pub struct Server {
    address: SocketAddr,
    requests: Arc<Mutex<Vec<Recorded>>>,
}

impl Server {
    pub fn start(replies: Vec<Reply>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback endpoint");
        let address = listener.local_addr().expect("loopback endpoint address");
        let requests = Arc::new(Mutex::new(Vec::new()));
        let recorded = requests.clone();
        thread::spawn(move || {
            for reply in replies {
                let (stream, _) = listener.accept().expect("accept endpoint request");
                serve(stream, reply, &recorded);
            }
        });
        Self { address, requests }
    }

    pub fn origin(&self) -> String {
        format!("http://{}", self.address)
    }

    pub fn requests(&self) -> Vec<Recorded> {
        self.requests.lock().expect("request lock").clone()
    }
}

fn serve(mut stream: TcpStream, reply: Reply, requests: &Mutex<Vec<Recorded>>) {
    stream.set_read_timeout(Some(Duration::from_secs(5))).expect("request timeout");
    let mut reader = BufReader::new(stream.try_clone().expect("clone endpoint stream"));
    let mut line = String::new();
    reader.read_line(&mut line).expect("request line");
    let mut words = line.split_whitespace();
    let method = words.next().unwrap_or("").to_string();
    let path = words.next().unwrap_or("").to_string();
    let mut headers = Vec::new();
    loop {
        line.clear();
        reader.read_line(&mut line).expect("header line");
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            break;
        }
        let (name, value) = line.split_once(':').expect("header has a colon");
        headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
    }
    let length = headers
        .iter()
        .find(|(name, _)| name == "content-length")
        .and_then(|(_, value)| value.parse().ok())
        .unwrap_or(0);
    let mut body = vec![0; length];
    reader.read_exact(&mut body).expect("request body");
    requests.lock().expect("request lock").push(Recorded {
        method,
        path,
        headers,
        body: String::from_utf8(body).expect("request body is UTF-8"),
    });

    let head = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: {}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
        reply.content_type,
        reply.body.len()
    );
    stream.write_all(head.as_bytes()).expect("response head");
    stream.write_all(reply.body.as_bytes()).expect("response body");
    stream.flush().expect("flush response");
}
