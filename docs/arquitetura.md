# Como o distro-recovery funciona

Guia para ler e entender o código. Os diagramas usam [Mermaid](https://mermaid.js.org/) (o
GitHub e a maioria dos editores renderizam). Para o uso do dia a dia, veja o `README.md`.

## 1. A ideia em uma frase

Duas metades que conversam por arquivos YAML:

- **backup** (Python): olha o sistema, decide o que foi *escolha sua* e grava YAML.
- **restore** (Ansible): lê esse YAML, mais as suas decisões, e recria repositórios, grupos e
  pacotes.

```mermaid
flowchart LR
    SYS[(Sistema<br/>Fedora + DNF5)] -- "somente leitura" --> BK[backup<br/>Python]
    BK --> GEN[/"generated/<br/>fedora.yml · pending.yml · meta.yml"/]
    DEC[/"decisions.yml<br/>(você edita)"/] --> RS
    GEN --> RS[restore<br/>Python + Ansible]
    RS -- "instala, nunca remove" --> NEW[(Máquina nova)]
    BK -. "só sugere" .-> DEC
```

Regra de ouro: **gerado ≠ decidido**. O `backup` reescreve `generated/` inteiro, mas *nunca*
toca no `decisions.yml`. O único comando que cria esse arquivo é o `init` (e só se ele não
existir).

## 2. Mapa dos módulos

```
src/distro_recovery/
├── cli.py               # comandos (typer): liga todas as peças; faz o I/O
├── model.py             # tipos de dados (dataclasses/enums), sem lógica de I/O
├── classify.py          # PURO: tabela de decisão "este pacote entra no backup?"
├── merge.py             # lê decisions.yml e calcula as variáveis do Ansible
├── render.py            # grava YAML de forma determinística
├── brewfile.py          # lê ~/.Brewfile (detecta nomes duplicados com o brew)
├── keys.py              # baixa chaves GPG (https) e lê fingerprints
├── repokeys.py          # endereços de chaves GPG: tabela conhecida + conferência
├── decisions_template.py# texto do decisions.yml criado pelo `init`
├── backends/
│   ├── base.py          # Protocol Backend + runners (real, fixtures) + trava somente-leitura
│   └── dnf5.py          # implementação para Fedora/DNF5
└── ansible/             # site.yml + roles (repos, swaps, groups, packages)
```

Dependências entre eles (seta = "usa"):

```mermaid
flowchart TD
    CLI[cli.py]
    CLI --> BASE[backends/base.py]
    CLI --> DNF[backends/dnf5.py]
    CLI --> CLS[classify.py]
    CLI --> MRG[merge.py]
    CLI --> RND[render.py]
    CLI --> BREW[brewfile.py]
    CLI --> RK[repokeys.py]
    CLI --> KEYS[keys.py]
    CLI --> TPL[decisions_template.py]
    CLI -. "ansible-playbook" .-> ANS[ansible/]
    DNF --> BASE
    DNF --> MOD[model.py]
    CLS --> MOD
    MRG --> CLS
    RND --> MOD
    RK --> MOD
    BASE --> MOD

    classDef pure fill:#e8f5e9,stroke:#2e7d32;
    class CLS,MOD,RND,MRG,BREW,TPL pure;
```

Em verde: módulos sem acesso ao sistema (só recebem dados e devolvem dados), por isso são
fáceis de testar. Todo contato com o sistema passa por **um único ponto**: o *runner*.

## 3. O runner: por que os testes não tocam no seu sistema

O backend nunca chama `subprocess` diretamente. Ele recebe um *runner* (uma função
`argv -> CmdResult`). Em produção é o `SubprocessRunner`; nos testes e em
`backup --from-fixtures` é o `FixtureRunner`, que responde com arquivos gravados.

```mermaid
flowchart LR
    DNF[Dnf5Backend] --> R{Runner}
    R -- produção --> SP[SubprocessRunner]
    R -- testes / --from-fixtures --> FX[FixtureRunner]
    SP --> WL{lista branca<br/>somente leitura?}
    WL -- sim --> OS[(dnf5 · rpm · ls · cat)]
    WL -- não --> ERR[ReadOnlyViolation]
    FX --> MAN[tests/fixtures/dnf5<br/>manifest.yml + saídas]
```

