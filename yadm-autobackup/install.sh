#!/usr/bin/env bash
# Instala o yadm-autobackup: script, serviço e timer do systemd (usuário).
# Uso: ./install.sh [--force] [--no-enable] [--with-tracked]
#   --force          sobrescreve arquivos já instalados que sejam diferentes
#   --no-enable      instala mas não habilita nem inicia o timer
#   --with-tracked   copia tracked.example para ~/.config/yadm/tracked se não existir
set -euo pipefail

FORCE=0 ENABLE=1 TRACKED=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --no-enable) ENABLE=0 ;;
    --with-tracked) TRACKED=1 ;;
    -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $a" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/yadm-autobackup"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
YADM_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/yadm"

command -v yadm >/dev/null || { echo "yadm não encontrado" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl não encontrado (sem systemd?); veja a seção de cron no README" >&2; exit 1; }

# Copia src -> dest sem sobrescrever arquivo diferente, a menos que --force.
put() {
  local mode="$1" src="$2" dest="$3"
  if [[ -e "$dest" ]] && ! cmp -s "$src" "$dest"; then
    if ((!FORCE)); then
      echo "já existe e é diferente: $dest (use --force para sobrescrever)" >&2
      exit 1
    fi
  fi
  install -Dm"$mode" "$src" "$dest"
  echo "instalado: $dest"
}

put 755 "$SRC/yadm-autobackup" "$BIN"
put 644 "$SRC/yadm-autobackup.service" "$UNIT_DIR/yadm-autobackup.service"
put 644 "$SRC/yadm-autobackup.timer" "$UNIT_DIR/yadm-autobackup.timer"

if ((TRACKED)); then
  if [[ -e "$YADM_CFG/tracked" ]]; then
    echo "mantido (já existe): $YADM_CFG/tracked"
  else
    install -Dm644 "$SRC/tracked.example" "$YADM_CFG/tracked"
    echo "criado: $YADM_CFG/tracked (edite a lista)"
  fi
fi

systemctl --user daemon-reload

echo
echo "Teste (não escreve nada):"
"$BIN" --dry-run

if ((ENABLE)); then
  systemctl --user enable --now yadm-autobackup.timer
  echo
  systemctl --user list-timers yadm-autobackup.timer --no-pager
else
  echo
  echo "Timer não habilitado. Para habilitar: systemctl --user enable --now yadm-autobackup.timer"
fi
