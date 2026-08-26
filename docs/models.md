# Models

foe calls a model through one of its built-in clients or leaves the call to
the process that launched it. This document covers the built-in clients:
which providers exist, where their credentials live, how `foe login` sets
them up, how a program of your own becomes a provider, and what each
provider cannot express.

## Quick start

```
foe login anthropic
foe "describe what this repository does"
```

The first command asks for an API key, checks it with one request, writes it
to `~/.config/foe/credentials/anthropic.json`, and offers a list of models
to make the default. The second command runs the built-in coding
configuration against the current directory with that default model and
prints the outcome as one JSON line. No flag names a model or a key file,
because both were settled by the login.

`foe login` alone lists every provider this build knows and whether each is
configured. `foe login --status` shows the default model and every
credential path.

The built-in coding workflow uses low reasoning effort for its implementation
episode and high reasoning effort for its independent audit episode with
`gpt-5.6-sol` through `openai` or `openai-codex`. An explicit
`reasoning_effort` in the default model file applies to both episodes.
Programs given through `--config` use their model block without this coding
default.

## Providers

A provider is a name in the `model` block of a configuration. The name
decides the wire format, the kind of credential, and the default endpoint.

| name | what it is | credential | what `foe login` asks for |
|---|---|---|---|
| `anthropic` | Anthropic's API | API key | the key |
| `openai` | OpenAI's API, over the Responses API | API key | the key |
| `openai-compatible` | any server speaking the Chat Completions API: Ollama, vLLM, llama.cpp, LiteLLM, and others | API key | the server's base URL, then the key |
| `openrouter` | OpenRouter, one key for many models | API key | the key |
| `openai-codex` | a ChatGPT subscription through the Codex backend | OAuth token, obtained in the browser | nothing typed; a browser sign-in |
| `vertex` | Google Cloud Vertex AI: Gemini models, and Claude models by name | Google credentials | the credentials file, the project, the location |
| `exec` | a program of your own | none; the program holds its own | nothing; there is no login |

One `model` block per provider, each the smallest that runs after
`foe login`:

```json
{ "provider": "anthropic", "model": "claude-opus-5" }
{ "provider": "openai", "model": "gpt-5.6-sol" }
{ "provider": "openai-compatible", "model": "llama3.1", "base_url": "http://127.0.0.1:11434/v1" }
{ "provider": "openrouter", "model": "anthropic/claude-opus-5" }
{ "provider": "openai-codex", "model": "gpt-5.6-sol" }
{ "provider": "vertex", "model": "gemini-2.5-pro" }
{ "provider": "exec", "model": "openai/gpt-5", "exec": "/home/user/project/tools/litellm-transport" }
```

`foe plan --config FILE` prints a `model` line naming the resolved wire
format and credential path, or says that the provider is unknown to this
build and lists the known names.

## The `model` block

| field | type | required | meaning |
|---|---|---|---|
| `provider` | string | yes | a name from the table above |
| `model` | string | yes | the model identifier the provider expects |
| `max_output_tokens` | integer | no | per-request output limit for a provider that accepts one; the default is the provider's |
| any other key | string | per provider | a provider-specific option |

Every provider-specific option is a flat string. The options by provider:

| option | providers | meaning |
|---|---|---|
| `api_key_file` | `anthropic`, `openai`, `openai-compatible`, `openrouter` | absolute path of the key file; see the next section for the default |
| `token_file` | `openai-codex` | absolute path of the OAuth token file; see the next section for the default |
| `credentials_file` | `vertex` | absolute path of a Google application-default-credentials file or service account key |
| `project` | `vertex` | the Google Cloud project id; required |
| `location` | `vertex` | the region, such as `us-east5`, or `global`; required |
| `base_url` | every HTTP provider | replaces the default endpoint; required for `openai-compatible` |
| `reasoning_effort` | `openai`, `openai-codex` | sent as `reasoning.effort`; models without reasoning reject it |
| `service_tier` | `openai`, `openai-codex` | sent as the Responses API `service_tier` request field |
| `include_thoughts` | `vertex` with Gemini models | `"false"` leaves `thinkingConfig` out, for models without thinking |
| `exec` | `exec` | absolute path of the program; required |

