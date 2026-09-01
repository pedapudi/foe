#!/bin/sh
set -eu

/usr/bin/perl -0pi -e '
    s/\bcurious\b/inquisitive/g;
    s/\briotous\b/wild/g;
    s/\bresponsiveness\b/awareness/g;
    s/\bcreative\b/imaginative/g;
    s/\bweatherbeaten\b/worn/g;
    s/\bpathfinder\b/pioneer/g;
' /app/input.tex

cd /app
/usr/bin/pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
