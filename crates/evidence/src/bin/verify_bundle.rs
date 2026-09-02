//! Verifies an evidence bundle using only the files in its directory.

use foe_evidence::verify_adoption;
use std::path::Path;
use std::process::ExitCode;

const USAGE: &str = "usage: verify-evidence-bundle DIR [EXPECTED_PREDECESSOR_CONTRACT_FINGERPRINT]";

fn run(args: &[String]) -> Result<String, String> {
    let (dir, predecessor) = match args {
        [dir] => (dir, None),
        [dir, predecessor] => (dir, Some(predecessor.as_str())),
        _ => return Err(USAGE.into()),
    };
    let verified = verify_adoption(Path::new(dir), predecessor).map_err(|error| error.to_string())?;
    serde_json::to_string(&verified).map_err(|error| error.to_string())
}

fn main() -> ExitCode {
    match run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        Ok(verified) => {
            println!("{verified}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("verify-evidence-bundle: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::run;

    #[test]
    fn refuses_bad_arguments() {
        assert!(run(&[]).unwrap_err().starts_with("usage:"));
        assert!(run(&["one".into(), "two".into(), "three".into()]).unwrap_err().starts_with("usage:"));
    }
}
