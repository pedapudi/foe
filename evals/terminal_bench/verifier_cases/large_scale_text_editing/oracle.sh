#!/bin/sh
set -eu

/bin/cat > /app/apply_macros.vim <<'VIM'
call setreg('a', "gUU")
call setreg('b', ":s/\\s//ge\r")
call setreg('c', ":s/\\([^,]*\\),\\([^,]*\\),\\(.*\\)/\\3;\\2;\\1;OK/e\r")
:%normal! @a
:%normal! @b
:%normal! @c
:wq
VIM
