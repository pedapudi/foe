#!/bin/sh
set -u

finding() {
    printf '%s\n' "$1"
    exit 0
}

for executable in \
    /usr/bin/curl /usr/bin/git /usr/bin/id /usr/bin/install /usr/bin/mktemp \
    /usr/bin/ssh /usr/bin/ssh-keygen
do
    test -x "$executable" || finding "$executable is unavailable"
done

/usr/bin/id user >/dev/null 2>&1 || finding "the SSH user named user does not exist"

temporary=$(/usr/bin/mktemp -d /tmp/foe-configure-git-webserver.XXXXXX) ||
    finding "could not create the public checker's temporary directory"
trap '/usr/bin/rm -rf "$temporary"' EXIT HUP INT TERM

if ! detail=$(/usr/bin/ssh-keygen -q -t ed25519 -N '' -f "$temporary/key" 2>&1)
then
    finding "could not create the public SSH key: $detail"
fi
if ! detail=$(/usr/bin/install -d -m 700 -o user -g user /home/user/.ssh 2>&1)
then
    finding "could not prepare user SSH authorization: $detail"
fi
if ! detail=$(/usr/bin/install -m 600 -o user -g user \
    "$temporary/key.pub" /home/user/.ssh/authorized_keys 2>&1)
then
    finding "could not authorize the public SSH key: $detail"
fi

export GIT_SSH_COMMAND="/usr/bin/ssh -F /dev/null -i $temporary/key -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
export GIT_TERMINAL_PROMPT=0
export HOME="$temporary"

client="$temporary/client"
if ! detail=$(/usr/bin/git clone -q user@127.0.0.1:/git/server "$client" 2>&1)
then
    finding "the public Git clone failed: $detail"
fi
printf '%s\n' 'foe public completion probe' > "$client/hello.html" ||
    finding "could not write the public probe content"

if ! detail=$(
    (
        cd "$client" && \
        /usr/bin/git add hello.html && \
        /usr/bin/git -c user.name='Foe completion checker' \
            -c user.email='foe-checker@invalid' commit -q --allow-empty \
            -m 'public completion probe' && \
        /usr/bin/git push -q origin HEAD:master
    ) 2>&1
)
then
    finding "the public Git push failed: $detail"
fi

if ! content=$(/usr/bin/curl --fail --silent --show-error \
    --retry 5 --retry-delay 1 http://127.0.0.1:8080/hello.html 2>&1)
then
    finding "the public HTTP probe failed: $content"
fi
test "$content" = 'foe public completion probe' ||
    finding "the public HTTP endpoint did not serve the pushed content"

exit 0
