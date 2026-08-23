"""Build one executable under an explicit target platform and toolchain."""


def _portable_transition_impl(_settings, attr):
    return {
        "//command_line_option:extra_toolchains": str(attr.toolchain),
        "//command_line_option:platforms": str(attr.platform),
    }


_portable_transition = transition(
    implementation = _portable_transition_impl,
    inputs = [],
    outputs = [
        "//command_line_option:extra_toolchains",
        "//command_line_option:platforms",
    ],
)


def _portable_binary_impl(ctx):
    binaries = ctx.attr.binary
    if type(binaries) == "list":
        if len(binaries) != 1:
            fail("binary transition must produce exactly one configured target")
        binary = binaries[0]
    else:
        binary = binaries
    source = binary[DefaultInfo].files_to_run.executable
    output = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.symlink(
        output = output,
        target_file = source,
        is_executable = True,
    )
    return DefaultInfo(
        executable = output,
        files = depset([output]),
        runfiles = ctx.runfiles(files = [output]),
    )


portable_binary = rule(
    implementation = _portable_binary_impl,
    executable = True,
    attrs = {
        "binary": attr.label(
            cfg = _portable_transition,
            executable = True,
            mandatory = True,
        ),
        "platform": attr.label(mandatory = True),
        "toolchain": attr.string(mandatory = True),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)
