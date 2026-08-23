#!/bin/sh
# Deterministic model chunks for capability probes in minimal task images.

request=$(cat)
step=$(printf '%s' "$request" | grep -o '"role":"assistant"' | wc -l)

start_call() {
    printf '%s\n' "{\"type\":\"model/chunk\",\"chunk\":{\"kind\":\"tool_call_start\",\"id\":\"$1\",\"name\":\"$2\"}}"
}

call_delta() {
    printf '%s\n' "{\"type\":\"model/chunk\",\"chunk\":{\"kind\":\"tool_call_delta\",\"id\":\"$1\",\"delta\":$2}}"
}

end_call() {
    printf '%s\n' "{\"type\":\"model/chunk\",\"chunk\":{\"kind\":\"tool_call_end\",\"id\":\"$1\"}}"
}

done_chunk() {
    printf '%s\n' "{\"type\":\"model/chunk\",\"chunk\":{\"kind\":\"done\",\"stop\":\"$1\",\"usage\":{\"input\":0,\"output\":0,\"cache_read\":0}}}"
}

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
        printf '%s\n' '{"type":"model/chunk","chunk":{"kind":"text","delta":"Capability probes completed."}}'
        done_chunk end
        ;;
esac
