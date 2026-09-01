#!/bin/sh
set -eu

/usr/bin/apt-get update -qq
/usr/bin/apt-get install -y -qq primer3
/usr/bin/test -x /usr/bin/oligotm