The public OpenAI Responses API accepts `max_output_tokens`. The ChatGPT
Codex backend used by `openai-codex` rejects that field, so foe omits it on
that route. Foe charges the output usage that the backend reports after each
response. One response can cross the remaining output-token allowance.

`base_url` follows each provider's own convention. For `anthropic` it is an
origin, `https://api.anthropic.com`, and `/v1/messages` is appended. For the
OpenAI-shaped providers it includes the version prefix,
`https://api.openai.com/v1` or `http://127.0.0.1:11434/v1`, and
`/responses` or `/chat/completions` is appended. For `openai-codex` it is
`https://chatgpt.com/backend-api` and `/codex/responses` is appended. For
`vertex` it is the regional origin, derived from `location` when absent.

The `model` block does not participate in identity. A system that needs to
record which model ran reads it from the log.

### Context windows

The provider table records the context window of the models it knows, so
that a `context` block enabling compaction need not state `window_tokens`
for them. A window is matched by the longest model-name prefix in the
table; a name no prefix matches is unknown, and `context.window_tokens`
is then required. [compaction.md](compaction.md) states how the window is
used.

| provider | model-name prefix | window in tokens |
|---|---|---|
| `anthropic` | `claude-` | 200000 |
| `openai`, `openai-codex` | `gpt-5.6` | 1050000 |
| `openai`, `openai-codex` | `gpt-5` | 400000 |
| `openrouter` | `anthropic/claude-` | 200000 |
| `openrouter` | `openai/gpt-5` | 400000 |
| `openrouter` | `google/gemini-2.5` | 1048576 |
| `vertex` | `gemini-2.5` | 1048576 |
| `vertex` | `claude-` | 200000 |

`openai-compatible` and `exec` know no windows, because the model behind
them is whatever the server or program answers for.

## Where credentials live

foe has two convention paths, both under the home directory of the user
running it, and nothing else is found by convention.

| path | holds |
|---|---|
| `~/.config/foe/credentials/<provider>.json` | the credential of one provider |
| `~/.config/foe/default-model.json` | the `model` block a bare `foe "task"` runs |

The home directory is the one the passwd database records for the process's
user id. No environment variable is read, including `HOME`.

A `model` block may omit its credential field. The transport then reads the
provider's convention file. An explicit `api_key_file`, `token_file`, or
`credentials_file` in the block replaces it. Whichever file is used, its
path is written into the `model` block that `episode/start.program`
records, so the log says which credential ran.

The convention file's shape depends on the credential kind.

| kind | contents of `~/.config/foe/credentials/<provider>.json` |
|---|---|
| API key | `{ "api_key": "..." }` |
| OAuth token | `{ "access": "...", "refresh": "...", "expires": N, "account_id": "..." }`, with `expires` in milliseconds since the Unix epoch; `refresh` may be omitted |
| Google credentials | `{ "credentials_file": "/abs/path", "project": "...", "location": "..." }`, pointing at the file Google's tools wrote |

A file named explicitly by `api_key_file` may hold the bare key instead of
the JSON object; trailing whitespace is removed. When an OAuth token file
contains `refresh`, Foe renews the token at the provider's token endpoint after
the access token enters the sixty-second refresh window. Foe rewrites the token
file atomically with mode 0600. When an OAuth token file omits `refresh`, Foe
cannot renew the access token. This access-only credential works until the
access token enters the same refresh window. Foe then returns an error before
sending the model request. A Google access token is minted from the credentials
file and cached in memory until sixty seconds before it expires; nothing is
written back.

The credential file is the one file outside the grants that an episode may
read. The sandbox adds it as a readable file so that a child episode, which
inherits the parent's restrictions, can read it too. Tools never receive
it.

## `foe login`

```
foe login                      list providers with a one-line description and whether each is configured
foe login <provider>           configure it, then set it as the default model when none is set
foe login <provider> --model M set the default model explicitly
foe login --status             show the default model and every configured credential path
```

