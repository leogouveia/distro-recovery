#!/usr/bin/env bash
# Teste de ponta a ponta do `distro-recovery restore` numa VM Fedora descartável.
# QEMU + imagem oficial Fedora Cloud + cloud-init. Não usa sudo, nem libvirt, nem altera
# o seu sistema: tudo fica em ~/.cache/distro-recovery-vm. Veja VM_TEST.md.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RELEASE=44
MEMORY=4096
CPUS=2
DISK=20G
DATA="$REPO_ROOT/tests/vm/smoke"
KEEP=0
VERBOSE=1
KVM=auto
REFRESH=0
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/distro-recovery-vm"
SSH_TIMEOUT=300

usage() {
    cat <<USAGE
Uso: scripts/vm-test.sh [opções]

  --data DIR      Dados a restaurar: DIR/generated/fedora.yml e, opcional, DIR/decisions.yml.
                  Padrão: tests/vm/smoke (exemplo pequeno). Para testar o seu backup real:
                  --data ~/.config/distro-recovery  (só leitura; copia generated/ e decisions.yml)
  --release N     Versão do Fedora da VM (padrão: $RELEASE)
  --memory MB     Memória da VM (padrão: $MEMORY)
  --cpus N        CPUs da VM (padrão: $CPUS)
  --no-kvm        Não usa KVM (muito mais lento)
  --quiet, -q     Não mostra a saída dos comandos da VM (por padrão ela aparece numa caixa
                  de 10 linhas que rola no lugar, sem encher a tela)
  --keep          Mantém a VM ligada ao final e mostra como entrar (ssh) e desligar
  --refresh-image Baixa a imagem base de novo
  --cache-dir DIR Onde guardar a imagem baixada (padrão: $CACHE_DIR)
  -h, --help      Esta ajuda
USAGE
}

log() { printf '\033[1;34m[vm-test]\033[0m %s\n' "$*"; }
die() {
    printf '\033[1;31m[vm-test] erro:\033[0m %s\n' "$*" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --data)
            [ $# -ge 2 ] || die "--data precisa de um diretório"
            DATA=$(cd "$2" 2>/dev/null && pwd) || die "diretório não existe: $2"
            shift 2
            ;;
        --release) RELEASE=${2:?--release precisa de um valor}; shift 2 ;;
        --memory) MEMORY=${2:?--memory precisa de um valor}; shift 2 ;;
        --cpus) CPUS=${2:?--cpus precisa de um valor}; shift 2 ;;
        --cache-dir) CACHE_DIR=${2:?--cache-dir precisa de um valor}; shift 2 ;;
        --no-kvm) KVM=off; shift ;;
        --keep) KEEP=1; shift ;;
        --quiet | -q) VERBOSE=0; shift ;;
        --verbose | -v) VERBOSE=1; shift ;;  # já é o padrão; mantido por compatibilidade
        --refresh-image) REFRESH=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "opção desconhecida: $1" ;;
    esac
done

# ---------------------------------------------------------------- pré-requisitos
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' não encontrado. $2"; }
need qemu-system-x86_64 "Instale: sudo dnf install qemu-system-x86-core (ou qemu-kvm)"
need ssh "Instale o openssh-clients"
need scp "Instale o openssh-clients"
need ssh-keygen "Instale o openssh-clients"
need curl "Instale o curl"
need gpg "Instale o gnupg2 (confere a assinatura da imagem)"
need python3 "Instale o python3"
need uv "Instale o uv (usado para empacotar a ferramenta)"
if command -v genisoimage >/dev/null 2>&1; then
    MKISO=(genisoimage -quiet)
elif command -v mkisofs >/dev/null 2>&1; then
    MKISO=(mkisofs -quiet)
elif command -v xorriso >/dev/null 2>&1; then
    MKISO=(xorriso -as mkisofs -quiet)
else
    die "precisa de genisoimage, mkisofs ou xorriso para criar o disco do cloud-init (sudo dnf install genisoimage)"
fi
# o qemu-img do SDK do Android costuma vir antes no PATH: prefira o do sistema
if [ -x /usr/bin/qemu-img ]; then
    QEMU_IMG=/usr/bin/qemu-img
else
    need qemu-img "Instale o qemu-img"
    QEMU_IMG=$(command -v qemu-img)
fi

[ -f "$DATA/generated/fedora.yml" ] \
    || die "$DATA/generated/fedora.yml não existe (rode 'distro-recovery backup' antes)"

if [ "$KVM" = auto ]; then
    if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
        KVM=on
    else
        KVM=off
        log "sem acesso a /dev/kvm: usando emulação (lento)"
    fi
fi
if [ "$KVM" = on ]; then
    ACCEL=(-enable-kvm -cpu host)
