use super::*;

#[test]
fn builtin_coding_uses_low_reasoning_for_gpt_5_6_sol() {
    for provider in ["openai", "openai-codex"] {
        let config = builtin_config("task".into(), ModelConfig::new(provider, "gpt-5.6-sol"), None).unwrap();
        assert_eq!(config.model.unwrap().option("reasoning_effort"), Some("low"));
    }
}

#[test]
fn builtin_coding_preserves_explicit_reasoning_and_other_models() {
    let mut explicit = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    explicit.options.insert("reasoning_effort".into(), "high".into());
    let config = builtin_config("task".into(), explicit, None).unwrap();
    assert_eq!(config.model.unwrap().option("reasoning_effort"), Some("high"));

    let config = builtin_config("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None).unwrap();
    assert_eq!(config.model.unwrap().option("reasoning_effort"), None);
}
