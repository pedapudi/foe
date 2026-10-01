#!/usr/bin/python3
"""Tests of the container environment's definition files, without running docker.

The tests read the Dockerfiles and scripts as text and check the pins, the
security options of the printed run recipe, and that no script or image
takes its configuration from an environment variable. build.sh runs as far
as its argument checks, which need no docker.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
DOCKERFILE = HERE / "Dockerfile"
SINK_DOCKERFILE = HERE / "sink" / "Dockerfile"
RECORDER = HERE / "sink" / "recorder.py"
BUILD = HERE / "build.sh"
DESCRIPTION = HERE / "environment.md"

BASE_IMAGE = re.compile(r"^ARG BASE_IMAGE=ubuntu:24\.04@sha256:([0-9a-f]{64})$", re.M)
CODEX_VERSION = "0.153.4"
RUSTUP_VERSION = "1.29.1"
BUILD_MOUNT = "/home/attempt/.local/state/foe/cross-harness/build"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def shell_variables(script: str) -> tuple[set[str], set[str]]:
    """The names a shell script assigns and the names it expands."""
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assigned = set(re.findall(r"(?<![\w$.-])([A-Za-z_][A-Za-z0-9_]*)=", code))
    assigned |= set(re.findall(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b", code))
    assigned |= set(re.findall(r"\bread\s+(?:-r\s+)?([A-Za-z_][A-Za-z0-9_]*)", code))
    expanded = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", code))
    return assigned, expanded


class Pins(unittest.TestCase):
    def test_both_images_pin_the_same_base_by_digest(self) -> None:
        attempt = BASE_IMAGE.search(text(DOCKERFILE))
        sink = BASE_IMAGE.search(text(SINK_DOCKERFILE))
        self.assertIsNotNone(attempt, "the attempt Dockerfile pins ubuntu:24.04 by digest")
        self.assertIsNotNone(sink, "the sink Dockerfile pins ubuntu:24.04 by digest")
        assert attempt is not None and sink is not None
        self.assertEqual(attempt.group(1), sink.group(1))

    def test_rustup_installer_is_pinned_by_version_and_checksum(self) -> None:
        dockerfile = text(DOCKERFILE)
        self.assertIn(f"ARG RUSTUP_VERSION={RUSTUP_VERSION}\n", dockerfile)
        self.assertRegex(dockerfile, r"(?m)^ARG RUSTUP_INIT_SHA256=[0-9a-f]{64}$")
        self.assertIn("rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init", dockerfile)
        self.assertIn("sha256sum -c", dockerfile)

    def test_toolchain_comes_from_the_repository_toolchain_file(self) -> None:
        dockerfile = text(DOCKERFILE)
        self.assertIn("COPY rust-toolchain.toml /build/rust-toolchain.toml", dockerfile)
        self.assertIn("rustup toolchain install", dockerfile)
        self.assertIn('cp "$repo/rust-toolchain.toml" "$context/rust-toolchain.toml"', text(BUILD))
        self.assertRegex(text(REPO / "rust-toolchain.toml"), r'(?m)^channel = "[^"]+"$')
        for component in ("rustfmt", "clippy"):
            self.assertIn(component, text(REPO / "rust-toolchain.toml"))

    def test_toolchain_is_installed_under_usr_local(self) -> None:
        dockerfile = text(DOCKERFILE)
        for tree in ("bin", "lib", "libexec"):
            self.assertIn(f"COPY --from=toolchain /build/prefix/{tree}/ /usr/local/{tree}/", dockerfile)

    def test_codex_version_is_pinned_in_the_image_and_checked_by_the_build(self) -> None:
        self.assertIn(f"ARG CODEX_VERSION={CODEX_VERSION}\n", text(DOCKERFILE))
        self.assertIn('grep -qx "codex-cli ${CODEX_VERSION}" /opt/codex/version', text(DOCKERFILE))
        self.assertIn(f"CODEX_VERSION={CODEX_VERSION}\n", text(BUILD))
        self.assertIn('[ "$codex_version" = "codex-cli $CODEX_VERSION" ] || fail', text(BUILD))

    def test_python_is_312(self) -> None:
        dockerfile = text(DOCKERFILE)
        self.assertIn(r"grep -q '^Python 3\.12\.'", dockerfile)
        self.assertIn("sys.version_info[:2] == (3, 12)", dockerfile)

    def test_image_holds_the_tools_the_attempt_needs(self) -> None:
        dockerfile = text(DOCKERFILE)
        for package in ("python3", "git", "bubblewrap", "gcc", "libc6-dev"):
            self.assertRegex(dockerfile, rf"apt-get install[^\n]*(\\\n[^\n]*)*\b{package}\b")

    def test_foe_binary_origin_and_digest_are_recorded(self) -> None:
        dockerfile = text(DOCKERFILE)
        self.assertIn("> /opt/foe/sha256", dockerfile)
        self.assertIn("> /opt/foe/origin", dockerfile)
        build = text(BUILD)
        self.assertLess(build.index("bazel-bin/crates/cli/foe-portable"), build.index("target/debug/foe"))
        self.assertIn('--build-arg "FOE_ORIGIN=$foe_origin"', build)

    def test_registry_is_vendored_and_selected_offline(self) -> None:
        self.assertIn('"$cargo" vendor --locked --versioned-dirs', text(BUILD))
        self.assertIn("COPY vendor/ /usr/share/cargo/vendor/", text(DOCKERFILE))
        self.assertIn("ln -s /usr/share/cargo/home /.cargo", text(DOCKERFILE))
        config = text(HERE / "cargo-config.toml")
        self.assertIn('directory = "/usr/share/cargo/vendor"', config)
        self.assertIn("offline = true", config)


class NoEnvironmentConfiguration(unittest.TestCase):
    def test_dockerfiles_carry_no_env_instruction(self) -> None:
        for path in (DOCKERFILE, SINK_DOCKERFILE):
            self.assertNotRegex(text(path), r"^\s*ENV\b", f"{path} sets an environment variable")

    def test_rustup_variables_appear_only_on_rustup_commands(self) -> None:
        for line in text(DOCKERFILE).splitlines():
            if "RUSTUP_HOME=" in line or "CARGO_HOME=" in line:
                self.assertIn("rustup", line, f"the line {line!r} sets a rustup variable for a command other than rustup")

    def test_build_script_expands_only_names_it_assigns(self) -> None:
        assigned, expanded = shell_variables(text(BUILD))
        self.assertEqual(expanded - assigned, set(), f"{BUILD} expands names it never assigns")

    def test_build_script_and_recorder_read_no_environment(self) -> None:
        self.assertNotIn("printenv", text(BUILD))
        self.assertNotIn("$HOME", text(BUILD))
        self.assertIn('home=$(getent passwd "$(id -u)" | cut -d: -f6)', text(BUILD))
        self.assertNotIn("os.environ", text(RECORDER))
        self.assertNotIn("getenv", text(RECORDER))


class RunRecipe(unittest.TestCase):
    def test_attempt_carries_the_sandbox_options(self) -> None:
        build = text(BUILD)
        attempt = build[build.index("--name foe-cross-harness-attempt") :]
        for option in (
            "--security-opt seccomp=unconfined",
            "--security-opt apparmor=unconfined",
            "--cap-drop ALL",
            "--network $NETWORK",
            "--dns $SINK_ADDRESS",
            "/work/foe:ro",
            "/work/run.json:ro",
            "--confirm-spend",
        ):
            self.assertIn(option, attempt)

    def test_network_has_no_host_gateway(self) -> None:
        build = text(BUILD)
        self.assertIn("-o com.docker.network.bridge.inhibit_ipv4=true $NETWORK", build)
        self.assertIn("--gateway $GATEWAY", build)

    def test_sink_takes_the_gateway_address_with_net_admin(self) -> None:
        build = text(BUILD)
        sink = build[build.index("--name foe-cross-harness-sink") :]
        self.assertIn("--cap-add NET_ADMIN", sink)
        self.assertIn("--ip $SINK_ADDRESS", sink)
        self.assertIn("--hold $GATEWAY/${SUBNET#*/}", sink)

    def test_sink_redirects_every_port_of_both_protocols(self) -> None:
        recorder = text(RECORDER)
        for protocol in ("tcp", "udp"):
            self.assertRegex(recorder, rf'"-p", "{protocol}", "-j", "REDIRECT", "--to-ports"')
        self.assertNotIn("--dport", recorder)

    def test_build_volume_is_warmed_offline_per_task_in_the_graders_layout(self) -> None:
        build = text(BUILD)
        self.assertIn("BUILD_VOLUME=foe-cross-harness-build\n", build)
        self.assertIn(f"BUILD_MOUNT={BUILD_MOUNT}\n", build)
        warm = build[build.index("docker volume create") : build.index("cat <<RECIPE")]
        self.assertIn("--network none", warm)
        self.assertIn("cargo test --workspace --no-run --locked --target-dir $BUILD_MOUNT/$task/target", warm)
        self.assertIn("cargo clippy --workspace --locked --target-dir $BUILD_MOUNT/$task/target", warm)
        attempt = build[build.index("--name foe-cross-harness-attempt") :]
        self.assertIn("-v $BUILD_VOLUME:$BUILD_MOUNT", attempt)
        self.assertNotIn("first_short", build)
        self.assertNotIn("cut -c2-", build)

    def test_image_holds_the_build_directory_the_volume_mounts(self) -> None:
        self.assertIn(f"mkdir -p {BUILD_MOUNT} /work", text(DOCKERFILE))
        self.assertIn("chown -R attempt:attempt /home/attempt/.local /work", text(DOCKERFILE))
        self.assertIn(BUILD_MOUNT, text(DESCRIPTION))

    def test_recipe_states_the_document_keys_the_container_needs(self) -> None:
        recipe = text(BUILD)[text(BUILD).index("cat <<RECIPE") :]
        for statement in ("omits tool_roots", "a document without tool_roots runs the\n   foe-as-shipped arm on every task"):
            self.assertIn(statement, recipe)
        description = text(DESCRIPTION)
        self.assertIn("omits `tool_roots`", description)
        self.assertIn("a document without\n`tool_roots` runs that arm on every task", description)
        self.assertNotIn("as_shipped_toolchain_on_path", recipe + description)

    def test_binaries_under_usr_local_bin_are_hard_links(self) -> None:
        dockerfile = text(DOCKERFILE)
        self.assertIn("ln /opt/foe/foe /usr/local/bin/foe", dockerfile)
        self.assertIn("ln /opt/codex/codex /usr/local/bin/codex", dockerfile)
        self.assertNotIn("ln -s /opt/", dockerfile)
        self.assertIn("hard link", text(DESCRIPTION))


class Files(unittest.TestCase):
    def test_scripts_are_executable_and_parse(self) -> None:
        self.assertTrue(BUILD.stat().st_mode & 0o111, f"{BUILD} is not executable")
        self.assertEqual(text(BUILD).splitlines()[0], "#!/bin/sh")
        completed = subprocess.run(["/bin/sh", "-n", str(BUILD)], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(RECORDER.stat().st_mode & 0o111)
        self.assertEqual(text(RECORDER).splitlines()[0], "#!/usr/bin/python3")

    def test_description_names_the_recorded_files(self) -> None:
        description = text(DESCRIPTION)
        for name in ("/opt/foe/sha256", "/opt/foe/origin", "/opt/codex/version", "/usr/share/cargo/vendor", "inhibit_ipv4"):
            self.assertIn(name, description)
        self.assertNotRegex(description, r"\b(?:n't|'re|'ll|'ve|'d)\b", "the description holds a contraction")


def executable(path: Path, output: str) -> None:
    """A shell script at `path` that prints `output` to whatever it is asked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    path.chmod(0o755)


