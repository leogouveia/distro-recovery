# distro-recovery

CLI em Python que faz inventário (backup) e restauração dos aplicativos instalados no Linux,
no espírito do `brew bundle dump/install`. O backup gera YAML consumido por um playbook
Ansible, que faz a restauração. Fase 1: Fedora + DNF5. Depois: backends apt e pacman, sem
mudar o núcleo.

## Decisões fixas (não reabrir)
- Python ≥ 3.11, `uv` (`uv init --package`), CLI com `typer`, YAML com `ruamel.yaml`
  (round-trip), testes com `pytest`, lint com `ruff`.
- YAML em tudo. Strings sempre entre aspas; saída determinística (listas ordenadas, ordem
  fixa de chaves, sem timestamps fora de `meta.yml`).
- Este repo contém SÓ código genérico: a CLI + roles Ansible empacotados. Nada pessoal.
  (Fixtures reais desta máquina foram aprovadas pelo usuário para `tests/fixtures/`.)
- Dados do usuário ficam em `~/.config/distro-recovery/` (yadm dele):
  - `decisions.yml`: editado pelo usuário (include, exclude, renames, swaps, repos.exclude)
  - `generated/<familia>.yml`, `generated/pending.yml`, `generated/meta.yml`: só a ferramenta
- Dotfiles: yadm. CLI tools: brew (`~/.Brewfile`). Aqui: só sistema (repos, chaves,
  pacotes nativos, grupos, swaps).

## Princípios
1. Intenção, não estado: nomes de pacotes, grupos e repositórios; nunca versões/kernels/deps.
2. Gerado ≠ decidido: `backup` reescreve `generated/` inteiro e NUNCA escreve em
   `decisions.yml`. Precedência no restore: exclude > include > generated. Nome em include
   e exclude = erro de validação.
3. Backup e diff são somente leitura.
4. Restore nunca remove, nunca faz downgrade, nunca desabilita GPG. Única exceção: `swaps`
   declarados (`dnf5 swap --allowerasing`).

## Regras de segurança (para o agente)
- NUNCA rodar sudo nem comandos que alterem o sistema (dnf install/remove/mark/swap,
  config-manager, rpm -i...). Só leitura.
- NUNCA escrever em `~/.config/distro-recovery/` nesta fase; usar tmp ou `--dir` dentro do
  repo (`.out/`, ignorado no git).
- Se a saída real de um comando divergir do formato descrito aqui: PARAR, mostrar a
  diferença ao usuário e ajustar esta especificação antes de seguir.
- Antes de salvar `.repo`, trocar tokens/credenciais por `REDACTED` e mostrar o que mudou.

## Arquitetura
```
src/distro_recovery/
  cli.py        # typer: backup, review, diff, restore, doctor
  model.py      # InstalledPkg, Classified, Confidence(HIGH/REVIEW/EXCLUDED), RepoSpec
  classify.py   # tabela de decisão, PURA, sem I/O
  merge.py      # aplica decisions sobre generated
  render.py     # YAML determinístico
  brewfile.py   # lê ~/.Brewfile para detectar duplicados
  backends/base.py   # Protocol Backend + runner de subprocess injetável
  backends/dnf5.py
  ansible/      # site.yml + roles: repos, swaps, groups, packages
tests/fixtures/dnf5/
```
Protocol Backend: `family`, `detect()`, `installed()`, `installed_groups()`, `baseline()`,
`history_user_installs()`, `leaves()`, `repos(needed)`. Toda leitura de sistema passa por um
runner injetável (testes usam fixtures).

## Backend dnf5: coleta (somente leitura)
- instalados: `dnf5 repoquery --installed --queryformat '%{name}\t%{arch}\t%{reason}\t%{from_repo}\t%{vendor}\n'`
- grupos/ambientes: `dnf5 group list --installed`, `dnf5 environment list --installed`
- histórico: `dnf5 history list --json`, `dnf5 history info <ids> --json`
  (command_line/description, packages[].nevra, action, reason, repository)
- leaves: `dnf5 leaves`
- dono dos .repo: `rpm -qf /etc/yum.repos.d/*.repo`
- chaves: `rpmkeys --list` (fallback `rpm -q gpg-pubkey`)
- protegidos: `/etc/dnf/protected.d/*.conf`
- extras aprovados: `dnf5 group info`, `dnf5 environment info`, `rpm -q --whatprovides system-release`