`SubprocessRunner` só aceita `ls`, `cat`, `rpmkeys --list`, `rpm -q…` e os subcomandos de
consulta do `dnf5` (`repoquery`, `leaves`, `group|environment list|info`, `history list|info`).
Qualquer outra coisa levanta `ReadOnlyViolation`. Também nunca usa shell nem stdin.

## 4. Fluxo do `backup`

```mermaid
sequenceDiagram
    actor U as Você
    participant C as cli.backup
    participant B as Dnf5Backend
    participant K as classify
    participant R as render
    participant FS as generated/

    U->>C: distro-recovery backup
    C->>B: detect()
    C->>C: load_decisions() e parse_brewfile()
    C->>B: installed(), baseline(), installed_groups()
    C->>B: history_user_installs(), leaves()
    C->>K: classify_all(pacotes, Context)
    K-->>C: HIGH / REVIEW / EXCLUDED por pacote
    C->>B: repos(repos necessários)
    C->>C: resolve_gpgkeys() e split_repos()
    C->>R: render_fedora / render_pending / render_meta
    R-->>FS: fedora.yml · pending.yml · meta.yml
    C-->>U: resumo (exit 0, ou 2 se houver pendências)
```

Passo a passo no código (`cli.py`, função `backup`):

1. **Coleta** (somente leitura): pacotes instalados, baseline, grupos, histórico, leaves.
2. **Classificação**: cada pacote vira `HIGH`, `REVIEW` ou `EXCLUDED` (seção 5).
3. **Repositórios**: descobre quais repos os pacotes usam e monta uma receita para cada um
   (seção 6).
4. **Gravação**: reescreve `generated/` por inteiro.
5. **Relatório** e código de saída.

## 5. A classificação: "este pacote foi escolha minha?"

O DNF registra um *motivo* (`reason`) para cada pacote: `User` (você pediu), `Group` (veio de
um grupo), `Dependency` / `Weak Dependency` (veio de carona). O classificador cruza isso com
o histórico, a baseline e as suas decisões. A primeira regra que casar vence
(`classify._decide`):

```mermaid
flowchart TD
    S([pacote]) --> A{em exclude?}
    A -- sim --> X1[EXCLUDED<br/>user_excluded]
    A -- não --> B{em include?}
    B -- sim --> H1[HIGH<br/>user_included]
    B -- não --> C{regra fixa?<br/>kernel · firmware · langpack…}
    C -- sim --> X2[EXCLUDED<br/>system_rule]
    C -- não --> D{reason é<br/>Dependency/Weak?}
    D -- sim --> X3[EXCLUDED<br/>dependency]
    D -- não --> E{reason é Group?}

    E -- sim --> E1{grupo na baseline?}
    E1 -- sim --> X4[EXCLUDED<br/>baseline_group]
    E1 -- não --> E2{grupo instalado<br/>fora da baseline?}
    E2 -- sim --> X5[EXCLUDED<br/>via_group<br/>o @grupo já cobre]
    E2 -- não --> E3{veio da imagem?}
    E3 -- sim --> X6[EXCLUDED<br/>baseline]
    E3 -- não --> R1[REVIEW<br/>user_unconfirmed]

    E -- "não (reason User)" --> F{na baseline?}
    F -- sim --> F1{você o instalou<br/>depois da imagem?}
    F1 -- sim --> P1[candidato REVIEW<br/>in_baseline_but_user]
    F1 -- não --> X7[EXCLUDED<br/>baseline]
    F -- não --> G{no histórico<br/>fora da imagem?}
    G -- sim --> P2[candidato HIGH<br/>user_history]
    G -- não --> G1{só a imagem<br/>instalou?}
    G1 -- sim --> X8[EXCLUDED<br/>baseline]
    G1 -- não --> G2{é leaf?}
    G2 -- sim --> P3[candidato HIGH<br/>user_leaf]
    G2 -- não --> P4[candidato REVIEW<br/>user_unconfirmed]

    P1 & P2 & P3 & P4 --> L{rpm local<br/>@commandline?}
    L -- sim --> R2[REVIEW<br/>local_rpm]
    L -- não --> M{nome no Brewfile?}
    M -- sim --> R3[REVIEW<br/>duplicated_in_brew]
    M -- não --> FIN([mantém o candidato])
```

Destino de cada resultado:

