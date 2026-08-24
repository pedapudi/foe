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
        "program_lineage",
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

/// Renders a parsed command as one line, so that a golden test can assert
/// exactly what an invocation parses to rather than matching a shape. Only
/// the marker `error` is rendered for a failure: error wording is allowed
/// to change, the fact of failing is not.
fn golden(line: &str) -> String {
    match parse(line) {
        Ok(Command::Run(o)) => format!(
            "run task={:?} config={:?} model={:?} key_file={:?} log_dir={:?} no_open={} headless={} host={}",
            o.task, o.config, o.model, o.key_file, o.log_dir, o.no_open, o.headless, o.host
        ),
        Ok(Command::Login { provider, model, status }) => {
            format!("login provider={provider:?} model={model:?} status={status}")
        }
        Ok(Command::View { dir, serve, port }) => format!("view dir={dir:?} serve={serve} port={port}"),
        Ok(Command::Plan { config, json }) => format!("plan config={config:?} json={json}"),
        Ok(Command::Tools { config }) => format!("tools config={config:?}"),
        Ok(Command::Lineage { state, states, evidence, json }) => {
            format!("lineage state={state:?} states={states:?} evidence={evidence:?} json={json}")
        }
        Ok(Command::Schema) => "schema".to_string(),
        Ok(Command::Telemetry { logs, json }) => format!("telemetry logs={logs:?} json={json}"),
        Ok(Command::Help(form)) => format!("help {}", form.name),
        Err(_) => "error".to_string(),
    }
}

/// One representative invocation per form, asserted against exactly what
/// the parser produces. This is the behavior-invariance net: the parser may
/// be rebuilt, but every line here must keep parsing to the same values.
#[test]
fn representative_invocations_parse_to_known_values() {
    let cases = [
        ("fix", "run task=Some(\"fix\") config=None model=None key_file=None log_dir=None no_open=false headless=false host=false"),
        (
            "fix --config c.json --model p/m --key-file k.txt --log-dir logs --no-open --headless",
            "run task=Some(\"fix\") config=Some(\"c.json\") model=Some(\"p/m\") key_file=Some(\"k.txt\") \
             log_dir=Some(\"logs\") no_open=true headless=true host=false",
        ),
        (
            "--config c.json --host --log-dir logs",
            "run task=None config=Some(\"c.json\") model=None key_file=None log_dir=Some(\"logs\") no_open=false \
             headless=false host=true",
        ),
        ("login", "login provider=None model=None status=false"),
        ("login openai --model gpt", "login provider=Some(\"openai\") model=Some(\"gpt\") status=false"),
        ("login --status", "login provider=None model=None status=true"),
        ("view logs", "view dir=\"logs\" serve=false port=0"),
        ("view logs --serve --port 8080", "view dir=\"logs\" serve=true port=8080"),
        ("plan --config c.json", "plan config=\"c.json\" json=false"),
        ("plan --config c.json --json", "plan config=\"c.json\" json=true"),
        ("tools", "tools config=None"),
        ("tools --config c.json", "tools config=Some(\"c.json\")"),
        ("schema", "schema"),
        (
            "lineage s.json --states st --evidence ev",
            "lineage state=\"s.json\" states=\"st\" evidence=\"ev\" json=false",
        ),
        ("lineage --states st --evidence ev", "error"),
        ("telemetry a.jsonl b.jsonl --json", "telemetry logs=[\"a.jsonl\", \"b.jsonl\"] json=true"),
        ("", "error"),
    ];
    for (line, expected) in cases {
        assert_eq!(golden(line), expected, "`foe {line}`");
    }
}

/// Runs the help of one form the way the binary does, without a process.
fn help_of(form: &'static Form) -> String {
    let line = format!("{} --help", form.name);
    match parse(&line) {
        Ok(Command::Help(chosen)) => help(chosen),
        _ => panic!("`foe {line}` did not ask for help"),
    }
}

/// The acceptance the issue states: every option of every form is
/// documented where a person can reach it. An option added to the table
/// without a meaning, or a form added without a description, fails here.
#[test]
fn every_form_documents_every_option_it_accepts() {
    for form in FORMS {
        let text = help_of(form);
        assert!(text.starts_with("usage: foe"), "`foe {} --help` has no usage line:\n{text}", form.name);
        assert!(text.contains(form.about), "`foe {} --help` does not say what it does", form.name);
        for o in accepted(form) {
            assert!(!o.meaning.is_empty(), "{} of `foe {}` has no meaning", o.flag, form.name);
            let line = text.lines().find(|l| l.trim_start().starts_with(o.flag)).unwrap_or_else(|| {
                panic!("`foe {} --help` does not list {}:\n{text}", form.name, o.flag);
            });
            assert!(line.contains(o.meaning), "{} of `foe {}` is listed without its meaning", o.flag, form.name);
            assert!(line.contains(o.value), "{} of `foe {}` is listed without its value", o.flag, form.name);
            let absent = match o.absent {
                "" => String::new(),
                "required" => "(required)".to_string(),
                text => format!("(default: {text})"),
            };
            assert!(line.contains(&absent), "{} of `foe {}` is listed without `{absent}`", o.flag, form.name);
        }
    }
}

/// `foe --help` and `foe help` are the same screen, and it names every
/// command word, so a form added to the table is announced.
#[test]
fn the_top_level_help_names_every_command() {
    let Ok(Command::Help(dashed)) = parse("--help") else { panic!("`foe --help` does not ask for help") };
    let Ok(Command::Help(worded)) = parse("help") else { panic!("`foe help` does not ask for help") };
    let text = help(dashed);
    assert_eq!(text, help(worded));
    for form in FORMS.iter().filter(|f| !f.name.is_empty()) {
        assert!(text.contains(form.name), "`foe --help` does not name `{}`", form.name);
        assert!(text.contains(form.about), "`foe --help` does not say what `{}` does", form.name);
    }
    assert!(text.contains("run `foe <command> --help`"), "`foe --help` does not point at the command help");
    assert_eq!(golden("help login"), "help login", "`foe help <command>` is that command's help");
}

/// An unknown option names itself and the help that would have listed it,
/// rather than reprinting every form. An option of another form is unknown
/// here, which is the acceptance the table replaced the cross-check with.
#[test]
fn an_unknown_option_names_itself_and_the_command_help() {
    let cases = [
        ("plan --serve", "unknown option --serve for `foe plan`; run `foe plan --help` for the options it takes"),
        ("--serve", "unknown option --serve for `foe`; run `foe --help` for the options it takes"),
        ("schema --json", "unknown option --json for `foe schema`; run `foe schema --help` for the options it takes"),
    ];
    for (line, expected) in cases {
        assert_eq!(parse(line).err().as_deref(), Some(expected), "`foe {line}`");
    }
    assert_eq!(parse("plan --config").err().as_deref(), Some("--config takes FILE"));
}

/// Help is reachable from a form that would otherwise refuse the
/// invocation, because a person asking for help has not supplied the
/// arguments yet.
#[test]
fn help_outranks_a_missing_argument() {
    for line in ["plan --help", "view --help", "telemetry --help", "--help"] {
        assert!(matches!(parse(line), Ok(Command::Help(_))), "`foe {line}` did not print help");
    }
}