### Divergências observadas (dnf5 5.4.6, Fedora 44) — spec ajustada
- `--queryformat` NÃO interpreta `\t`/`\n` literais: o shell deve passar TAB/LF reais
  (`$'...\t...\n'`). O runner nunca usa `shell=True`; o argv leva os caracteres reais.
- `from_repo` de ~1117 pacotes (instalados pelo Anaconda) é um hash de 32 hex
  (ex.: `19278be6...`), não um id de repo. Tratar como "mídia de instalação": nunca entra
  em "repos necessários". Também aparecem `@commandline` (local_rpm).
- `reason` literais: `User`, `Group`, `Dependency`, `Weak Dependency` (com espaço).
- `history list --json` traz `command_line`; `history info --json` traz `description`
  (igual ao comando) e `packages[].{nevra,action,reason,repository}`; nevra no formato
  `nome-epoch:ver-rel.arch`.
- `dnf5 leaves` imprime `- nevra` por linha (não só o nome).
- `group list` / `environment list` imprimem linhas de progresso ("Updating and loading
  repositories") — o parser deve ignorá-las (provável stderr; confirmar ao salvar fixtures).
- `rpm -qf` mistura "file ... is not owned by any package" com nomes de pacote, uma linha
  por arquivo na ordem do glob.
- Há `.repo` de uma tentativa anterior (`fedora-recovery-*.repo`, sem dono): são do usuário,
  tratar como repofile comum.

- `group list` / `environment list`: progresso vai para o stderr (confirmado); o stdout termina
  com uma linha-resumo (`Groups: N (...)`) a ignorar.
- `environment info` devolve DUAS entradas (instalada: `Installed: True`, repos `@System`;
  disponível: `False`). Usar a instalada. `Installed` é `yes` no `group info`, `True` no env.
- `group list --installed` lista só 4 grupos, mas o ambiente requer 14: o baseline vem do
  `environment info` + `group info` de cada grupo requerido (todos `Installed: yes`).
- `rpm -q --whatprovides system-release` → `fedora-release-workstation`.
- Fixtures: `manifest.yml` mapeia argv exato (prefixo `dnf5 --assumeno`) → arquivo; leituras
  de arquivo/diretório usam `cat`/`ls -1` pelo runner. `user_name` do histórico foi
  anonimizado (`1000 Test User <user>`), embora o usuário tenha dito que não era necessário. Sem `.repo` com credencial ou `gpgcheck=0` nas
  fixtures reais: casos de pending precisam de fixtures sintéticas.
- Nunca rodar dnf5 sem `-C --assumeno` na coleta: sem cache ele pode pedir importação de
  chave GPG (alteraria o chaveiro).

- `dnf5 leaves`: além de `- nevra`, imprime membros de ciclos de dependência só indentados
  (`  nevra`); ambos são leaves. Pacotes multi-versão (kernel) repetem o nome.
- Tabelas de `group/environment list`: a coluna ID pode ficar a UM espaço do nome; parsear
  com regex (`ID  nome  yes|no`), não por 2+ espaços.
- `.repo` duplicados: o mesmo id pode estar em 2 arquivos (ex.: `vivaldi.repo` e
  `fedora-recovery-vivaldi.repo`). Regra: preferir o que tem gpgkey remota (não `file://`),
  depois a ordem do `ls`; emitir warning `duplicate_repo_id`.
- `.repo` pertencente a um pacote que não é `*-release` nem `fedora-*` (ex.: `chatgpt`)
  -> kind `repofile` com `package` e warning `owned_by_package`.
- Fingerprints GPG: (1) quando estão no nome do arquivo da chave; (2) em repos COPR, por
  casamento exato do UID do `rpmkeys --list` (`<dono#projeto@copr.fedorahosted.org>`). Nos
  demais repos com URL remota ficam vazios (só `gpgkey_urls`): não há acesso à rede.
- `fedora-workstation-repositories` distribui google-chrome, rpmfusion-nonfree-* e o copr
  do PyCharm (kind `third-party`, `package` = dono). Os copr:* sem dono são `copr`.

- Repositório de terceiros sem dono e com `gpgkey` só `file://` (ex.: `fedora-recovery-*.repo`)
  não é restaurável em outra máquina: warning `local_gpgkey`, vai para `pending.yml`.
  Avisos que mandam o repo para pending: `credentials_in_url`, `gpgcheck_disabled`,
  `local_gpgkey`. `duplicate_repo_id` e `owned_by_package` são só informativos.
- `backup --from-fixtures` exige `--dir` explícito. `--ansible` é aceito mas por ora só gera
  os mesmos dados (roles do Ansible: próxima sessão).

Baseline (Fase 1): grupos/ambientes da edição instalada (mandatory + default) + regras
fixas: `kernel*`, `kmod-*` versionados (manter `akmod-*`), `*-firmware`, `glibc-langpack-*`,
`anaconda*`, quem fornece `system-release`, protegidos. Simulação `--installroot` = Fase 3.

> Histórico (divergência aprovada): as transações 1 e 2 desta máquina são builds de imagem
> (`kiwi`, uid 0, todos os pacotes do repo-hash), não instalação do usuário. "Após a 1ª
> transação" vira "fora das transações de imagem" (`UserHistory.image_tx_ids` = transações
> cujos pacotes vêm todos de um `from_repo` hash de 32 hex). Pacotes instalados só pela
> imagem (qualquer razão) e nunca por ação do usuário são `baseline` (EXCLUDED), mesmo sendo
> leaves (ex.: `dracut-live`, `livesys-scripts`).

