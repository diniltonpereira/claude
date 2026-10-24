#!/usr/bin/env bash
# Provisiona uma VPS Ubuntu 22.04/24.04 nova para a Cerne. Rode como root, uma vez:
#
#   curl -fsSLO https://raw.githubusercontent.com/<dono>/<repo>/<branch>/cerne/deploy/vps/provisionar.sh
#   CERNE_DOMAIN=cerne.seudominio.com.br ACME_EMAIL=voce@seudominio.com.br bash provisionar.sh
#
# O que faz (idempotente: pode rodar de novo sem estragar nada):
#   1. atualiza o sistema, fuso America/Sao_Paulo, swap de 2 GB se a máquina tiver pouca RAM
#   2. cria o usuário de operação (padrão "cerne") com sudo e a mesma chave SSH do root
#   3. SSH só por chave e sem login de root (somente se a chave já estiver instalada)
#   4. firewall UFW (22, 80, 443), fail2ban e atualizações automáticas de segurança
#   5. Docker Engine + Compose (repositório oficial) com rotação de logs
#   6. clona o repositório em /opt/cerne, gera o .env com segredos aleatórios e sobe a pilha
set -euo pipefail

USUARIO="${USUARIO:-cerne}"
REPO_URL="${REPO_URL:-https://github.com/diniltonpereira/claude.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
DIR="${DIR:-/opt/cerne}"
SSH_PORT="${SSH_PORT:-22}"
SUBIR="${SUBIR:-sim}"          # "nao" para só preparar a máquina

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Rode como root." >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || { echo "Script testado só em Ubuntu (encontrado: ${ID:-?})." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

log "1/6 Sistema"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
apt-get -y -q install ca-certificates curl git gnupg ufw fail2ban unattended-upgrades openssl jq
timedatectl set-timezone America/Sao_Paulo || true
if ! swapon --show | grep -q . && [ "$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)" -lt 4096 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null && echo 'vm.swappiness=10' > /etc/sysctl.d/99-cerne.conf
fi

log "2/6 Usuário de operação: $USUARIO"
id "$USUARIO" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$USUARIO"
usermod -aG sudo "$USUARIO"
echo "$USUARIO ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$USUARIO" && chmod 440 "/etc/sudoers.d/90-$USUARIO"
HOME_U="$(getent passwd "$USUARIO" | cut -d: -f6)"
install -d -m 700 -o "$USUARIO" -g "$USUARIO" "$HOME_U/.ssh"
if [ -s /root/.ssh/authorized_keys ]; then
  touch "$HOME_U/.ssh/authorized_keys"
  sort -u /root/.ssh/authorized_keys "$HOME_U/.ssh/authorized_keys" -o "$HOME_U/.ssh/authorized_keys"
  chown "$USUARIO:$USUARIO" "$HOME_U/.ssh/authorized_keys"; chmod 600 "$HOME_U/.ssh/authorized_keys"
fi

log "3/6 SSH"
if [ -s "$HOME_U/.ssh/authorized_keys" ]; then
  cat > /etc/ssh/sshd_config.d/99-cerne.conf <<CONF
Port $SSH_PORT
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
AllowUsers $USUARIO
CONF
  sshd -t && (systemctl reload ssh 2>/dev/null || systemctl reload sshd)
  echo "SSH endurecido: entre com  ssh -p $SSH_PORT $USUARIO@<ip>  (root e senha desativados)."
else
  echo "AVISO: nenhuma chave SSH em /root/.ssh/authorized_keys. Login por senha MANTIDO."
  echo "       Instale sua chave (ssh-copy-id root@<ip>) e rode este script de novo."
fi

log "4/6 Firewall, fail2ban e atualizações automáticas"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
cat > /etc/fail2ban/jail.d/cerne.local <<CONF
[sshd]
enabled = true
port = $SSH_PORT
maxretry = 5
findtime = 10m
bantime = 1h
CONF
systemctl enable --now fail2ban >/dev/null && systemctl restart fail2ban
cat > /etc/apt/apt.conf.d/20auto-upgrades <<CONF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF

log "5/6 Docker"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || cat > /etc/docker/daemon.json <<CONF
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" }, "live-restore": true }
CONF
systemctl enable --now docker >/dev/null && systemctl restart docker
usermod -aG docker "$USUARIO"

log "6/6 Aplicação em $DIR"
if [ ! -d "$DIR/.git" ]; then
  install -d -o "$USUARIO" -g "$USUARIO" "$DIR"
  if ! sudo -u "$USUARIO" git clone --branch "$REPO_BRANCH" --filter=blob:none --sparse "$REPO_URL" "$DIR"; then
    echo "Falha ao clonar $REPO_URL. Se o repositório for privado, crie uma deploy key:" >&2
    echo "  sudo -u $USUARIO ssh-keygen -t ed25519 -N '' -f $HOME_U/.ssh/id_ed25519" >&2
    echo "  (cadastre $HOME_U/.ssh/id_ed25519.pub em Settings > Deploy keys do repositório, só leitura)" >&2
    echo "  e rode de novo com REPO_URL=git@github.com:<dono>/<repo>.git" >&2
    exit 1
  fi
  sudo -u "$USUARIO" git -C "$DIR" sparse-checkout set cerne
fi
ENVF="$DIR/cerne/deploy/.env"
if [ ! -f "$ENVF" ]; then
  : "${CERNE_DOMAIN:?informe CERNE_DOMAIN=... (o DNS deve apontar para este servidor)}"
  : "${ACME_EMAIL:?informe ACME_EMAIL=... (avisos do certificado HTTPS)}"
  sed -e "s|^CERNE_DOMAIN=.*|CERNE_DOMAIN=$CERNE_DOMAIN|" \
      -e "s|^ACME_EMAIL=.*|ACME_EMAIL=$ACME_EMAIL|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" \
      -e "s|^ADMIN_TOKEN=.*|ADMIN_TOKEN=$(openssl rand -hex 32)|" \
      -e "s|^CERNE_MASTER_KEY=.*|CERNE_MASTER_KEY=$(openssl rand 32 | base64 | tr '+/' '-_')|" \
      "$DIR/cerne/deploy/.env.example" > "$ENVF"
  chown "$USUARIO:$USUARIO" "$ENVF"; chmod 600 "$ENVF"
  echo "Gerado $ENVF com segredos aleatórios."
fi
install -d -o "$USUARIO" -g "$USUARIO" "$DIR/cerne/deploy/backups"

if [ "$SUBIR" = "sim" ]; then
  sudo -u "$USUARIO" bash "$DIR/cerne/deploy/deploy.sh" --sem-pull
fi

IP="$(curl -fsS4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
cat <<FIM

======================================================================
 VPS pronta.  IP: $IP
 - Painel / API:   https://$(grep ^CERNE_DOMAIN= "$ENVF" | cut -d= -f2)
 - Operação:       ssh -p $SSH_PORT $USUARIO@$IP   (pasta $DIR/cerne/deploy)
 - Cadastrar provedor:  bash $DIR/cerne/deploy/criar-provedor.sh "Razão Social" 00.000.000/0001-00

 IMPORTANTE: copie o arquivo $ENVF para um cofre FORA da VPS.
 Sem a CERNE_MASTER_KEY as credenciais de ERP cifradas não podem ser lidas.
======================================================================
FIM