Every prompt is plain text on standard error, answered on standard input.
No secret is ever printed, and a key is typed with the terminal's echo off.
Every error says what to do next.

For a provider with an API key, the command prompts `Paste your <Provider>
API key:` and sends one authenticated request that lists the provider's
models; OpenRouter answers a key-information request instead. The
credentials file is written with mode 0600 only when the provider accepted
the key. A rejected key ends with the provider's message and the
instruction to run the command again. `openai-compatible` asks for the
server's base URL first, because it has no default, and stores that URL in
the default model block.

For `openai-codex`, the command starts a listener on `127.0.0.1:1455`, the
callback address registered for the Codex client, prints an authorization
URL, and opens it with `/usr/bin/xdg-open`. The flow is authorization code
with PKCE against `https://auth.openai.com`. When the browser returns to the
listener with a code, the command exchanges it for a token at
`https://auth.openai.com/oauth/token`, writes the token file, and prints
the last four characters of the account id. A busy port 1455 is reported
with the instruction to stop the program using it.

For `vertex`, the command asks for the credentials file, offering
`~/.config/gcloud/application_default_credentials.json` as the default, then
the project id and the location. It mints one access token to prove the
credentials work and writes the three values to the convention file.

After configuring a credential, the command offers the provider's preset
models as a numbered list when `~/.config/foe/default-model.json` does not
exist, or writes the `--model` value without asking. The last line of every
successful login is the next command to run:

```
next: foe "describe what this repository does"
```

A bare `foe "task"` reads the default model file when `--model` is absent.
`--model PROVIDER/MODEL` on the command line replaces it for one run, and
`--key-file PATH` names the key file explicitly.

When the selected model is `gpt-5.6-sol` through `openai` or
`openai-codex`, login writes `"reasoning_effort": "low"` into the default
model file. A pre-existing default model file receives the same effective
setting in memory when it omits the option. `foe login --status` reports
the effective reasoning effort.

## The `exec` transport

A `model` block whose provider is `exec` names a program. This is the seam
for a provider that foe does not know: a program written in any language
answers each model request and holds whatever credential it needs.

```json
"model": { "provider": "exec", "exec": "/home/user/project/tools/litellm-transport", "model": "openai/gpt-5", "api_key_file": "/home/user/project/.secrets/openai.key" }
```

For every model request the program is started once, through the same
executor that runs configured tools, with the network allowed and the model
name as its single argument. It reads one JSON object from standard input
and writes `model/chunk` lines to standard output in the shape
[protocol.md](protocol.md) defines:

```
stdin:  {"type":"model/request","request_id":"rq_01","model":"openai/gpt-5","system":"...","tools":[...],"messages":[...],"max_output_tokens":null,"options":{"api_key_file":"..."}}
stdout: {"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"text","delta":"Hello"}}
        {"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"done","stop":"end","usage":{"input":12,"output":2,"cache_read":0}}}
```

`tools` and `messages` have the shapes of the log's `request/header.tools`
and `model/request.messages`. `options` carries every key of the `model`
block other than `provider`, `model`, `max_output_tokens`, and `exec`, which
is how the program learns where its own credential lives. The program runs
under the episode's sandbox narrowed as for a configured tool: it reads the
read roots and the loader directories, it may open TCP connections, it
reads the resolver configuration that turns a host name into an address,
and it starts with an empty environment. A credential file it reads must
therefore lie under a read root.

The chunks reach the episode when the program exits, because the executor
captures output whole. A program that exits without a final `done` or
`error` chunk produces an error quoting its standard error; a non-zero exit
is not retried, an exit of zero without a final chunk is.

[`examples/exec-transport/`](../examples/exec-transport/) holds two such
programs and the configuration that runs them: `litellm-transport`, of
about fifty lines, which answers through `litellm`, and
`scripted-transport.py`, which answers with fixed chunks so that the
example runs without a credential. Its README states which lines to
change to reach a provider.

## Formats and credential sources

