#!/bin/sh
set -u
PATH=/usr/bin:/bin
export PATH

source=/app/gpt2.c
if test ! -f "$source"; then
    echo "/app/gpt2.c does not exist"
    exit 0
fi
size=$(/usr/bin/wc -c < "$source")
if test "$size" -ge 5000; then
    echo "/app/gpt2.c is $size bytes; it must be smaller than 5000 bytes"
    exit 0
fi
directory=$(/usr/bin/mktemp -d /tmp/foe-gpt2-check.XXXXXX) || exit 1
trap '/bin/rm -rf "$directory"' EXIT HUP INT TERM
if ! /usr/bin/gcc -O3 "$source" -lm -o "$directory/gpt2" 2> "$directory/compile.stderr"; then
    echo "/app/gpt2.c does not compile:"
    /bin/cat "$directory/compile.stderr"
    exit 0
fi

check_continuation() {
    index=$1
    prompt=$2
    expected=$3
    /usr/bin/timeout 90 "$directory/gpt2" \
        /app/gpt2-124M.ckpt /app/vocab.bpe "$prompt" \
        > "$directory/observed" 2> "$directory/run.stderr"
    status=$?
    if test "$status" -ne 0; then
        detail=$(/bin/cat "$directory/run.stderr")
        echo "public continuation $index exited $status: ${detail:-no diagnostics}"
        return
    fi
    observed=$(/bin/cat "$directory/observed")
    if test "$observed" != "$expected"; then
        echo "public continuation $index differed from the expected arg-max output: observed $observed"
    fi
}

check_continuation 1 \
    'Hello, I am a language model' \
    'Hello, I am a language modeler. I am a programmer. I am a writer. I am a writer. I am a'
check_continuation 2 \
    'The meaning of life is' \
    'The meaning of life is not the same as the meaning of death.

The meaning of life is not the same as'
check_continuation 3 \
    'Once upon a time' \
    'Once upon a time, the world was a place of great beauty and great danger. The world was a place of great'
