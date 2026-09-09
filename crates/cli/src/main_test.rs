use super::*;
use std::ops::Deref;

pub(crate) struct ScratchDir(Option<tempfile::TempDir>);

impl ScratchDir {
    fn path(&self) -> &Path {
        self.0.as_ref().unwrap().path()
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        self.path()
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.0.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", dir.path().display());
            dir.disable_cleanup(true);
            return;
        }
        let path = dir.path().to_path_buf();
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
    }
}

pub(crate) fn scratch(prefix: &str, name: &str) -> ScratchDir {
    assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
    ScratchDir(Some(tempfile::Builder::new().prefix(&format!("{prefix}-{name}-")).tempdir().unwrap()))
}

fn parse(line: &str) -> Result<Command, String> {
    let args: Vec<String> = line.split_whitespace().map(str::to_string).collect();
    command(&args)
}

/// docs/design.md: readable terminal output is opt-in.
#[test]
fn conversation_is_an_explicit_running_option() {
    let Ok(Command::Run(default)) = parse("a-task") else { panic!() };
    assert!(!default.conversation);
    for input in ["a-task --conversation", "--config c.json --conversation --viewer off"] {
        let Ok(Command::Run(options)) = parse(input) else { panic!("{input}") };
        assert!(options.conversation);
    }
    let Ok(Command::Run(options)) = parse("a-task --conversation --viewer serve") else { panic!() };
    assert!(options.conversation && options.viewer == run::Viewer::Serve);
    assert!(parse("view logs --conversation").is_err());
}

#[test]
fn every_form_parses_and_foreign_options_are_refused() {
    assert!(matches!(parse("plan --schema"), Ok(Command::Schema)));
    assert!(matches!(parse("plan --config c.json --json"), Ok(Command::Plan { json: true, .. })));
    assert!(matches!(parse("plan"), Ok(Command::Plan { config: None, json: false, .. })));
    assert!(matches!(
        parse("login"),
        Ok(Command::Login(login::Options { provider: None, model: None, status: false, .. }))
    ));
    assert!(matches!(parse("login --status"), Ok(Command::Login(login::Options { provider: None, status: true, .. }))));
    let Ok(Command::Login(login::Options { provider, model, .. })) = parse("login example --model m") else { panic!() };
    assert_eq!((provider.as_deref(), model.as_deref()), (Some("example"), Some("m")));
    assert!(parse("login a b").is_err(), "login takes one provider");
    assert!(matches!(parse("view logs --serve --port 8080"), Ok(Command::View { serve: true, port: 8080, .. })));
    let Ok(Command::Run(options)) = parse("--config c.json --protocol-fds 3,4") else { panic!() };
    assert_eq!(options.protocol_fds, Some((3, 4)));
    let Ok(Command::Run(options)) = parse("fix --model example/m --service-tier priority --sandbox off --viewer off")
    else {
        panic!()
    };
    assert_eq!((options.task.as_deref(), options.viewer), (Some("fix"), run::Viewer::Off));
    assert_eq!(options.sandbox.as_deref(), Some("off"));
    assert_eq!(options.service_tier.as_deref(), Some("priority"));
    let Ok(Command::Run(options)) = parse("--from /logs/ep_1 --config c.json") else { panic!() };
    assert_eq!((options.from.as_deref(), options.at), (Some(Path::new("/logs/ep_1")), None));
    let Ok(Command::Run(options)) = parse("redo --from /logs/ep_1@12") else { panic!() };
    assert_eq!((options.from.as_deref(), options.at), (Some(Path::new("/logs/ep_1")), Some(12)));
    let Ok(Command::Run(options)) = parse("redo --from /logs/at@noon") else { panic!() };
    assert_eq!(options.from.as_deref(), Some(Path::new("/logs/at@noon")), "only a digit suffix is a boundary");
    assert!(parse("plan --json").is_err(), "--json takes --config");
    assert!(parse("plan --schema --json").is_err(), "--schema stands alone");
    assert!(parse("plan --config c.json --evidence ev").is_err(), "plan rejects removed adoption options");
    assert!(parse("view --json").is_err(), "an option of another form is refused");
    assert!(parse("view").is_err(), "view needs a directory");
    assert!(parse("").is_err());
}

