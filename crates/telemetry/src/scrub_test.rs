use super::*;

/// A fixed key. The scrubber's output must be a function of its inputs, so
/// a test key produces the same pseudonyms on every machine.
fn key() -> Vec<u8> {
    (0u8..32).collect()
}

fn known(values: &[(char, &str)]) -> Vec<KnownValue> {
    values.iter().map(|(tag, value)| KnownValue { tag: *tag, value: value.to_string() }).collect()
}

fn scrubber() -> Scrubber {
    Scrubber::new(key(), &known(&[('p', "/home/rowan/work/repo"), ('u', "rowan"), ('p', "/srv/capture")]))
}

fn scrub(text: &str) -> String {
    scrubber().scrub(text, &mut Report::default())
}

/// One planted secret of each detector class, with the text it is seeded
/// in. Every one must be gone from the scrubbed output.
const PLANTED: &[(&str, &str)] = &[
    ("pem-header", "-----BEGIN RSA PRIVATE KEY-----"),
    ("ssh-key", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH8kQ2vTgLpR7wXyZ0aB"),
    ("aws-key", "AKIAIOSFODNN7EXAMPLE"),
    ("github-token", "ghp_16C7e42F292c6912E7710c838347Ae178B4a"),
    ("model-key", "sk-proj-9RtQm2vX7LpZ0aWyKd83Hn4Fb6Tg"),
    ("chat-token", "xoxb-2410-77081-3xYzQpLmNvBt"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"),
    ("email", "rowan.quill@example.org"),
    ("url-with-userinfo", "https://deploy:s3cr3tpassword@internal.example.net/artifacts"),
    ("uuid", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
    ("mac", "00:1b:44:11:3a:b7"),
    ("ipv4", "192.168.14.203"),
    ("ipv6", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"),
    ("long-hex", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"),
    ("high-entropy", "Zm9vYmFyQmF6UXV4MTIzNDU2Nzg5MFFXRVJUWQ=="),
];

#[test]
fn every_planted_secret_is_replaced() {
    for (name, secret) in PLANTED {
        let seeded = format!("failed while reading {secret} from the store");
        let scrubbed = scrub(&seeded);
        assert!(!scrubbed.contains(secret), "{name} survived: {scrubbed}");
        assert!(scrubbed.contains('⟨'), "{name} produced no pseudonym: {scrubbed}");
    }
}

#[test]
fn a_planted_secret_leaves_no_finding_behind() {
    let corpus = PLANTED.iter().map(|(_, secret)| *secret).collect::<Vec<_>>().join(" and then ");
    let scrubber = scrubber();
    let scrubbed = scrubber.scrub(&corpus, &mut Report::default());
    assert!(scrubber.findings("corpus", &scrubbed).is_empty());
}

#[test]
fn a_known_value_is_replaced_in_every_variant_form() {
    let forms = [
        "/home/rowan/work/repo",
        "/home/rowan/work/repo/",
        r"\/home\/rowan\/work\/repo",
        "~/work/repo",
        "/srv/capture",
    ];
    for form in forms {
        let scrubbed = scrub(&format!("cannot open {form}: read-only"));
        assert!(!scrubbed.contains("rowan"), "{form} left a user name: {scrubbed}");
        assert!(!scrubbed.contains("capture"), "{form} left a path component: {scrubbed}");
        assert!(!scrubbed.contains("work"), "{form} left a path component: {scrubbed}");
    }
}

#[test]
fn no_known_value_survives_anywhere_in_the_corpus() {
    let corpus = "rowan ran /home/rowan/work/repo/build.sh and wrote \\/srv\\/capture\\/out.txt from ~/work/repo";
    let scrubber = scrubber();
    let scrubbed = scrubber.scrub(corpus, &mut Report::default());
    assert!(!scrubbed.contains("rowan"));
    assert!(!scrubbed.contains("capture"));
    assert!(scrubber.findings("corpus", &scrubbed).is_empty());
}

#[test]
fn scrubbing_is_idempotent() {
    let corpus = PLANTED.iter().map(|(_, secret)| *secret).collect::<Vec<_>>().join(" ")
        + " /home/rowan/work/repo/src/main.rs ~/work/repo rowan";
    let scrubber = scrubber();
    let once = scrubber.scrub(&corpus, &mut Report::default());
    let twice = scrubber.scrub(&once, &mut Report::default());
    assert_eq!(once, twice);
}

#[test]
fn a_replacement_token_trips_no_detector() {
    let scrubber = scrubber();
    // Every type tag, alone, repeated, adjacent, and inside a path and a
    // dotted name — the shapes a second pass would see.
    for tag in ['p', 'u', 'e', 'h', 's'] {
        let token = scrubber.pseudonym(tag, "some value");
        let shapes = [
            token.clone(),
            format!("{token}{token}{token}"),
            format!("/{token}/{token}/{token}.py"),
            format!("{token}.{token}.{token}"),
            format!("{token}:{token}:{token}"),
            format!("{token}@{token}"),
        ];
        for shape in shapes {
            assert!(scrubber.findings("token", &shape).is_empty(), "a pseudonym tripped a detector: {shape}");
            assert_eq!(scrubber.scrub(&shape, &mut Report::default()), shape);
        }
    }
}

#[test]
fn a_pseudonym_is_eight_hex_characters_under_a_keyed_digest() {
    let scrubber = scrubber();
    let token = scrubber.pseudonym('u', "rowan");
    assert_eq!(token.chars().count(), 12, "{token}");
    assert!(token.starts_with("⟨u:") && token.ends_with('⟩'));
    let digest = token.trim_start_matches("⟨u:").trim_end_matches('⟩');
    assert!(digest.len() == 8 && digest.chars().all(|c| c.is_ascii_hexdigit()));
    // Same value and key, same token: joins across episodes hold.
    assert_eq!(token, Scrubber::new(key(), &[]).pseudonym('u', "rowan"));
    // A different key, a different token: joins across installations cannot.
    assert_ne!(token, Scrubber::new(vec![9u8; 32], &[]).pseudonym('u', "rowan"));
}

#[test]
fn a_path_keeps_its_common_directories_and_its_extension() {
    let scrubbed = scrub("/usr/lib/plaintiff/src/tests/verdict.py: no such file");
    assert!(scrubbed.starts_with("/usr/lib/⟨p:"), "{scrubbed}");
    assert!(scrubbed.contains("/src/tests/⟨p:"), "{scrubbed}");
    assert!(scrubbed.ends_with(".py: no such file"), "{scrubbed}");
    assert!(!scrubbed.contains("plaintiff") && !scrubbed.contains("verdict"));
}

#[test]
fn a_workspace_relative_path_is_componentized_too() {
    // Tool subjects report paths relative to the workspace, so a layer that
    // only reached absolute paths would pass these through untouched.
    let scrubbed = scrub("ledger/quarterly/summary.txt lines 1-40 of 400");
    assert!(!scrubbed.contains("ledger") && !scrubbed.contains("quarterly") && !scrubbed.contains("summary"));
    assert!(scrubbed.contains(".txt lines 1-40 of 400"), "{scrubbed}");
}

#[test]
fn a_path_of_common_directories_alone_is_left_as_it_is() {
    assert_eq!(scrub("cannot write to /usr/lib"), "cannot write to /usr/lib");
}

#[test]
fn ordinary_prose_is_left_as_it_is() {
    let subject = "grep -rn TODO src · exit 0 in 0.01s";
    assert_eq!(scrub(subject), subject);
}

#[test]
fn a_long_low_entropy_run_is_not_treated_as_key_material() {
    // Twenty-odd characters of ordinary identifier: the pattern reaches it
    // and the gate rejects it.
    let subject = "configurationmanagerfactory not found";
    assert_eq!(scrub(subject), subject);
}

#[test]
fn a_digest_in_an_error_line_is_masked_even_though_it_is_probably_a_commit() {
    // Losing a commit hash from an error line is acceptable; shipping a key
    // that looked like one is not.
    let scrubbed = scrub("bad object 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b");
    assert!(!scrubbed.contains("9f86d081"), "{scrubbed}");
}

#[test]
fn the_report_counts_replacements_by_type_and_never_the_values() {
    let mut report = Report::default();
    scrubber().scrub("rowan mailed rowan.quill@example.org about /srv/capture/out.log", &mut report);
    assert_eq!(report.0.get("user"), Some(&1));
    assert_eq!(report.0.get("email"), Some(&1));
    assert!(report.0.values().sum::<u32>() >= 3);
    let rendered = serde_json::to_string(&report).unwrap();
    assert!(!rendered.contains("rowan") && !rendered.contains("capture"));
}

#[test]
fn the_self_check_sees_a_known_value_the_substitution_missed() {
    // Substitution matches exactly, because folding case would corrupt
    // paths that differ only by case. Detection folds case, so a known
    // value that reaches the output in a form substitution did not cover is
    // caught here rather than shipped.
    let scrubber = scrubber();
    let slipped = "permission denied for user ROWAN";
    let scrubbed = scrubber.scrub(slipped, &mut Report::default());
    assert_eq!(scrubbed, slipped, "layer one is expected to miss this form");
    let findings = scrubber.findings("tool/result.subject", &scrubbed);
    assert_eq!(findings, vec!["known user survived scrubbing in tool/result.subject"]);
}

#[test]
fn the_self_check_names_every_detector_that_still_fires() {
    let scrubber = scrubber();
    let found = scrubber.findings("field", "mail auditor@example.org from 10.0.0.4");
    assert_eq!(found, vec!["email survived scrubbing in field", "ipv4 survived scrubbing in field"]);
}

#[test]
fn the_key_is_generated_once_and_kept_private() {
    let dir = std::env::temp_dir().join(format!("foe-telemetry-key-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let first = local_key(&dir).unwrap();
    assert_eq!(first.len(), 32);
    assert_eq!(local_key(&dir).unwrap(), first, "a second run must reuse the key, or joins break");
    use std::os::unix::fs::PermissionsExt;
    let mode = std::fs::metadata(dir.join(KEY_FILE)).unwrap().permissions().mode();
    assert_eq!(mode & 0o777, 0o600);
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn hmac_matches_the_published_test_vector() {
    // RFC 4231 test case 2, which pins the padding construction.
    let digest = hmac_sha256(b"Jefe", b"what do ya want for nothing?");
    assert_eq!(hex::encode(digest), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
}

#[test]
fn one_known_value_carries_one_pseudonym_however_it_was_written() {
    // Four spellings of one directory. If they hashed apart, a join over
    // the workspace would split four ways.
    let scrubber = scrubber();
    let token = scrubber.pseudonym('p', "/home/rowan/work/repo");
    for form in ["/home/rowan/work/repo", "/home/rowan/work/repo/", r"\/home\/rowan\/work\/repo", "~/work/repo"] {
        let scrubbed = scrubber.scrub(form, &mut Report::default());
        assert!(scrubbed.starts_with(&token), "{form} became {scrubbed}, not {token}");
    }
    // A trailing slash survives as a trailing slash rather than being eaten.
    assert_eq!(scrubber.scrub("/home/rowan/work/repo/", &mut Report::default()), format!("{token}/"));
}