class BuildArguments(unittest.TestCase):
    """build.sh as far as its argument checks, which need no docker."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="build-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(BUILD), *arguments], capture_output=True, text=True, check=False)

    def foe_binary_present(self) -> bool:
        return (REPO / "bazel-bin" / "crates" / "cli" / "foe-portable").is_file() or (REPO / "target" / "debug" / "foe").is_file()

    def test_help_prints_the_header(self) -> None:
        completed = self.run_script("--help")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith("Build the attempt image"), completed.stdout[:80])
        self.assertIn("--no-warm", completed.stdout)

    def test_unknown_argument_is_named(self) -> None:
        completed = self.run_script("--bogus")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("unknown argument --bogus", completed.stderr)

    def test_repo_must_be_a_checkout(self) -> None:
        completed = self.run_script("--repo", str(self.root))
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f"--repo names {self.root}, which is not a git checkout", completed.stderr)

    def test_tasks_must_be_a_directory(self) -> None:
        completed = self.run_script("--repo", str(REPO), "--tasks", str(self.root / "absent"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f"--tasks names {self.root / 'absent'}, which is not a directory", completed.stderr)

    def test_codex_must_report_the_pinned_version(self) -> None:
        if not self.foe_binary_present():
            self.skipTest("neither bazel-bin/crates/cli/foe-portable nor target/debug/foe is built")
        codex = self.root / "codex"
        executable(codex, "codex-cli 0.0.0")
        completed = self.run_script("--repo", str(REPO), "--codex", str(codex))
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f"{codex} reports 'codex-cli 0.0.0'; the image pins codex-cli {CODEX_VERSION}", completed.stderr)

    def test_cargo_must_be_executable(self) -> None:
        if not self.foe_binary_present():
            self.skipTest("neither bazel-bin/crates/cli/foe-portable nor target/debug/foe is built")
        codex = self.root / "codex"
        executable(codex, f"codex-cli {CODEX_VERSION}")
        completed = self.run_script("--repo", str(REPO), "--codex", str(codex), "--cargo", str(self.root / "cargo"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f"--cargo names {self.root / 'cargo'}, which is not an executable file", completed.stderr)


if __name__ == "__main__":
    sys.exit(unittest.main())
