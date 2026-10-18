#!/usr/bin/env bash
# Roda DENTRO da VM. Uso: guest.sh setup | guest.sh test
# Espera em $HOME: dist/*.whl (a ferramenta) e data/ (generated/fedora.yml [+ decisions.yml]).
set -uo pipefail

export PYTHONUNBUFFERED=1  # ansible e pip mostram a saída na hora, não só no final
DR=/opt/dr/bin/distro-recovery
PY=/opt/dr/bin/python
DATA="$HOME/data"
LOGS="$HOME/logs"
PASS=0
FAIL=0

ok()      { printf 'PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()     { printf 'FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }
section() { printf '\n== %s\n' "$1"; }
check()   { local desc=$1; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }

# Roda um comando guardando a saída em um arquivo. Com VERBOSE=1, repete a saída na tela com o
# prefixo "@@ " (o vm-test.sh mostra essas linhas numa caixa que rola no lugar).
run_logged() {
    local log=$1
    shift
    if [ "${VERBOSE:-0}" = 1 ]; then
        "$@" 2>&1 | tee "$log" | sed -u 's/^/@@ /'
        return "${PIPESTATUS[0]}"
    fi
    "$@" >"$log" 2>&1
}

# ------------------------------------------------------------------ setup
setup() {
    set -e
    sudo dnf5 install -y ansible-core python3-libdnf5 dnf5-plugins python3-pip
    if ! ansible-galaxy collection list 2>/dev/null | grep -q community.general; then
        sudo dnf5 install -y ansible-collection-community-general \
            || sudo ansible-galaxy collection install community.general -p /usr/share/ansible/collections
    fi
    sudo python3 -m venv /opt/dr
    sudo /opt/dr/bin/pip install --quiet "$HOME"/dist/*.whl
    "$DR" --help >/dev/null
    echo "setup concluído"
}

# ------------------------------------------------------------------ test
expected() {  # expected <chave>: valores esperados, um por linha, calculados pela própria ferramenta
    "$PY" - "$DATA" "$1" <<'PYEOF'
import sys
from pathlib import Path
from ruamel.yaml import YAML
from distro_recovery.merge import build_vars, load_decisions

data_dir, key = Path(sys.argv[1]), sys.argv[2]
gen = YAML(typ="safe").load((data_dir / "generated" / "fedora.yml").read_text())
dec = load_decisions(data_dir / "decisions.yml")
v = build_vars(gen, dec)
if key == "packages":
    print("\n".join(v["dr_packages"]))
elif key == "groups":
    print("\n".join(v["dr_groups"]))
elif key == "exclude":
    print("\n".join(sorted(dec.exclude)))
elif key == "swaps":
    print(len(v["dr_swaps"]))
elif key == "repos":  # id|kind|tem_baseurl|chaves_https
    for r in v["dr_repos"]:
        urls = [u for u in r.get("gpgkey_urls", []) if u.startswith("https://")]
        print(f'{r["id"]}|{r["kind"]}|{int("baseurl" in r)}|{len(urls)}')
PYEOF
}

pkgs_now()  { rpm -qa --qf '%{NAME}\n' | sort -u; }
repo_files() { ls -1 /etc/yum.repos.d | sort; }
key_count() { rpmkeys --list | wc -l; }
restore()   { "$DR" restore --dir "$DATA" --no-ask-become-pass "$@"; }

run_tests() {
    mkdir -p "$LOGS"
    mapfile -t PKGS < <(expected packages | sed '/^$/d')
    mapfile -t GROUPS_ < <(expected groups | sed '/^$/d')
    mapfile -t EXCLUDED < <(expected exclude | sed '/^$/d')
    mapfile -t REPOS < <(expected repos | sed '/^$/d')
    SWAPS=$(expected swaps)

    section "Preparação"
    # Pacote "canário": instalado antes e fora dos dados. O restore nunca pode removê-lo.
    CANARY=
    for c in tree cowsay figlet; do
        if ! printf '%s\n' "${PKGS[@]}" | grep -qx "$c" && ! rpm -q "$c" >/dev/null 2>&1; then
            CANARY=$c; break
        fi
    done
    [ -n "$CANARY" ] && run_logged "$LOGS/canary.log" sudo dnf5 install -y "$CANARY"
    check "pacote canário instalado (${CANARY:-nenhum})" test -n "$CANARY"
    pkgs_now >"$LOGS/pkgs.before"; repo_files >"$LOGS/repos.before"; KEYS_BEFORE=$(key_count)
    echo "pacotes antes: $(wc -l <"$LOGS/pkgs.before"), chaves antes: $KEYS_BEFORE"

    section "restore --check (simulação) não altera o sistema"
    run_logged "$LOGS/check.log" restore --check; rc=$?
    [ "$rc" -eq 0 ] && ok "restore --check terminou com código 0" || { bad "restore --check código $rc"; tail -25 "$LOGS/check.log"; }
    pkgs_now >"$LOGS/pkgs.check"; repo_files >"$LOGS/repos.check"
    check "nenhum pacote mudou" cmp -s "$LOGS/pkgs.before" "$LOGS/pkgs.check"
    check "nenhum arquivo .repo mudou" cmp -s "$LOGS/repos.before" "$LOGS/repos.check"

    section "restore (aplicando)"
    run_logged "$LOGS/restore1.log" restore; rc=$?
    [ "$rc" -eq 0 ] && ok "restore terminou com código 0" || { bad "restore código $rc"; tail -40 "$LOGS/restore1.log"; }
    pkgs_now >"$LOGS/pkgs.after"

    section "Repositórios e chaves"
    EXPECT_KEYS=0
    for line in "${REPOS[@]}"; do
        IFS='|' read -r id kind has_base nkeys <<<"$line"
        EXPECT_KEYS=$((EXPECT_KEYS + nkeys))
        enabled=$(sudo dnf5 --assumeno repo list --enabled 2>/dev/null)
        if printf '%s\n' "$enabled" | awk '{print $1}' | grep -qxF "$id"; then
            ok "repositório $id habilitado"
        else
            bad "repositório $id habilitado"
        fi
        if [ "$kind" = repofile ] && [ "$has_base" = 1 ]; then
            f=$(grep -lxF "[$id]" /etc/yum.repos.d/*.repo 2>/dev/null | head -1)
            if [ -n "$f" ] && grep -q '^gpgcheck *= *1' "$f"; then ok "$id: gpgcheck=1 (assinatura verificada)"; else bad "$id: gpgcheck=1 (arquivo: ${f:-nenhum})"; fi
        fi
    done
    if grep -rqE '^gpgcheck *= *0' /etc/yum.repos.d/ 2>/dev/null; then
        # a imagem base não deve ter nada com gpgcheck=0; qualquer um novo é falha
        new=$(grep -rlE '^gpgcheck *= *0' /etc/yum.repos.d/ | xargs -n1 basename | sort | comm -13 "$LOGS/repos.before" -)
        [ -z "$new" ] && ok "nenhum .repo novo com gpgcheck=0" || bad "novo .repo com gpgcheck=0: $new"
    else
        ok "nenhum .repo com gpgcheck=0"
    fi
    KEYS_AFTER=$(key_count)
    if [ "$EXPECT_KEYS" -gt 0 ]; then
        [ "$KEYS_AFTER" -gt "$KEYS_BEFORE" ] && ok "chaves GPG importadas ($KEYS_BEFORE -> $KEYS_AFTER)" || bad "chaves GPG importadas ($KEYS_BEFORE -> $KEYS_AFTER)"
    fi

    section "Grupos"
    installed_groups=$(sudo dnf5 --assumeno group list --installed 2>/dev/null | awk '{print $1}')
    for g in "${GROUPS_[@]}"; do
        printf '%s\n' "$installed_groups" | grep -qxF "$g" && ok "grupo $g instalado" || bad "grupo $g instalado"
    done

    section "Pacotes"
    missing_reported=$(sed -n 's/.*Não encontrado: \([^ ]*\)\..*/\1/p' "$LOGS/restore1.log" | sed 's/\.$//' | sort -u)
    for p in "${PKGS[@]}"; do
        if rpm -q "$p" >/dev/null 2>&1; then
            ok "pacote $p instalado"
        elif printf '%s\n' "$missing_reported" | grep -qxF "$p"; then
            echo "INFO  pacote $p não existe nos repositórios (reportado, sem falhar)"
        else
            bad "pacote $p nem instalado nem reportado como ausente"
        fi
    done
    for p in "${EXCLUDED[@]}"; do
        if grep -qx "$p" "$LOGS/pkgs.before"; then continue; fi
        grep -qx "$p" "$LOGS/pkgs.after" && bad "exclude: $p foi instalado" || ok "exclude: $p não foi instalado"
    done

    section "Nunca remove"
    removed=$(comm -23 "$LOGS/pkgs.before" "$LOGS/pkgs.after")
    if [ "$SWAPS" = 0 ]; then
        [ -z "$removed" ] && ok "nenhum pacote foi removido" || bad "pacotes removidos: $(echo $removed)"
    else
        echo "INFO  há swaps declarados: remoções esperadas não são verificadas"
    fi
    [ -z "$CANARY" ] || { rpm -q "$CANARY" >/dev/null 2>&1 && ok "canário $CANARY continua instalado" || bad "canário $CANARY foi removido"; }

    section "Idempotência (segunda execução)"
    run_logged "$LOGS/restore2.log" restore; rc=$?
    [ "$rc" -eq 0 ] && ok "segunda execução com código 0" || { bad "segunda execução código $rc"; tail -30 "$LOGS/restore2.log"; }
    if grep -qE 'changed=0 ' "$LOGS/restore2.log"; then ok "segunda execução sem mudanças (changed=0)"; else bad "segunda execução alterou algo"; grep -E 'changed=|^changed:' "$LOGS/restore2.log" | head; fi

    printf '\nRESUMO: %d ok, %d falhas\n' "$PASS" "$FAIL"
    [ "$FAIL" -eq 0 ] && echo "RESULTADO: PASSOU" || echo "RESULTADO: FALHOU"
    [ "$FAIL" -eq 0 ]
}

case "${1:-}" in
    setup) setup ;;
    test) run_tests ;;
    *) echo "uso: $0 setup|test" >&2; exit 2 ;;
esac