/// docs/protocol.md "Launch": no option a run takes behaves differently for
/// the presence of a host. A task on the command line, a built-in document,
/// the viewer, and the conversation are what the command line asked for,
/// whether or not another process answers the model requests.
#[test]
fn the_protocol_channel_changes_no_other_option() {
    for host in ["", " --protocol-fds 3,4"] {
        let Ok(Command::Run(options)) = parse(&format!("fix --config builtin:coding --conversation{host}")) else {
            panic!("a built-in document with a task runs{host}")
        };
        assert_eq!(options.task.as_deref(), Some("fix"));
        assert_eq!(options.config.as_deref(), Some("builtin:coding"));
        assert!(options.conversation);
        assert_eq!(options.viewer, run::Viewer::Open, "the viewer is what the command line asked for");
        let Ok(Command::Run(off)) = parse(&format!("fix --viewer off{host}")) else { panic!("--viewer off{host}") };
        assert_eq!(off.viewer, run::Viewer::Off);
    }
}

#[test]
fn the_schema_is_json_and_names_every_key_of_the_document() {
    let schema: serde_json::Value = serde_json::from_str(SCHEMA).unwrap();
    assert_eq!(schema["properties"]["version"]["const"], 4);
    let keys: Vec<&str> = schema["properties"].as_object().unwrap().keys().map(String::as_str).collect();
    let expected = [
        "budget",
        "child_contracts",
        "context",
        "done_when",
        "grants",
        "host_tools",
        "instructions",
        "model",
        "name",
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
            "run task={:?} config={:?} model={:?} log_dir={:?} from={:?} at={:?} viewer={:?} protocol_fds={:?}",
            o.task, o.config, o.model, o.log_dir, o.from, o.at, o.viewer, o.protocol_fds
        ),
        Ok(Command::Init { repository }) => format!("init repository={repository:?}"),
        Ok(Command::Login(login::Options { provider, model, key_file, status })) => {
            format!("login provider={provider:?} model={model:?} key_file={key_file:?} status={status}")
        }
        Ok(Command::View { dir, serve, port }) => format!("view dir={dir:?} serve={serve} port={port}"),
        Ok(Command::Plan { config, json }) => format!("plan config={config:?} json={json}"),
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
        (
            "fix",
            "run task=Some(\"fix\") config=None model=None log_dir=None from=None at=None viewer=Open \
             protocol_fds=None",
        ),
        (
            "fix --config c.json --model p/m --log-dir logs --viewer serve",
            "run task=Some(\"fix\") config=Some(\"c.json\") model=Some(\"p/m\") log_dir=Some(\"logs\") from=None \
             at=None viewer=Serve protocol_fds=None",
        ),
        (
            "--config c.json --protocol-fds 3,4 --log-dir logs",
            "run task=None config=Some(\"c.json\") model=None log_dir=Some(\"logs\") from=None \
             at=None viewer=Open protocol_fds=Some((3, 4))",
        ),
        (
            "--config builtin:coding --protocol-fds 3,4",
            "run task=None config=Some(\"builtin:coding\") model=None log_dir=None from=None \
             at=None viewer=Open protocol_fds=Some((3, 4))",
        ),
        ("--config c.json --protocol-fds 3", "error"),
        ("fix --viewer watch", "error"),
        (
            "redo --from /logs/ep_1@12",
            "run task=Some(\"redo\") config=None model=None log_dir=None \
             from=Some(\"/logs/ep_1\") at=Some(12) viewer=Open protocol_fds=None",
        ),
        (
            "fix --config builtin:coding",
            "run task=Some(\"fix\") config=Some(\"builtin:coding\") model=None log_dir=None \
             from=None at=None viewer=Open protocol_fds=None",
        ),
        ("init --repository repo", "init repository=\"repo\""),
        ("init", "error"),
        ("init --repository repo extra", "error"),
        ("login", "login provider=None model=None key_file=None status=false"),
        ("login example --model m", "login provider=Some(\"example\") model=Some(\"m\") key_file=None status=false"),
        (
            "login example --key-file /keys/endpoint.json",
            "login provider=Some(\"example\") model=None key_file=Some(\"/keys/endpoint.json\") status=false",
        ),
        ("login --status", "login provider=None model=None key_file=None status=true"),
        ("view logs", "view dir=\"logs\" serve=false port=0"),
        ("view logs --serve --port 8080", "view dir=\"logs\" serve=true port=8080"),
        ("plan", "plan config=None json=false"),
        ("plan --config c.json", "plan config=Some(\"c.json\") json=false"),
        ("plan --config builtin:coding", "plan config=Some(\"builtin:coding\") json=false"),
        ("plan --config c.json --json", "plan config=Some(\"c.json\") json=true"),
        ("plan --config c.json --states st --evidence ev", "error"),
        ("plan --config c.json --states st", "error"),
        ("plan --schema", "schema"),
        ("plan --schema --json", "error"),
        // A word that once selected a removed form is a task like any other.
        (
            "tools",
            "run task=Some(\"tools\") config=None model=None log_dir=None from=None at=None \
             viewer=Open protocol_fds=None",
        ),
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

/// docs/design.md "The command line": the running form's help lists each of
/// its options once, under the heading its row names, and `--protocol-fds`
/// above every heading because it names the channel to a host process.
#[test]
fn the_running_help_lists_every_option_under_one_group() {
    let text = help_of(&FORMS[0]);
    let listed = |flag: &str| text.lines().filter(|line| line.trim_start().starts_with(flag)).count();
    for o in OPTS.iter().filter(|o| o.command.is_empty()) {
        let named = GROUPS.iter().filter(|group| **group == o.group).count();
        match o.flag {
            "--protocol-fds" => assert_eq!(o.group, "", "--protocol-fds stands above the groups"),
            flag => assert_eq!(named, 1, "{flag} names {named} of the groups the help prints"),
        }
        assert_eq!(listed(o.flag), 1, "{} is listed {} times:\n{text}", o.flag, listed(o.flag));
    }
    assert_eq!(listed("--help"), 1);
    for group in GROUPS {
        assert!(text.contains(&format!("\n{group}:\n")), "the help prints no `{group}` heading:\n{text}");
    }
    let headings: Vec<&str> = text.lines().filter(|line| GROUPS.contains(&line.trim_end_matches(':'))).collect();
    assert_eq!(headings.len(), GROUPS.len(), "each heading is printed once: {headings:?}");
    for form in FORMS.iter().filter(|form| !form.name.is_empty()) {
        let other = help_of(form);
        assert!(!GROUPS.iter().any(|group| other.contains(group)), "`foe {}` prints no group", form.name);
    }
}

/// docs/design.md "The command line": `--verify` gates completion of the
/// document the run uses, which is either assessment branch at the coding
/// workflow's root and the one episode of the one-shot document.
#[test]
fn verifier_help_names_built_in_completion() {
    let text = help_of(&FORMS[0]);
    assert!(text.contains("acceptance completes the built-in document"));
}

/// docs/design.md "The command line": a built-in run may select any
/// sandbox mode that the configuration vocabulary defines.
#[test]
fn sandbox_help_names_every_supported_mode() {
    let text = help_of(&FORMS[0]);
    assert!(text.contains("kernel confinement mode: best-effort, required, or off"));
}

/// docs/design.md "The command line": the tier vocabulary belongs to the
/// provider, so the help sends a reader to the per-provider values rather
/// than fixing a list of its own.
#[test]
fn service_tier_help_points_at_the_per_provider_values() {
    let text = help_of(&FORMS[0]);
    assert!(text.contains("the provider's request service tier"), "{text}");
    assert!(text.contains("docs/models.md lists the values each provider accepts"), "{text}");
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
        ("view --json", "unknown option --json for `foe view`; run `foe view --help` for the options it takes"),
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

/// docs/design.md, "The command line": `--yolo` is a second spelling of the
/// long option, accepted and absent from the help.
#[test]
fn the_short_spelling_reaches_the_option_it_stands_for() {
    let long = "--dangerously-permit-everything-no-sandbox";
    for flag in ["--yolo", long] {
        let Ok(given) = given(&FORMS[0], &["t".to_string(), flag.to_string()]) else { panic!("{flag}") };
        assert!(given.options.contains_key(long), "{flag}");
    }
    assert!(!help(&FORMS[0]).contains("--yolo"));
    let Err(unknown) = given(&FORMS[0], &["--yolo-please".to_string()]) else { panic!("accepted") };
    assert!(unknown.starts_with("unknown option --yolo-please"), "{unknown}");
}