## Tabela de decisão (classify.py)
| Condição | Resultado | reason_code |
|---|---|---|
| em decisions.exclude | EXCLUDED | user_excluded |
| em decisions.include | HIGH | user_included |
| regra fixa | EXCLUDED | system_rule |
| Dependency/Weak | EXCLUDED | dependency |
| Group, grupo fora da baseline | coberto pelo @grupo | via_group |
| Group, grupo na baseline | EXCLUDED | baseline_group |
| User ∉ baseline, no histórico | HIGH | user_history |
| User ∉ baseline, fora do histórico, leaf | HIGH | user_leaf |
| User ∉ baseline, fora do histórico, não leaf | REVIEW | user_unconfirmed |
| User ∈ baseline, no histórico fora das transações de imagem | REVIEW | in_baseline_but_user |
| User ∈ baseline, fora do histórico | EXCLUDED | baseline |
| from_repo = @commandline | REVIEW | local_rpm |
| nome também no Brewfile | REVIEW | duplicated_in_brew |

HIGH → `generated/fedora.yml`; REVIEW → `generated/pending.yml`; EXCLUDED → log com --verbose.

## Repositórios
- Necessários = from_repo de TODOS os instalados, menos os oficiais.
- kind pelo dono do .repo: `fedora-repos*` = oficial (ignorar); `fedora-workstation-repositories`
  = third-party; pacote `*-release` = release-package (URL com `{{ releasever }}`);
  `_copr*.repo` = copr (owner/projeto); sem dono = repofile.
- repofile normalizado: versões fixas → `$releasever`, arquitetura → `$basearch`; credenciais
  na URL ou gpgcheck=0 → pending com aviso. Registrar URL + fingerprint das chaves GPG.

## Ansible (próximas sessões)
Ordem: repos → swaps → groups → packages. Módulos: `ansible.builtin.dnf5` (present,
allow_downgrade false), `yum_repository`, `rpm_key` com fingerprint, `community.general.copr`.
packages: filtrar com `dnf5 repoquery --available`, instalar a interseção, reportar ausentes
(sugestão via `--whatobsoletes`/`--whatprovides`) sem falhar.

## CLI e códigos de saída
- `backup [--ansible] [--dir PATH] [--from-fixtures DIR] [--verbose]`: 0 ok, 2 pendências, 1 erro
- `diff [--json]`: 0 sem drift, 3 drift
- `review`, `restore [--check]`, `doctor`
`--from-fixtures` roda o backup inteiro sem tocar no sistema.

## Comandos de desenvolvimento
`uv` está em `/home/linuxbrew/.linuxbrew/bin/uv` (brew).
- `uv sync` — instala dependências
- `uv run pytest` — testes
- `uv run ruff check` / `uv run ruff format --check` — lint

## Decisões pendentes
Ver `docs/pendencias.md` (chaves GPG de repositórios, `--ansible`).

## Fluxo
TDD na ordem: model → parsers dnf5 → classify (1 teste por reason_code) → render
(determinismo: 2 execuções = saída idêntica) → `backup --ansible --from-fixtures`.
Um commit por etapa, Conventional Commits.
