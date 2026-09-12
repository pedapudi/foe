use super::*;

fn parse(line: &str) -> Result<Command, String> {
    let args: Vec<String> = line.split_whitespace().map(str::to_string).collect();
    command(&args)
}

#[test]
fn every_form_parses_and_foreign_options_are_refused() {
    assert!(matches!(parse("schema"), Ok(Command::Schema)));
    assert!(matches!(parse("plan --config c.json --json"), Ok(Command::Plan { json: true, .. })));
    assert!(matches!(parse("tools"), Ok(Command::Tools { config: None })));
    assert!(matches!(parse("login"), Ok(Command::Login { provider: None, model: None, status: false })));
    assert!(matches!(parse("login --status"), Ok(Command::Login { provider: None, status: true, .. })));
    let Ok(Command::Login { provider, model, .. }) = parse("login anthropic --model m") else { panic!() };
    assert_eq!((provider.as_deref(), model.as_deref()), (Some("anthropic"), Some("m")));
    assert!(parse("login a b").is_err(), "login takes one provider");
    assert!(matches!(parse("view logs --serve --port 8080"), Ok(Command::View { serve: true, port: 8080, .. })));
    assert!(matches!(parse("--config c.json --host"), Ok(Command::Run(run::Options { host: true, .. }))));
    let Ok(Command::Run(options)) = parse("fix --model anthropic/m --key-file k --headless --no-open") else {
        panic!()
    };
    assert_eq!((options.task.as_deref(), options.headless, options.no_open), (Some("fix"), true, true));
    assert!(parse("plan").is_err(), "plan needs --config");
    assert!(parse("schema --json").is_err(), "an option of another form is refused");
    assert!(parse("fix --host").is_err(), "--host takes its task from the configuration");
    assert!(parse("view").is_err(), "view needs a directory");
    assert!(parse("").is_err());
}

#[test]
fn the_schema_is_json_and_names_every_key_of_the_document() {
    let schema: serde_json::Value = serde_json::from_str(SCHEMA).unwrap();
    assert_eq!(schema["properties"]["version"]["const"], 2);
    let keys: Vec<&str> = schema["properties"].as_object().unwrap().keys().map(String::as_str).collect();
    let expected = [
        "budget",
        "context",
        "done_when",
        "grants",
        "host_tools",
        "instructions",
        "model",
        "name",
        "programs",
        "sandbox",
        "task",
        "tool_defs",
        "tools",
        "version",
        "workflow",
    ];
    assert_eq!(keys, expected);
}
