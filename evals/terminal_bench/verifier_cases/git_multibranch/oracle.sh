#!/bin/sh
set -eu

/usr/sbin/useradd --create-home --shell /usr/bin/git-shell git
printf '%s\n' 'git:password' | /usr/sbin/chpasswd

/usr/bin/mkdir -p /git /var/www/html /var/www/dev /run/sshd
/usr/bin/git init --bare --initial-branch=main -q /git/project
/bin/cat > /git/project/hooks/post-receive <<'HOOK'
#!/bin/sh
set -eu
while read -r old_revision new_revision reference; do
    case "$reference" in
        refs/heads/main) destination=/var/www/html/index.html ;;
        refs/heads/dev) destination=/var/www/dev/index.html ;;
        *) continue ;;
    esac
    temporary="${destination}.temporary.$$"
    /usr/bin/git --git-dir=/git/project show "${new_revision}:index.html" > "$temporary"
    /usr/bin/mv "$temporary" "$destination"
done
HOOK
/usr/bin/chmod 755 /git/project/hooks/post-receive
/usr/bin/chown -R git:git /git /var/www/html /var/www/dev

/bin/cat > /etc/ssh/sshd_config.d/foe-git-server.conf <<'SSHD'
PasswordAuthentication yes
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
AllowUsers git
SSHD
/usr/bin/ssh-keygen -A
/usr/sbin/sshd

/usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
    -subj '/CN=localhost' \
    -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
    -keyout /etc/ssl/private/foe-git-server.key \
    -out /etc/ssl/certs/foe-git-server.crt >/dev/null 2>&1
/bin/cat > /etc/nginx/sites-enabled/default <<'NGINX'
server {
    listen 8443 ssl;
    server_name localhost;
    ssl_certificate /etc/ssl/certs/foe-git-server.crt;
    ssl_certificate_key /etc/ssl/private/foe-git-server.key;

    location / {
        root /var/www/html;
    }

    location /dev/ {
        alias /var/www/dev/;
    }
}
NGINX
/usr/sbin/nginx -t
/usr/sbin/nginx