else
    ACCEL=(-cpu max)
    SSH_TIMEOUT=900
fi

# ---------------------------------------------------------------- imagem base
BASE_URL="https://dl.fedoraproject.org/pub/fedora/linux/releases/$RELEASE/Cloud/x86_64/images"
mkdir -p "$CACHE_DIR"

fetch_image() {
    local index image checksum key gnupg expected actual
    index=$(curl -fsSL --max-time 60 "$BASE_URL/") \
        || die "não consegui listar $BASE_URL/ (o Fedora $RELEASE existe?)"
    image=$(grep -oE "Fedora-Cloud-Base-Generic-$RELEASE-[0-9.]+\.x86_64\.qcow2" <<<"$index" | sort -uV | tail -1)
    checksum=$(grep -oE "Fedora-Cloud-$RELEASE-[0-9.]+-x86_64-CHECKSUM" <<<"$index" | sort -uV | tail -1)
    [ -n "$image" ] && [ -n "$checksum" ] || die "imagem ou CHECKSUM não encontrados em $BASE_URL/"
    BASE_IMAGE="$CACHE_DIR/$image"

    if [ -f "$BASE_IMAGE" ] && [ "$REFRESH" = 0 ]; then
        log "imagem em cache: $image"
        return
    fi

    log "baixando $image (cerca de 500 MB)"
    curl -fL --progress-bar -C - -o "$BASE_IMAGE.part" "$BASE_URL/$image"
    curl -fsSL -o "$CACHE_DIR/$checksum" "$BASE_URL/$checksum"

    log "conferindo a assinatura (chave do Fedora) e o SHA256"
    key=$(find /etc/pki/rpm-gpg -name "RPM-GPG-KEY-fedora-$RELEASE-*" 2>/dev/null | sort | head -1)
    [ -n "$key" ] || die "chave do Fedora $RELEASE não encontrada em /etc/pki/rpm-gpg (pacote fedora-gpg-keys)"
    gnupg=$(mktemp -d)
    gpg --homedir "$gnupg" --batch --quiet --import "$key" 2>/dev/null
    if ! gpg --homedir "$gnupg" --batch --verify "$CACHE_DIR/$checksum" 2>/dev/null; then
        rm -rf "$gnupg" "$BASE_IMAGE.part"
        die "assinatura do arquivo CHECKSUM inválida"
    fi
    rm -rf "$gnupg"
    expected=$(sed -n "s/^SHA256 ($image) = \([0-9a-f]\{64\}\)\$/\1/p" "$CACHE_DIR/$checksum")
    actual=$(sha256sum "$BASE_IMAGE.part" | cut -d' ' -f1)
    if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
        rm -f "$BASE_IMAGE.part"
        die "SHA256 da imagem não confere (esperado '$expected', obtido '$actual')"
    fi
    mv "$BASE_IMAGE.part" "$BASE_IMAGE"
}
fetch_image

# ---------------------------------------------------------------- diretório da execução
WORK=$(mktemp -d "$CACHE_DIR/run-XXXXXX")
QEMU_PID_FILE="$WORK/qemu.pid"
SSH_CMD=""

cleanup() {
    local code=$?
    if [ "$KEEP" = 1 ] && [ -f "$QEMU_PID_FILE" ]; then
        log "VM mantida ligada. Entrar:  $SSH_CMD"
        log "Desligar e limpar:  kill \$(cat $QEMU_PID_FILE) && rm -rf $WORK"
    else
        if [ -f "$QEMU_PID_FILE" ]; then
            kill "$(cat "$QEMU_PID_FILE")" 2>/dev/null || true
        fi
        if [ "$code" -ne 0 ] && [ -f "$WORK/serial.log" ]; then
            log "últimas linhas do console da VM:"
            tail -15 "$WORK/serial.log" >&2 || true
        fi
        rm -rf "$WORK"
    fi
    exit "$code"
}
trap cleanup EXIT

log "empacotando a ferramenta"
(cd "$REPO_ROOT" && uv build --wheel -o "$WORK/dist" >/dev/null 2>&1) || die "falha ao empacotar (uv build)"

log "preparando os dados: $DATA (somente leitura)"
mkdir -p "$WORK/data/generated"
cp "$DATA/generated/fedora.yml" "$WORK/data/generated/"
if [ -f "$DATA/generated/pending.yml" ]; then cp "$DATA/generated/pending.yml" "$WORK/data/generated/"; fi
if [ -f "$DATA/decisions.yml" ]; then cp "$DATA/decisions.yml" "$WORK/data/"; fi

# ---------------------------------------------------------------- disco, cloud-init, chave
ssh-keygen -q -t ed25519 -N '' -C vm-test -f "$WORK/id_ed25519"
"$QEMU_IMG" create -q -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$WORK/disk.qcow2" "$DISK"

