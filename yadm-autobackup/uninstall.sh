#!/usr/bin/env bash
# Desinstala o yadm-autobackup (script, serviço e timer). Não mexe em ~/.config/yadm/tracked
# nem nos commits já feitos.
# Uso: ./uninstall.sh [--purge-state]
#   --purge-state   também apaga o log em ~/.local/state/yadm-autobackup
set -euo pipefail

PURGE=0
[[ "${1:-}" == "--purge-state" ]] && PURGE=1

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/yadm-autobackup"

if command -v systemctl >/dev/null; then
  systemctl --user disable --now yadm-autobackup.timer 2>/dev/null || true
  systemctl --user stop yadm-autobackup.service 2>/dev/null || true
fi

for f in "$UNIT_DIR/yadm-autobackup.timer" "$UNIT_DIR/yadm-autobackup.service" "$HOME/.local/bin/yadm-autobackup"; do
  if [[ -e "$f" ]]; then
    rm -- "$f"
    echo "removido: $f"
  fi
done

command -v systemctl >/dev/null && systemctl --user daemon-reload

if ((PURGE)) && [[ -d "$STATE" ]]; then
  rm -r -- "$STATE"
  echo "removido: $STATE"
fi

echo "Se você versionou esses arquivos no yadm: yadm rm --cached <arquivos>"
