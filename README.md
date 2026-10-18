# distro-recovery

Guarda a lista do que você instalou no Linux e restaura tudo em outro computador (ou depois de
formatar), no espírito do `brew bundle dump` / `brew bundle install`.

- **`backup`** olha o sistema e anota os **aplicativos que você instalou** e os **repositórios
  externos** que eles precisam. Só lê o sistema, não altera nada.
- **`restore`** usa um playbook Ansible para recriar os repositórios (com as chaves de
  assinatura), os grupos e os pacotes.

> **Fase 1:** Fedora com DNF5. Debian/Ubuntu (apt) e Arch (pacman) virão depois, sem mudar o
> núcleo. Os comandos `review`, `diff` e `doctor` ainda não existem.

## Princípios

1. **Intenção, não estado.** Só nomes de pacotes, grupos e repositórios. Nunca versões, kernels
   ou dependências.
2. **Gerado ≠ decidido.** O `backup` reescreve `generated/` inteiro e **nunca** escreve no
   `decisions.yml`, que é só seu. Precedência no restore: `exclude` > `include` > gerado. Um
   nome em `include` e `exclude` ao mesmo tempo é erro.
3. **O backup é somente leitura.**
4. **O restore nunca remove, nunca faz downgrade e nunca desliga a verificação de assinatura
   (GPG).** A única exceção são as trocas (`swaps`) que você declarar.

Este repositório tem só código genérico. Os seus dados ficam fora dele, em
`~/.config/distro-recovery/` (pensado para ir no seu yadm):

```
~/.config/distro-recovery/
├── decisions.yml          # você edita
├── generated/             # a ferramenta escreve (não edite)
│   ├── fedora.yml         #   apps, grupos e repositórios
│   ├── pending.yml        #   o que precisa da sua atenção
│   └── meta.yml           #   data e versão (o único lugar com data)
└── ansible/               # só com `backup --ansible`: playbook + vars.yml
```

## Requisitos

- Fedora com DNF5 (o padrão desde o Fedora 41).
- [`uv`](https://docs.astral.sh/uv/) e Python 3.11 ou mais novo.
- Para o `restore`: `ansible-core` e a coleção `community.general`.

```bash
sudo dnf install ansible-core
ansible-galaxy collection install community.general
```

## Instalação

```bash
git clone <este repositório> && cd distro-recovery
uv tool install .          # instala o comando `distro-recovery`
# ou, sem instalar: uv run distro-recovery --help
```

## Uso

### 1. Fazer o backup

```bash
distro-recovery backup --ansible --verify-keys
```

Isso atualiza `~/.config/distro-recovery/generated/` e o playbook em `.../ansible/`.
Rode de novo quando quiser: sempre reflete o que está instalado agora.

| Opção | O que faz |
|---|---|
| `--ansible` | Copia o playbook para `<dir>/ansible/` e escreve o `vars.yml`. |
| `--verify-keys` | Baixa cada chave GPG (só https) e confere se é uma das instaladas. Só avisa. Usa internet. |
| `--verbose` | Lista também o que foi deixado de fora e por quê. |
| `--brewfile ARQ` | Compara com um Brewfile. Apps iguais ficam como pendência. Padrão: `~/.Brewfile`. |
| `--dir DIR` | Pasta dos dados (padrão: `~/.config/distro-recovery`). |
| `--from-fixtures DIR` | Lê as saídas dos comandos de uma pasta (testes). Exige `--dir`. |

Código de saída: **0** tudo certo, **2** há pendências em `pending.yml`, **1** erro.

### 2. Revisar as pendências

Abra `generated/pending.yml`. Para cada item, decida no `decisions.yml`:

- app que **não é seu** → `exclude`;
- app que **é seu** → `include`.

### 3. Restaurar

```bash
distro-recovery restore --check     # simula, sem alterar nada
distro-recovery restore             # aplica (pede a senha do sudo)
```

O restore lê `generated/fedora.yml` e o `decisions.yml` na hora, então mudar uma decisão vale
sem novo backup. Ordem: **repositórios → trocas → grupos → pacotes**. Pacote que não existe nos
repositórios é listado (com sugestão de substituto), sem derrubar a restauração.

Antes de usar no seu computador de verdade, teste numa VM descartável:
[`VM_TEST.md`](VM_TEST.md).

## O `decisions.yml`

Só você edita este arquivo. Tudo é opcional:

```yaml
include:                 # apps que o backup não detectou como seus
  - "meu-app"
exclude:                 # apps que não devem ser restaurados
  - "chatgpt"
repos:
  exclude:               # repositórios que não devem ser restaurados
    - "vivaldi"
  gpgkey:                # endereço da chave de um repositório (https), sobrescreve a tabela
    code: "https://packages.microsoft.com/keys/microsoft.asc"
swaps:                   # única exceção ao "nunca remove": troca um pacote por outro
  - from: "ffmpeg-free"
    to: "ffmpeg"
```

## Como o backup decide o que é "seu"

O dnf marca cada pacote com um motivo (`User`, `Group`, `Dependency`, `Weak Dependency`).
O backup cruza isso com o histórico de transações e com o que veio na imagem de instalação do
Fedora:

| Resultado | Quando |
|---|---|
| **Entra** (`generated/fedora.yml`) | Você o instalou (consta no histórico) ou nada depende dele. |
| **Pendência** (`pending.yml`) | Não dá para ter certeza, ou é um RPM local, ou o mesmo nome está no Brewfile. |
| **Fica de fora** | Dependência, pacote do sistema (kernel, firmware...), ou veio só com a imagem de instalação. |

As regras completas estão em [`CLAUDE.md`](CLAUDE.md).

## Repositórios e chaves

O backup guarda o **endereço oficial da chave**, nunca a chave. No restore a chave é baixada na
hora e importada, como em `rpm --import <endereço>`, e o repositório é criado com a verificação
de assinatura ligada. Repositórios COPR usam `dnf copr enable`, que já importa a chave certa.
Detalhes e decisões em aberto: [`docs/pendencias.md`](docs/pendencias.md).

## Desenvolvimento

```bash
uv sync                      # dependências, inclusive pytest, ruff e ansible-core
uv run pytest                # testes (usam fixtures reais de um Fedora 44, sem tocar no sistema)
uv run ruff check            # lint
uv run ruff format --check
```

Toda leitura do sistema passa por um *runner* injetável; os testes e o
`backup --from-fixtures` usam as saídas gravadas em `tests/fixtures/dnf5/`. O teste de sintaxe
do playbook precisa da coleção `community.general` (sem ela, é pulado).

```
src/distro_recovery/
  cli.py classify.py merge.py render.py model.py brewfile.py keys.py repokeys.py
  backends/        # base.py (Protocol + runners) e dnf5.py
  ansible/         # site.yml + roles: repos, swaps, groups, packages
tests/             # pytest; fixtures em tests/fixtures; teste de VM em tests/vm
scripts/vm-test.sh # teste de ponta a ponta numa VM (veja VM_TEST.md)
docs/pendencias.md # decisões em aberto
```

## Limitações conhecidas

- Só Fedora/DNF5 por enquanto.
- Dos repositórios com chave só por endereço remoto, o backup não guarda o fingerprint.
- Os módulos do Ansible para dnf5 e COPR exigem `root` até em `--check`; por isso o
  [teste em VM](VM_TEST.md) existe.
- `review`, `diff` e `doctor` ainda não foram implementados.