The transport crate is organized as a table of wire formats times credential
sources. A provider is one row of twelve fields: the name a configuration
writes, the title and one-line description `foe login` prints, the wire
format, the credential source, the default base URL, the path appended to
it, the options the `model` block must carry, the models `foe login`
offers, the context windows by model-name prefix, any fixed headers, and
how `foe login` proves a credential works. Each format and each source sits
behind a Cargo
feature of the `foe-transport` crate, and the default feature set enables
all of them. A row exists only when both of its features are enabled, so
`foe login` and `foe plan` describe the build that is running.

| wire format | module | providers | feature |
|---|---|---|---|
| Anthropic Messages | `format/messages.rs` | `anthropic`, `vertex` for `claude*` models | `messages` |
| OpenAI Chat Completions | `format/chat.rs` | `openai-compatible`, `openrouter` | `chat` |
| OpenAI Responses | `format/responses.rs` | `openai`, `openai-codex` | `responses` |
| Gemini on Vertex AI | `format/gemini.rs` | `vertex` for other models | `gemini` |

| credential source | module | what it reads | feature |
|---|---|---|---|
| API key | `auth/api_key.rs` | a key file | `api-key` |
| OAuth token file | `auth/token_file.rs` | a token file, refreshed at the provider's token endpoint | `token-file` |
| Google credentials | `auth/google.rs` | application default credentials or a service account key, exchanged for an access token | `google` |

Adding a provider that speaks an existing format with an existing source is
one row in `crates/transport/src/providers.rs`. A provider that serves the
Chat Completions API at `https://api.example.com/v1` with a bearer key,
offers two models with a 128,000-token window, and answers `GET /models`
would be:

```rust
#[cfg(all(feature = "chat", feature = "api-key"))]
Provider {
    name: "example",
    title: "Example",
    description: "Example's hosted models, over the Chat Completions API",
    format: WireFormat::Chat,
    auth: AuthKind::ApiKey { header: KeyHeader::Bearer },
    default_base_url: Some("https://api.example.com/v1"),
    path: "/chat/completions",
    required: &[],
    presets: &["example-large", "example-small"],
    windows: &[("example-", 128_000)],
    headers: &[],
    verify: Verify::GetJson("/models"),
},
```

After that row exists, `foe login example` works, `{ "provider": "example",
"model": "example-large" }` runs, and the credential lives at
`~/.config/foe/credentials/example.json`. A provider with a new wire format
or a new credential source needs a module implementing the `Format` or
`Auth` trait beside the existing ones, and a feature gating it.

## What each provider cannot express

The chunk vocabulary of [protocol.md](protocol.md) has three stop reasons
and no notion of a refusal. Each provider maps onto it with these losses.

| provider | limit |
|---|---|
| `anthropic` | a `refusal` stop reason and any unknown stop reason become a non-retryable error |
| `openai`, `openai-codex` | a `content_filter` incompletion and a refusal part become non-retryable errors; a failed response without a code is retried unless its message starts with a structured error code; reasoning is replayed only for items that arrived with `encrypted_content`, which every request asks for with `store: false` |
| `openai-compatible`, `openrouter` | a `content_filter` finish becomes a non-retryable error; a failed tool result has no field and travels as text; reasoning blocks are never replayed, because the API has no item for them; the reasoning stream fields are a convention of DeepSeek, vLLM, and llama.cpp rather than part of the specification |
| `vertex` with Gemini | `SAFETY`, `RECITATION`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `MALFORMED_FUNCTION_CALL`, and a blocked prompt become non-retryable errors; function calls have no ids, so the transport numbers them per response and results are matched by function name; a thought signature is replayed on a part of the kind it arrived on, and a signature whose part has no counterpart in the replayed turn is dropped; schema keywords the API rejects, `additionalProperties` and every `$`-prefixed keyword, are removed from tool declarations |
| `vertex` with Claude | as `anthropic` |
| `exec` | chunks arrive after the program exits rather than as it writes them |

Every provider replays reasoning only to the route that produced it. The
runtime fixes the model for the whole episode, so every block in a log came
from the route that will read it.