| Resultado | Vai para | Significado |
|---|---|---|
| `HIGH` | `generated/fedora.yml` | será instalado no restore |
| `REVIEW` | `generated/pending.yml` | precisa da sua decisão (veja `reason_code`) |
| `EXCLUDED` | só o log (`--verbose`) | sistema, dependência ou coberto por grupo |

Conceitos usados:

- **Baseline**: o que a instalação "de fábrica" do Fedora já traz (grupos obrigatórios e padrão
  do ambiente instalado + `kernel*`, firmwares etc.). Calculada em `Dnf5Backend.baseline()`.
- **Transações de imagem**: as primeiras transações do histórico podem ser a montagem da imagem
  do Fedora (todos os pacotes vêm de um repo cujo id é um hash de 32 hex). Elas *não* contam
  como ação sua.
- **Leaf**: pacote de que nenhum outro depende. Um `User` que é leaf, fora do histórico, é
  provavelmente escolha sua.

### Quando um pacote cai em `pending.yml` (como `bat`, `eza`, `rust`)

Se o nome também está no `~/.Brewfile`, o resultado é `REVIEW / duplicated_in_brew`: a
ferramenta não sabe se você quer instalar via DNF ou só via brew. A saída é decidir no
`decisions.yml` (`exclude` para ficar só no brew, `include` para instalar via DNF).

## 6. Repositórios

Necessários = `from_repo` de todos os pacotes instalados, menos os oficiais e a mídia de
instalação (`@System`, `@commandline`, hash de 32 hex). Para cada um, `Dnf5Backend.repos()` lê
os arquivos `/etc/yum.repos.d/*.repo`, descobre o **dono** de cada arquivo (`rpm -qf`) e o dono
define o tipo (`_spec`):

```mermaid
flowchart TD
    S([repo necessário]) --> O{dono do .repo}
    O -- "fedora-repos*" --> OF[official<br/>ignorado]
    O -- "id começa com copr:" --> CO[copr<br/>dono/projeto]
    O -- "fedora-workstation-repositories" --> TP[third-party<br/>Chrome, RPM Fusion…]
    O -- "pacote *-release" --> RP[release-package<br/>URL com releasever]
    O -- "outro pacote ou ninguém" --> RF[repofile<br/>URL + chave GPG]
    TP & RP & CO & RF --> W{avisos bloqueantes?<br/>credencial na URL ·<br/>gpgcheck=0 ·<br/>chave só file://}
    W -- sim --> PEN[pending.yml]
    W -- não --> OK[fedora.yml]
```

Detalhes importantes:

- URLs são normalizadas: a versão fixa vira `$releasever` e a arquitetura `$basearch`.
- **Chaves GPG**: guardamos o *endereço* oficial, nunca a chave nem um fingerprint fixo; o
  restore baixa e importa. Se o `.repo` perdeu o endereço, `repokeys.py` consulta a tabela
  `KNOWN_GPGKEYS` ou o override `repos.gpgkey` do `decisions.yml`.
- Se o mesmo id está em dois arquivos `.repo`, vale o que tem chave remota (aviso
  `duplicate_repo_id`, só informativo).

## 7. Os arquivos de dados

```
~/.config/distro-recovery/
├── decisions.yml          # VOCÊ edita (criado por `init`)
├── generated/             # a FERRAMENTA escreve, a cada backup
│   ├── fedora.yml         #   family, repos, groups, packages
│   ├── pending.yml        #   packages (+reason_code), repos, missing_repos
│   └── meta.yml           #   versão, data/hora e contagens
└── ansible/               # só com `backup --ansible`: playbook + vars.yml
```

`decisions.yml` (todas as chaves são opcionais):

| Chave | Efeito |
|---|---|
| `include` | força um pacote a entrar no restore |
| `exclude` | impede um pacote de ser restaurado (vence `include`; estar nos dois = erro) |
| `repos.exclude` | ignora repositórios por id |
| `repos.gpgkey` | informa o endereço (https) da chave de um repo |
| `swaps` | trocas `from -> to` (`dnf5 swap --allowerasing`), a única forma de remover algo |
| `renames` | lido e validado, mas ainda não usado no restore |

Determinismo: strings sempre entre aspas, listas ordenadas, ordem fixa de chaves. Duas execuções
sem mudança no sistema geram arquivos idênticos (exceto `meta.yml`, que tem data).

