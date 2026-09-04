//! The provider table: every name a `model` block may give, with the wire
//! format, the credential source, and the defaults that name implies.
//!
//! A row exists only when the features of both its format and its
//! credential source are enabled, so `known_providers()` describes the
//! build that is running. Adding a provider that speaks an existing format
//! with an existing credential source is one row here and nothing else.

use crate::auth::AuthKind;
#[cfg(feature = "api-key")]
use crate::auth::KeyHeader;

/// The wire format a row speaks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WireFormat {
    /// Anthropic Messages API.
    #[cfg(feature = "messages")]
    Messages,
    /// OpenAI Chat Completions API, and the servers and proxies that imitate it.
    #[cfg(feature = "chat")]
    Chat,
    /// OpenAI Responses API.
    #[cfg(feature = "responses")]
    Responses,
    /// Vertex AI: Messages for model names starting with `claude`, Gemini
    /// otherwise.
    #[cfg(feature = "google")]
    VertexByModel,
}

impl WireFormat {
    pub fn name(&self) -> &'static str {
        match *self {
            #[cfg(feature = "messages")]
            WireFormat::Messages => "messages",
            #[cfg(feature = "chat")]
            WireFormat::Chat => "chat",
            #[cfg(feature = "responses")]
            WireFormat::Responses => "responses",
            #[cfg(feature = "google")]
            WireFormat::VertexByModel => "messages or gemini, by model name",
        }
    }
}

/// How `foe login` proves a credential works.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verify {
    /// GET this path under the base URL with the credential; 2xx passes.
    GetJson(&'static str),
    /// Minting an access token is the proof.
    MintToken,
    None,
}

#[derive(Debug)]
pub struct Provider {
    pub name: &'static str,
    /// The provider's own name for itself, used in prompts.
    pub title: &'static str,
    /// One line for `foe login`.
    pub description: &'static str,
    pub format: WireFormat,
    pub auth: AuthKind,
    pub default_base_url: Option<&'static str>,
    /// Appended to the base URL.
    pub path: &'static str,
    /// Options the `model` block must carry, with the hint printed when
    /// one is missing.
    pub required: &'static [(&'static str, &'static str)],
    /// Model names `foe login` offers.
    pub presets: &'static [&'static str],
    /// Context windows in tokens by model-name prefix. The longest prefix
    /// that matches a model name wins; a name no prefix matches is unknown.
    pub windows: &'static [(&'static str, u64)],
    /// Sent with every request, before the credential headers.
    pub headers: &'static [(&'static str, &'static str)],
    pub verify: Verify,
}

impl Provider {
    /// Keys of the required options.
    pub fn required_keys(&self) -> impl Iterator<Item = &'static str> + '_ {
        self.required.iter().map(|(key, _)| *key)
    }

    pub fn hint(&self, key: &str) -> &'static str {
        self.required.iter().find(|(k, _)| *k == key).map(|(_, hint)| *hint).unwrap_or("")
    }

    /// The context window of `model` in tokens, when the table knows it.
    pub fn context_window(&self, model: &str) -> Option<u64> {
        let known = self.windows.iter().filter(|(prefix, _)| model.starts_with(prefix));
        known.max_by_key(|(prefix, _)| prefix.len()).map(|(_, window)| *window)
    }
}

const CLAUDE: u64 = 200_000;
const GPT5: u64 = 400_000;
const GEMINI_25: u64 = 1_048_576;