{
    echo '#cloud-config'
    echo 'users:'
    echo '  - name: tester'
    echo '    shell: /bin/bash'
    echo '    sudo: ALL=(ALL) NOPASSWD:ALL'
    echo '    lock_passwd: true'
    echo '    ssh_authorized_keys:'
    echo "      - $(cat "$WORK/id_ed25519.pub")"
    echo 'ssh_pwauth: false'
    echo 'disable_root: true'
} >"$WORK/user-data"
printf 'instance-id: vm-test-%s\nlocal-hostname: dr-vm-test\n' "$(basename "$WORK")" >"$WORK/meta-data"
"${MKISO[@]}" -output "$WORK/seed.iso" -volid cidata -joliet -rock \
    "$WORK/user-data" "$WORK/meta-data" >/dev/null 2>&1

PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')

log "iniciando a VM (Fedora $RELEASE, $MEMORY MB, $CPUS CPUs, KVM=$KVM, ssh 127.0.0.1:$PORT)"
qemu-system-x86_64 "${ACCEL[@]}" -m "$MEMORY" -smp "$CPUS" \
    -drive "file=$WORK/disk.qcow2,if=virtio,format=qcow2" \
    -drive "file=$WORK/seed.iso,media=cdrom,readonly=on" \
    -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22" -device virtio-net-pci,netdev=n0 \
    -device virtio-rng-pci -display none -serial "file:$WORK/serial.log" \
    -pidfile "$QEMU_PID_FILE" -daemonize

COMMON_OPTS=(-i "$WORK/id_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5)
SSH_CMD="ssh -i $WORK/id_ed25519 -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null tester@127.0.0.1"
vm() { ssh "${COMMON_OPTS[@]}" -p "$PORT" tester@127.0.0.1 "$@"; }
copy_to_vm() { scp "${COMMON_OPTS[@]}" -P "$PORT" -q -r "$@" tester@127.0.0.1:; }
copy_from_vm() { scp "${COMMON_OPTS[@]}" -P "$PORT" -q -r "tester@127.0.0.1:$1" "$2"; }

log "aguardando o ssh da VM (até ${SSH_TIMEOUT}s)"
deadline=$((SECONDS + SSH_TIMEOUT))
until vm true 2>/dev/null; do
    [ "$SECONDS" -lt "$deadline" ] || die "a VM não respondeu ao ssh a tempo"
    kill -0 "$(cat "$QEMU_PID_FILE")" 2>/dev/null || die "o QEMU terminou inesperadamente"
    sleep 3
done
vm 'sudo cloud-init status --wait >/dev/null 2>&1 || true'

# ---------------------------------------------------------------- envio, preparação e teste
log "enviando a ferramenta, os dados e o roteiro de testes"
copy_to_vm "$WORK/dist" "$WORK/data" "$REPO_ROOT/tests/vm/guest.sh"

# Caixa de altura fixa que rola no lugar (padrão; desligue com --quiet)
box() { python3 "$REPO_ROOT/scripts/rolling_box.py" --lines 10 "$@"; }

log "preparando a VM (instala ansible, a ferramenta e dependências)"
set +e
if [ "$VERBOSE" = 1 ]; then
    vm 'bash guest.sh setup' 2>&1 | tee "$WORK/setup.log" | box --all --title "preparando a VM"
    SETUP_RC=${PIPESTATUS[0]}
else
    vm 'bash guest.sh setup' >"$WORK/setup.log" 2>&1
    SETUP_RC=$?
fi
set -e
if [ "$SETUP_RC" -ne 0 ]; then
    [ "$VERBOSE" = 1 ] || tail -30 "$WORK/setup.log" >&2
    die "falha na preparação da VM (log: $WORK/setup.log)"
fi

log "executando os testes na VM"
set +e
if [ "$VERBOSE" = 1 ]; then
    vm 'VERBOSE=1 bash guest.sh test' 2>&1 | tee "$WORK/test.log" | box --title "saída dos comandos"
else
    vm 'bash guest.sh test' 2>&1 | tee "$WORK/test.log"
fi
RESULT=${PIPESTATUS[0]}
set -e

if [ "$RESULT" -ne 0 ]; then
    FAIL_DIR="$CACHE_DIR/last-failure"
    rm -rf "$FAIL_DIR"
    mkdir -p "$FAIL_DIR"
    copy_from_vm logs "$FAIL_DIR/" 2>/dev/null || true
    cp "$WORK/test.log" "$WORK/setup.log" "$FAIL_DIR/" 2>/dev/null || true
    log "FALHOU. Logs do teste e do Ansible salvos em $FAIL_DIR"
else
    log "PASSOU."
fi
exit "$RESULT"