## 8. Fluxo do `restore`

```mermaid
sequenceDiagram
    actor U as Você
    participant C as cli.restore
    participant M as merge.build_vars
    participant A as ansible-playbook
    U->>C: distro-recovery restore [--check]
    C->>C: lê generated/fedora.yml e decisions.yml
    C->>M: build_vars(generated, decisions)
    M-->>C: dr_family · dr_repos · dr_groups · dr_packages · dr_swaps
    C->>C: avisa se pending.yml tem itens (NÃO serão restaurados)
    C->>A: site.yml com vars.yml temporário
    A->>A: role repos → swaps → groups → packages
```

Precedência ao montar as variáveis (`merge.build_vars`):
`pacotes = (generated ∪ include) − exclude`.

Roles do Ansible, em ordem (`src/distro_recovery/ansible/`):

```mermaid
flowchart LR
    R1["1. repos<br/>importa chaves (https)<br/>cria .repo só se nenhum arquivo define<br/>habilita COPR e third-party"]
    R2["2. swaps<br/>só se 'from' está instalado<br/>e 'to' ainda não"]
    R3["3. groups<br/>instala @grupos"]
    R4["4. packages<br/>instala o que existe nos repos<br/>e reporta o que falta"]
    R1 --> R2 --> R3 --> R4
```

O role `packages` primeiro pergunta ao DNF quais nomes existem (`repoquery --available`),
instala a interseção e *relata* os ausentes (com possíveis substitutos), sem falhar.

Garantias de segurança do restore (cobertas por testes estáticos): nunca remove, nunca faz
downgrade, nunca desabilita GPG; `--allowerasing` só aparece no role `swaps`.

## 9. Comandos e códigos de saída

| Comando | O que faz | Saída |
|---|---|---|
| `backup [--ansible] [--dir] [--from-fixtures] [--brewfile] [--verify-keys] [--verbose]` | inventário | 0 ok · 2 há pendências · 1 erro |
| `init [--dir]` | cria `decisions.yml` comentado (nunca sobrescreve) | 0 |
| `restore [--check] [--no-ask-become-pass]` | executa o playbook | 0 ok · 1 erro |
| `review`, `diff`, `doctor` | **ainda não implementados** (dão erro) | 1 |

`--from-fixtures` roda o backup inteiro sobre saídas gravadas, sem tocar no sistema, e exige
`--dir` explícito.

## 10. Testes e ferramentas de apoio

- `tests/` — `pytest`. Fixtures reais em `tests/fixtures/dnf5/` (saídas gravadas de
  comandos). Há testes por `reason_code`, de determinismo do YAML, estáticos do Ansible e da
  CLI.
- `scripts/vm-test.sh` — restore de ponta a ponta numa VM descartável (QEMU). Ver `VM_TEST.md`.
- `yadm-autobackup/` — script e unidades systemd que automatizam o backup dos dotfiles (yadm)
  junto com o `brew bundle dump` e o `distro-recovery backup`.
- `docs/pendencias.md` — decisões ainda abertas.

## 11. Glossário rápido

| Termo | Significado |
|---|---|
| **DNF5** | gerenciador de pacotes do Fedora |
| **reason** | por que o DNF instalou o pacote: User, Group, Dependency, Weak Dependency |
| **leaf** | pacote que nenhum outro pacote exige |
| **baseline** | pacotes que já vêm com a instalação padrão |
| **COPR** | repositórios comunitários do Fedora (`dono/projeto`) |
| **swap** | troca de um pacote por outro numa transação |
| **fixture** | saída de comando gravada, usada nos testes no lugar do sistema real |
| **runner** | função que executa comandos; injetada para poder trocar por fixtures |
| **GPG / gpgkey** | assinatura que garante a origem dos pacotes de um repositório |

## 12. Como estender para outra distribuição

Implemente o `Protocol Backend` (`backends/base.py`): `detect`, `installed`,
`installed_groups`, `baseline`, `history_user_installs`, `leaves`, `repos`. O classificador, o
render e o merge não mudam, porque só enxergam os tipos de `model.py`. Falta ainda um role
Ansible equivalente e a escolha do backend na CLI (hoje fixa em `Dnf5Backend`).
