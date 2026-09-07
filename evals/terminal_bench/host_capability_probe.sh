#!/bin/sh
# Host protocol driver for capability probes in minimal task images.
set -eu

if [ "$#" -ne 3 ]; then
    echo "usage: host_capability_probe.sh BINARY CONFIG LOG_DIR" >&2
    exit 2
fi

binary=$1
config=$2
log_dir=$3
# The run creates its own directory for the episode under the one --log-dir
# names and prints it. The probe retains that directory under the path its
# caller named, which is what the probe report reads.
log_parent="$log_dir.runs"
channel_dir="/tmp/foe-capability-host.$$"
to_runtime="$channel_dir/to-runtime"
from_runtime="$channel_dir/from-runtime"
foe_stderr="$channel_dir/foe.stderr"
mkdir "$channel_dir"
mkfifo "$to_runtime" "$from_runtime"

cleanup() {
    rm -f "$to_runtime" "$from_runtime" "$foe_stderr"
    rmdir "$channel_dir"
}
trap cleanup EXIT

"$binary" --config "$config" --host --log-dir "$log_parent" \
    <"$to_runtime" >"$from_runtime" 2>"$foe_stderr" &
runtime_pid=$!
exec 3>"$to_runtime"

request_id=
step=0

send_chunk() {
    printf '%s\n' "{\"type\":\"model/chunk\",\"request_id\":\"$request_id\",\"chunk\":$1}" >&3
}

start_call() {
    send_chunk "{\"kind\":\"tool_call_start\",\"id\":\"$1\",\"name\":\"$2\"}"
}

call_delta() {
    send_chunk "{\"kind\":\"tool_call_delta\",\"id\":\"$1\",\"delta\":$2}"
}

end_call() {
    send_chunk "{\"kind\":\"tool_call_end\",\"id\":\"$1\"}"
}

done_chunk() {
    send_chunk "{\"kind\":\"done\",\"stop\":\"$1\",\"usage\":{\"input\":0,\"output\":0,\"cache_read\":0}}"
}

while IFS= read -r event; do
    case "$event" in
        *'"type":"model/request"'*)
            request_id=$(printf '%s\n' "$event" | sed -n 's/.*"request_id":"\([^"]*\)".*/\1/p')
            case "$step" in
                0)
                    start_call probe_start bash
                    call_delta probe_start '"{\"command\":\"echo CWD=$PWD; echo UID=$(id -u); if command -v git >/dev/null && command -v sh >/dev/null; then echo STANDARD_PATH=available; else echo STANDARD_PATH=incomplete; fi; yes probe-marker | head -n 1000000 > /tmp/foe-capability-large.txt; sleep 300 >/tmp/foe-capability-background.log 2>&1 & echo $! > /tmp/foe-capability-background.pid; if command -v nc >/dev/null; then echo LOOPBACK_PROBE=available; else echo LOOPBACK_PROBE=unavailable; fi\",\"timeout_seconds\":120}"'
                    end_call probe_start
                    done_chunk tool
                    ;;
                1)
                    start_call probe_check bash
                    call_delta probe_check '"{\"command\":\"pid=$(cat /tmp/foe-capability-background.pid); if kill -0 $pid 2>/dev/null; then echo BACKGROUND=alive; else echo BACKGROUND=gone; fi; if command -v apt-get >/dev/null; then echo PACKAGE_MANAGER=apt-get; else echo PACKAGE_MANAGER=absent; fi\",\"timeout_seconds\":30}"'
                    end_call probe_check
                    done_chunk tool
                    ;;
                2)
                    start_call probe_large_grep grep
                    call_delta probe_large_grep '"{\"pattern\":\"probe-marker\",\"path\":\"/tmp/foe-capability-large.txt\",\"limit\":2}"'
                    end_call probe_large_grep
                    start_call probe_large_read read
                    call_delta probe_large_read '"{\"path\":\"/tmp/foe-capability-large.txt\",\"limit\":3}"'
                    end_call probe_large_read
                    done_chunk tool
                    ;;
                3)
                    start_call probe_timeout bash
                    call_delta probe_timeout '"{\"command\":\"sleep 2\",\"timeout_seconds\":1}"'
                    end_call probe_timeout
                    start_call probe_pty bash
                    call_delta probe_pty '"{\"command\":\"if test -t 0; then echo PTY=yes; else echo PTY=no; fi\"}"'
                    end_call probe_pty
                    done_chunk tool
                    ;;
                *)
                    send_chunk '{"kind":"text","delta":"Capability probes completed."}'
                    done_chunk end
                    ;;
            esac
            step=$((step + 1))
            ;;
        *'"type":"episode/end"'*)
            break
            ;;
    esac
done <"$from_runtime"

exec 3>&-
status=0
wait "$runtime_pid" || status=$?
cat "$foe_stderr" >&2
created=$(sed -n 's/^foe: log //p' "$foe_stderr" | head -n 1)
if [ -n "$created" ]; then
    mv "$created" "$log_dir"
    rmdir "$log_parent" 2>/dev/null || true
fi
exit "$status"