pub static PROVIDERS: &[Provider] = &[
    #[cfg(all(feature = "messages", feature = "api-key"))]
    Provider {
        name: "anthropic",
        title: "Anthropic",
        description: "Anthropic's API with an API key",
        format: WireFormat::Messages,
        auth: AuthKind::ApiKey { header: KeyHeader::XApiKey },
        default_base_url: Some("https://api.anthropic.com"),
        path: "/v1/messages",
        required: &[],
        presets: &["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
        windows: &[("claude-", CLAUDE)],
        headers: &[("anthropic-version", "2023-06-01")],
        verify: Verify::GetJson("/v1/models"),
    },
    #[cfg(all(feature = "responses", feature = "api-key"))]
    Provider {
        name: "openai",
        title: "OpenAI",
        description: "OpenAI's API with an API key, over the Responses API",
        format: WireFormat::Responses,
        auth: AuthKind::ApiKey { header: KeyHeader::Bearer },
        default_base_url: Some("https://api.openai.com/v1"),
        path: "/responses",
        required: &[],
        presets: &["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        windows: &[("gpt-5.6", 1_050_000), ("gpt-5", GPT5)],
        headers: &[],
        verify: Verify::GetJson("/models"),
    },
    #[cfg(all(feature = "chat", feature = "api-key"))]
    Provider {
        name: "compatible-http",
        title: "Compatible HTTP endpoint",
        description: "any server speaking the streaming chat-completion format",
        format: WireFormat::Chat,
        auth: AuthKind::ApiKey { header: KeyHeader::Bearer },
        default_base_url: None,
        path: "/chat/completions",
        required: &[("base_url", "the server's origin and version prefix, for example http://127.0.0.1:11434/v1")],
        presets: &[],
        windows: &[],
        headers: &[],
        verify: Verify::GetJson("/models"),
    },
    #[cfg(all(feature = "chat", feature = "api-key"))]
    Provider {
        name: "openrouter",
        title: "OpenRouter",
        description: "OpenRouter, one key for many models, over the Chat Completions API",
        format: WireFormat::Chat,
        auth: AuthKind::ApiKey { header: KeyHeader::Bearer },
        default_base_url: Some("https://openrouter.ai/api/v1"),
        path: "/chat/completions",
        required: &[],
        presets: &["anthropic/claude-opus-5", "openai/gpt-5", "google/gemini-2.5-pro"],
        windows: &[("anthropic/claude-", CLAUDE), ("openai/gpt-5", GPT5), ("google/gemini-2.5", GEMINI_25)],
        // OpenRouter attributes traffic to the application named here.
        headers: &[("HTTP-Referer", "https://github.com/pedapudi/foe"), ("X-Title", "foe")],
        verify: Verify::GetJson("/key"),
    },
    #[cfg(all(feature = "responses", feature = "token-file"))]
    Provider {
        name: "openai-codex",
        title: "OpenAI Codex",
        description: "a ChatGPT subscription through the Codex backend, logged in with the browser",
        format: WireFormat::Responses,
        auth: AuthKind::TokenFile {
            account_header: Some("chatgpt-account-id"),
            token_url: crate::auth::token_file::codex::TOKEN_URL,
            client_id: crate::auth::token_file::codex::CLIENT_ID,
        },
        default_base_url: Some("https://chatgpt.com/backend-api"),
        path: "/codex/responses",
        required: &[],
        presets: &["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        windows: &[("gpt-5.6", 1_050_000), ("gpt-5", GPT5)],
        headers: &[("originator", "foe"), ("OpenAI-Beta", "responses=experimental")],
        verify: Verify::None,
    },
    #[cfg(all(feature = "google", any(feature = "messages", feature = "gemini")))]
    Provider {
        name: "vertex",
        title: "Vertex AI",
        description: "Google Cloud Vertex AI with Google credentials: Gemini models, and Claude models by name",
        format: WireFormat::VertexByModel,
        auth: AuthKind::Google,
        default_base_url: None,
        path: "",
        required: &[
            ("project", "the Google Cloud project id"),
            ("location", "the region, for example us-east5 or global"),
        ],
        presets: &["gemini-2.5-pro", "gemini-2.5-flash", "claude-opus-5"],
        windows: &[("gemini-2.5", GEMINI_25), ("claude-", CLAUDE)],
        headers: &[],
        verify: Verify::MintToken,
    },
];

pub fn find(name: &str) -> Option<&'static Provider> {
    PROVIDERS.iter().find(|p| p.name == name)
}

pub fn names() -> Vec<&'static str> {
    PROVIDERS.iter().map(|p| p.name).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_unique_and_required_keys_have_hints() {
        let names = names();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), names.len());
        for provider in PROVIDERS {
            for key in provider.required_keys() {
                assert!(!provider.hint(key).is_empty(), "{}.{key}", provider.name);
            }
            if provider.auth.option_key().is_some() && provider.format != WireFormat::VertexByModel {
                assert!(provider.default_base_url.is_some() || provider.required_keys().any(|k| k == "base_url"));
            }
        }
        assert_eq!(find("anthropic").map(|p| p.path), Some("/v1/messages"));
        assert!(find("unknown").is_none());
    }

    #[test]
    fn context_windows_match_by_longest_prefix_and_cover_every_preset() {
        for provider in PROVIDERS {
            for preset in provider.presets {
                assert!(provider.context_window(preset).is_some(), "{}/{preset} has no context window", provider.name);
            }
            assert_eq!(provider.context_window("a-model-no-table-names"), None);
        }
        if let Some(vertex) = find("vertex") {
            assert_eq!(vertex.context_window("gemini-2.5-flash"), Some(GEMINI_25));
            assert_eq!(vertex.context_window("claude-sonnet-5"), Some(CLAUDE));
        }
        for name in ["openai", "openai-codex"] {
            let provider = find(name).unwrap();
            assert!(provider.presets.contains(&"gpt-5.6-sol"), "{}: the preset is offered", provider.name);
            assert_eq!(provider.context_window("gpt-5.6-sol"), Some(1_050_000));
        }
    }
}
