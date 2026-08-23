//! The scrubber.
//!
//! The strongest protection here is that almost nothing is emitted: the
//! schema carries counts, durations, token usage, outcome terms, tool
//! names, hashes, and bucket labels, and exactly two short tool-generated
//! strings. This file cleans those two strings, and it refuses the whole
//! emission when it can still see something in its own output.
//!
//! Four layers run in order over each string. Known values first, because
//! they are the only values whose exact form is known and substituting them
//! whole keeps the surrounding text readable. Format detectors next, over
//! what remains. Path componentization third, so that a path no detector
//! recognized still loses everything but its shape. Every replacement is a
//! keyed pseudonym.

use crate::extract::KnownValue;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;

/// Format detectors: the finding name reported by the self-check, the
/// pseudonym type tag, and the pattern. Order matters — the first
/// alternative that matches at the leftmost position wins — so a specific
/// shape is listed before a general one that would swallow it.
///
/// Every group is non-capturing: the replacer identifies the detector that
/// fired by capture-group index, and a capturing group inside a pattern
/// would shift the indices of every detector after it.
const DETECTORS: &[(&str, char, &str)] = &[
    ("pem-header", 's', r"-----BEGIN [A-Z ]+-----"),
    ("ssh-key", 's', r"ssh-(?:rsa|ed25519|ecdsa|dss)[ \t]+[A-Za-z0-9+/=]{20,}"),
    ("cloud-key", 's', r"AKIA[0-9A-Z]{12,}"),
    ("forge-token", 's', r"gh[pousr]_[A-Za-z0-9]{16,}"),
    ("model-key", 's', r"sk-[A-Za-z0-9_-]{16,}"),
    ("chat-token", 's', r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    ("jwt", 's', r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ("email", 'e', r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("url", 'h', r#"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>"'\\]+"#),
    ("uuid", 's', r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    ("mac", 'h', r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b"),
    ("ipv6", 'h', r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"),
    ("ipv4", 'h', r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
    ("long-hex", 's', r"\b[0-9a-fA-F]{32,}\b"),
    ("high-entropy", 's', r"\b[A-Za-z0-9+/]{20,}={0,2}"),
];

/// Index of the entropy-gated detector in [`DETECTORS`]: the last one.
const HIGH_ENTROPY: usize = DETECTORS.len() - 1;

/// Shannon entropy, in bits per character, at or above which a base64-shaped
/// run is treated as key material. Random base64 of this length lands near
/// 4.1 bits; running text and identifiers repeat characters enough to stay
/// below. The run must also mix digits and upper case, which separates a
/// token from a long lower-case path that the pattern would otherwise reach.
const ENTROPY_BITS: f64 = 3.5;

/// Path components kept in the clear: the standard filesystem hierarchy,
/// and the names of system files and programs that are the same on every
/// machine. Masking `null` or `useradd` protects nobody, and it destroys
/// the one thing the subject was worth reading for — that the episode wrote
/// to the null device, or added a user. A component that could name a
/// person, a project, or a task is not on this list; when in doubt, mask.
const COMMON_DIRS: &str = concat!(
    "usr|bin|sbin|etc|var|tmp|opt|home|root|dev|proc|sys|run|mnt|srv|boot|log|share|local|include|",
    "src|lib|test|tests|docs|target|build|dist|node_modules|git|",
    "null|zero|bash|sh|useradd|adduser|systemctl|nginx|sshd|sshd_config|passwd|hosts|resolv.conf|crontab"
);

/// Whether a path component is one of those kept in the clear.
fn common(part: &str) -> bool {
    COMMON_DIRS.split('|').any(|dir| dir == part)
}

/// Anything with a slash in it, absolute or relative. Relative paths are
/// included because tool subjects report workspace-relative paths, and a
/// layer that only saw absolute ones would pass those through untouched.
const PATH_PATTERN: &str = r"[A-Za-z0-9._@+-]*(?:/[A-Za-z0-9._@+-]+)+/?";

/// File name of the local key inside the output directory.
pub const KEY_FILE: &str = "key";

/// How many replacements of each pseudonym type were made. The original
/// values are never recorded, here or anywhere else.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Report(pub BTreeMap<String, u32>);

impl Report {
    fn record(&mut self, tag: char) {
        *self.0.entry(tag_name(tag).to_string()).or_default() += 1;
    }
}

/// The name a pseudonym type tag is reported under.
const TAG_NAMES: &[(char, &str)] = &[('p', "path-component"), ('u', "user"), ('e', "email"), ('h', "host")];

fn tag_name(tag: char) -> &'static str {
    TAG_NAMES.iter().find(|(t, _)| *t == tag).map(|(_, name)| *name).unwrap_or("secret-shaped")
}

/// The 32-byte local key and the known values of one episode.
pub struct Scrubber {
    key: Vec<u8>,
    /// Each variant form of each known value, with the tag it counts under
    /// and the text that replaces it. Longest form first, so a longer path
    /// is substituted before a prefix of it.
    ///
    /// The replacement is the pseudonym of the value as recorded, never of
    /// the variant: a directory written raw, JSON-escaped, tilde-abbreviated
    /// or with a trailing slash is one directory, and it must carry one
    /// pseudonym or joins over it split four ways.
    variants: Vec<(char, String, String)>,
    detectors: regex::Regex,
    paths: regex::Regex,
}

impl Scrubber {
    pub fn new(key: Vec<u8>, known: &[KnownValue]) -> Scrubber {
        let mut variants: Vec<(char, String, String)> = Vec::new();
        for entry in known {
            let name = format!("⟨{}:{}⟩", entry.tag, digest(&key, &entry.value));
            let forms = variant_forms(&entry.value).into_iter();
            variants.extend(forms.map(|(form, suffix)| (entry.tag, form, format!("{name}{suffix}"))));
        }
        variants.sort_by(|a, b| b.1.len().cmp(&a.1.len()).then(a.1.cmp(&b.1)));
        variants.dedup_by(|a, b| a.1 == b.1);
        let alternation = DETECTORS.iter().map(|(_, _, p)| format!("({p})")).collect::<Vec<_>>().join("|");
        Scrubber {
            key,
            variants,
            detectors: regex::Regex::new(&alternation).expect("detector patterns are constants"),
            paths: regex::Regex::new(PATH_PATTERN).expect("path pattern is a constant"),
        }
    }

    /// `⟨t:xxxxxxxx⟩`, where `t` is the type tag and the digest is
    /// HMAC-SHA256 of the value under the local key, truncated to eight hex
    /// characters.
    ///
    /// Eight hex characters sit below every detector's length threshold and
    /// the delimiters are outside every detector's character class, so a
    /// pseudonym trips no detector. That is what makes scrubbing
    /// idempotent, and it is a requirement with a test behind it rather
    /// than a property that happens to hold.
    pub fn pseudonym(&self, tag: char, value: &str) -> String {
        format!("⟨{tag}:{}⟩", digest(&self.key, value))
    }

    /// Runs all four layers over `text`, counting replacements in `report`.
    pub fn scrub(&self, text: &str, report: &mut Report) -> String {
        let mut out = text.to_string();
        for (tag, form, replacement) in &self.variants {
            if out.contains(form.as_str()) {
                report.record(*tag);
                out = out.replace(form.as_str(), replacement);
            }
        }
        let out = self
            .detectors
            .replace_all(&out, |caps: &regex::Captures| match fired(caps) {
                Some((index, matched)) => {
                    report.record(DETECTORS[index].1);
                    self.pseudonym(DETECTORS[index].1, matched)
                }
                None => caps[0].to_string(),
            })
            .into_owned();
        self.paths.replace_all(&out, |caps: &regex::Captures| self.componentize(&caps[0], report)).into_owned()
    }

    /// Replaces every component of `path` that is not a common directory
    /// with a pseudonym, keeping the alphabetic extension of the last one.
    fn componentize(&self, path: &str, report: &mut Report) -> String {
        let parts: Vec<&str> = path.split('/').collect();
        let last = parts.len() - 1;
        let mut rendered: Vec<String> = Vec::new();
        for (index, part) in parts.iter().enumerate() {
            if part.is_empty() || common(part) {
                rendered.push(part.to_string());
                continue;
            }
            report.record('p');
            let keep = (index == last).then(|| crate::extract::extension(part)).flatten();
            rendered.push(format!(
                "{}{}",
                self.pseudonym('p', part),
                keep.map(|e| format!(".{e}")).unwrap_or_default()
            ));
        }
        rendered.join("/")
    }

    /// Every detector and known value the self-check can still see in
    /// `text`. An empty result is the condition for emitting `field`.
    ///
    /// The known-value scan here is deliberately looser than the
    /// substitution above: substitution must match exactly, because folding
    /// case would corrupt paths that differ only by case, while detection
    /// may match however loosely it likes. A known value that reaches the
    /// output in a form the substitution did not cover is therefore caught
    /// here and fails the emission instead of shipping.
    pub fn findings(&self, field: &str, text: &str) -> Vec<String> {
        let mut found: Vec<String> = Vec::new();
        let mut name = |detector: &str| {
            let finding = format!("{detector} survived scrubbing in {field}");
            if !found.contains(&finding) {
                found.push(finding);
            }
        };
        let lowered = text.to_ascii_lowercase();
        for (tag, form, _) in &self.variants {
            if lowered.contains(&form.to_ascii_lowercase()) {
                name(&format!("known {}", tag_name(*tag)));
            }
        }
        for caps in self.detectors.captures_iter(text) {
            if let Some((index, _)) = fired(&caps) {
                name(DETECTORS[index].0);
            }
        }
        found
    }
}

/// The detector that matched, as an index into [`DETECTORS`], and the text
/// it matched. Yields nothing when the only detector that matched was the
/// entropy-gated one and the run did not clear the gate.
fn fired<'t>(caps: &regex::Captures<'t>) -> Option<(usize, &'t str)> {
    let index = (0..DETECTORS.len()).find(|i| caps.get(i + 1).is_some())?;
    let matched = caps.get(index + 1)?.as_str();
    (index != HIGH_ENTROPY || high_entropy(matched)).then_some((index, matched))
}

fn high_entropy(run: &str) -> bool {
    let mut seen: BTreeMap<char, f64> = BTreeMap::new();
    for c in run.chars() {
        *seen.entry(c).or_default() += 1.0;
    }
    let length = run.chars().count() as f64;
    let bits: f64 = seen.values().map(|n| -(n / length) * (n / length).log2()).sum();
    bits >= ENTROPY_BITS && run.chars().any(|c| c.is_ascii_digit()) && run.chars().any(|c| c.is_ascii_uppercase())
}

/// The forms one known value takes in text — as recorded, with a trailing
/// slash, JSON-escaped both ways, and abbreviated against the home
/// directory — each with the text that must follow its pseudonym.
fn variant_forms(value: &str) -> Vec<(String, String)> {
    let home = ["/home/", "/Users/"].iter().find_map(|p| value.strip_prefix(p)).and_then(|r| r.split_once('/'));
    let bare = [value.to_string(), value.replace('/', r"\/"), value.replace('\\', r"\\")];
    let mut forms: Vec<(String, String)> = vec![(format!("{value}/"), "/".to_string())];
    forms.extend(bare.into_iter().chain(home.map(|(_, tail)| format!("~/{tail}"))).map(|f| (f, String::new())));
    forms.retain(|(form, _)| form.len() >= 3);
    forms
}

/// Eight hex characters of HMAC-SHA256 over `value` under `key`.
fn digest(key: &[u8], value: &str) -> String {
    hex::encode(&hmac_sha256(key, value.as_bytes())[..4])
}

/// HMAC-SHA256, in the standard inner/outer padded construction. Keyed
/// hashing rather than a plain digest is what makes a pseudonym
/// irrecoverable: an unkeyed hash of a user name falls to a word list.
pub fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    let mut block = [0u8; 64];
    match key.len() > block.len() {
        true => block[..32].copy_from_slice(&Sha256::digest(key)),
        false => block[..key.len()].copy_from_slice(key),
    }
    let inner: Vec<u8> = block.iter().map(|b| b ^ 0x36).collect();
    let outer: Vec<u8> = block.iter().map(|b| b ^ 0x5c).collect();
    let digest = Sha256::new().chain_update(inner).chain_update(message).finalize();
    Sha256::new().chain_update(outer).chain_update(digest).finalize().into()
}

/// Reads the local key from `dir`, creating it from the system random
/// source on first use. The key never leaves this file's callers: it is not
/// emitted, not logged, and not part of any identity. Pseudonyms are
/// therefore stable across every episode written under one output
/// directory and meaningless outside it, which makes cross-installation
/// joins impossible by construction.
pub fn local_key(dir: &Path) -> std::io::Result<Vec<u8>> {
    let path = dir.join(KEY_FILE);
    if let Ok(bytes) = std::fs::read(&path) {
        if bytes.len() == 32 {
            return Ok(bytes);
        }
    }
    use std::os::unix::fs::OpenOptionsExt;
    let mut key = [0u8; 32];
    std::fs::File::open("/dev/urandom")?.read_exact(&mut key)?;
    std::fs::create_dir_all(dir)?;
    let mut file = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&path)?;
    std::io::Write::write_all(&mut file, &key)?;
    Ok(key.to_vec())
}

#[cfg(test)]
#[path = "scrub_test.rs"]
mod tests;
