//! See Cargo.toml: this binary exists only as the subtrahend of the
//! evaluator size measurement.

fn main() {
    let value = serde_json::json!({"ok": true});
    println!("{value}");
}
