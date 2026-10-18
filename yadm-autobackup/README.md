# yadm-autobackup

Backup diário e automático dos dotfiles com [yadm](https://yadm.io), para você não precisar
lembrar de commitar. Antes do commit, atualiza o `~/.Brewfile` e roda `distro-recovery backup`,
então o repositório sempre reflete o estado atual da máquina.

Os arquivos aqui são genéricos. Os seus dados pessoais ficam no `$HOME` e no seu repo do yadm.

## Arquivos

| Arquivo | Instala em |
|---|---|
| `yadm-autobackup` | `~/.local/bin/yadm-autobackup` |
| `yadm-autobackup.service` | `~/.config/systemd/user/yadm-autobackup.service` |
| `yadm-autobackup.timer` | `~/.config/systemd/user/yadm-autobackup.timer` |
| `tracked.example` | `~/.config/yadm/tracked` (opcional) |

## O que faz

1. Garante uma única execução por vez e não roda se o yadm estiver em uso (`index.lock`).
2. `brew bundle dump --force --file ~/.Brewfile`. Só atualiza o inventário; não faz `update` nem `upgrade`.
3. `distro-recovery backup`. O código de saída 2 (há pendências) conta como sucesso.
4. Stage: `yadm add -u` mais os caminhos de `~/.config/yadm/tracked`.
5. Travas de segurança sobre o stage:
   - bloqueia arquivos listados em `~/.config/yadm/encrypt` e nomes como `id_*`, `*.pem`, `*.key`, `known_hosts`;
   - procura padrões de segredo nas linhas adicionadas (chave privada, `ghp_`, `AKIA`, `sk-`, `token=`/`password=` com valor).

   Em caso de achado, desfaz o stage (`yadm reset -q`), não commita e notifica.
6. Avisa se o `archive` cifrado ficou mais velho que algum arquivo cifrado. O `yadm encrypt` pede
   senha, então não roda sozinho: você precisa executá-lo.
7. Commita (`auto: backup AAAA-MM-DD (N arquivos)`) só se houver mudança e faz `yadm push`.
   Commits locais de uma execução que falhou no push são reenviados na próxima.
8. Falhas e avisos geram `notify-send`. A saída fica em `~/.local/state/yadm-autobackup/last.log`.

## Requisitos

`yadm`, `git`, `flock`, `systemd` (modo usuário). Opcionais: `brew`, `distro-recovery` e
`notify-send`; se faltarem, o script avisa e segue. O push precisa de acesso ao remoto, por exemplo
SSH com a chave no agente.

## Instalar

Na raiz do repositório `distro-recovery`:

```sh
D=yadm-autobackup
install -Dm755 $D/yadm-autobackup        ~/.local/bin/yadm-autobackup
install -Dm644 $D/yadm-autobackup.service ~/.config/systemd/user/yadm-autobackup.service
install -Dm644 $D/yadm-autobackup.timer   ~/.config/systemd/user/yadm-autobackup.timer
cp -n $D/tracked.example ~/.config/yadm/tracked      # opcional; edite a lista depois

yadm-autobackup --dry-run                            # confira o que faria
systemctl --user daemon-reload
systemctl --user enable --now yadm-autobackup.timer
systemctl --user list-timers yadm-autobackup.timer
```

Ajuste antes de instalar, se precisar:

- **Horário:** `OnCalendar=` e `OnBootSec=` em `yadm-autobackup.timer`. Hoje: todo dia às 13:00 e
  5 minutos após ligar o PC. `Persistent=true` recupera uma execução perdida.
- **Agente SSH:** `Environment=SSH_AUTH_SOCK=%t/gcr/ssh` em `yadm-autobackup.service` é o do GNOME
  (`gcr-ssh-agent`). Veja o seu com `echo $SSH_AUTH_SOCK` e troque se for diferente.

Para versionar a instalação no próprio yadm:
`yadm add ~/.local/bin/yadm-autobackup ~/.config/systemd/user/yadm-autobackup.*`

## Adaptar ao seu ambiente

O script foi escrito para Fedora com GNOME, yadm 3.x e Homebrew. Em outro ambiente, confira estes
pontos **antes** de habilitar o timer. Os valores ficam no topo de `yadm-autobackup` e nas unidades.

| O que | Onde | Padrão | Quando mudar |
|---|---|---|---|
| Agente SSH | `Environment=SSH_AUTH_SOCK=` em `yadm-autobackup.service` | `%t/gcr/ssh` (GNOME) | Em outro desktop ou agente. Descubra com `echo $SSH_AUTH_SOCK`. Exemplos: `%t/keyring/ssh` (gnome-keyring antigo), `%t/ssh-agent.socket` (unit `ssh-agent` do systemd), `%t/gnupg/S.gpg-agent.ssh` (gpg-agent). Sem agente, o push falha; use remoto HTTPS com credential helper ou deixe sem push (veja abaixo). |
| Local do repo do yadm | `YADM_REPO` | `~/.local/share/yadm/repo.git` | yadm 2.x usa `~/.yadm/repo.git`. Confira com `yadm introspect repo`. |
| Local do archive cifrado | `ARCHIVE` | `~/.local/share/yadm/archive` | yadm 2.x usa `~/.yadm/files.gpg`. |
| Config do yadm | `CONFIG_DIR` | `$XDG_CONFIG_HOME/yadm` (`~/.config/yadm`) | yadm 2.x usa `~/.yadm`. Afeta `tracked` e `encrypt`. |
| Brewfile | `BREWFILE` | `~/.Brewfile` | Se o seu estiver em outro lugar. |
| Caminho do Homebrew | linha `PATH=` do script | `/home/linuxbrew/.linuxbrew/bin` | No macOS (`/opt/homebrew/bin`) ou instalação em `~/.linuxbrew`. O systemd não carrega o seu PATH de shell. |
| `distro-recovery` | `~/.local/bin` no `PATH` do script | instalado com `uv tool`/`pipx` em `~/.local/bin` | Se estiver em outro lugar, acrescente-o ao `PATH` do script ou à unidade. |
| Horário | `yadm-autobackup.timer` | 13:00 e 5 min após o boot | Qualquer `OnCalendar=` válido (`systemd-analyze calendar '<expressão>'`). |
| Padrões de segredo | `SECRET_RE` e `FORBIDDEN_RE` | genéricos | Acrescente os formatos de token que você usa. Falsos positivos: ajuste a regex ou tire o arquivo do backup. |

Casos comuns:

- **Sem Homebrew ou sem `distro-recovery`:** nada a fazer. O script avisa, notifica e segue com o resto.
  Para remover o aviso, apague o bloco do passo 2 ou 3 no script.
- **Sem remoto configurado ou sem push:** o push só acontece se a branch tiver upstream
  (`yadm rev-parse @{u}`). Defina com `yadm push -u origin <branch>` ou deixe sem; o script fará só commits locais.
- **Sem criptografia do yadm:** sem `~/.config/yadm/encrypt`, as travas por lista e o aviso do archive
  são ignorados. A trava por nome e por padrão continua valendo.
- **Sem `notify-send` ou sem sessão gráfica (servidor):** as notificações são ignoradas; acompanhe pelo
  `last.log` e pelo `journalctl`. Um timer de usuário em servidor precisa de `loginctl enable-linger $USER`
  para rodar sem sessão aberta.
- **Outro sistema de init (sem systemd):** use cron chamando `~/.local/bin/yadm-autobackup`, definindo
  `SSH_AUTH_SOCK` no crontab. As unidades não se aplicam.
- **Outro gerenciador de dotfiles** (chezmoi, bare git manual): o script usa `yadm` em todos os passos.
  Troque por `git --git-dir=... --work-tree=$HOME` e revise as seções de lock e archive.
- **Muitas máquinas no mesmo repositório:** todas fariam commits automáticos e podem divergir. Use uma
  branch por máquina (`yadm checkout -b <host>`) ou rode o autobackup em uma só e faça `yadm pull` nas outras.
- **Nome ou e-mail do git:** o commit usa `user.name`/`user.email` do yadm. Confira com
  `yadm config user.email`.

Depois de adaptar, valide:

```sh
shellcheck yadm-autobackup
yadm-autobackup --dry-run
systemd-analyze verify --user ~/.config/systemd/user/yadm-autobackup.service ~/.config/systemd/user/yadm-autobackup.timer
```

## Usar

```sh
yadm-autobackup --dry-run                         # só mostra o que faria, sem escrever nada
systemctl --user start yadm-autobackup.service    # executa agora, de verdade
cat ~/.local/state/yadm-autobackup/last.log       # saída da última execução
journalctl --user -u yadm-autobackup.service      # histórico do systemd
systemctl --user list-timers yadm-autobackup.timer
```

A primeira execução real commita tudo o que já estiver modificado e rastreado. Veja
`yadm status` antes.

### Lista `tracked`

`~/.config/yadm/tracked` tem um caminho relativo ao `$HOME` por linha (`#` comenta). Arquivos
**novos** dentro desses caminhos entram no backup sozinhos. Arquivos já rastreados não precisam
estar na lista. Prefira arquivos ou diretórios pequenos: as travas ajudam, mas não substituem revisão.
O formato é o mesmo do [autoyadm](https://git.ficd.sh/ficd/autoyadm).

### Arquivo cifrado desatualizado

Se o aviso "archive desatualizado" aparecer, rode manualmente:

```sh
yadm encrypt && yadm add ~/.local/share/yadm/archive && yadm commit -m "chore: update archive" && yadm push
```

### Tirar um arquivo do backup

```sh
yadm rm --cached <arquivo>      # mantém o arquivo no disco
```

E remova a linha correspondente de `~/.config/yadm/tracked`, senão o script o adiciona de novo.

## Desinstalar

```sh
systemctl --user disable --now yadm-autobackup.timer
rm ~/.config/systemd/user/yadm-autobackup.service ~/.config/systemd/user/yadm-autobackup.timer
rm ~/.local/bin/yadm-autobackup
systemctl --user daemon-reload
rm -rf ~/.local/state/yadm-autobackup          # log; opcional
```

`~/.config/yadm/tracked` é seu: remova só se quiser. Os commits já feitos continuam no repositório.
Se você versionou os arquivos no yadm, tire-os também: `yadm rm --cached <arquivos>`.

## Solução de problemas

| Sintoma | Causa provável |
|---|---|
| "yadm em uso (index.lock existe)" | Outra operação do yadm está rodando, ou travou. Confira e tente de novo. |
| "possível segredo em: ..." | A trava bloqueou. Revise o arquivo; nada foi commitado. |
| "yadm push falhou" | Agente SSH sem a chave ou sem rede. O commit local fica e é reenviado depois. |
| Notificação não aparece | Falta `notify-send` ou sessão gráfica; veja o `last.log`. |
| Timer não dispara | `systemctl --user list-timers`; confira se foi habilitado com `enable --now`. |
