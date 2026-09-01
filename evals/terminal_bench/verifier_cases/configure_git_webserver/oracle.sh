#!/bin/sh
set -eu

DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get update -qq
DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get install -y -qq \
    git nginx openssh-server >/dev/null

/usr/sbin/useradd --create-home --shell /bin/bash user
/usr/bin/mkdir -p /git /var/www/html /run/sshd
/usr/bin/git init --bare --initial-branch=master -q /git/server
/usr/bin/chown -R user:user /git /var/www/html

/bin/cat > /git/server/hooks/post-receive <<'HOOK'
#!/bin/sh
set -eu
/usr/bin/git --git-dir=/git/server --work-tree=/var/www/html checkout -f master
HOOK
/usr/bin/chmod 755 /git/server/hooks/post-receive
/usr/bin/chown user:user /git/server/hooks/post-receive

/bin/cat > /etc/ssh/sshd_config.d/foe-git-server.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers user
SSHD
/usr/bin/ssh-keygen -A
/usr/sbin/sshd

/bin/cat > /etc/nginx/sites-enabled/default <<'NGINX'
server {
    listen 8080;
    server_name localhost;
    root /var/www/html;
}
NGINX
/usr/sbin/nginx -t
/usr/sbin/nginx
