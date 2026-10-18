# Pendências para decidir

## 1. Repositórios que precisam de chave (assinatura) para funcionar

**Situação.** Cada repositório externo usa uma "chave" para provar que os pacotes são
autênticos. Ao adicionar o repositório à mão, essa chave também é importada. Hoje o backup
anota a chave só quando ela tem um endereço na internet. Três repositórios apontam para um
arquivo que existe apenas neste computador (criado pela ferramenta antiga `fedora-recovery`):

| Repositório | Chave que o computador já tem |
|---|---|
| `code` (Visual Studio Code) | Microsoft (Release signing) |
| `gitlab.com_paulcarroty_vscodium_repo` (VSCodium) | Pavlo Rudyi |
| `copr:...:kylegospo:webapp-manager` (WebApp Manager) | kylegospo_webapp-manager |

Por isso eles ficam em `pending.yml` com o aviso `local_gpgkey` e não entram no backup.

**O que você pediu.** No restore, importar as chaves da mesma forma que se faz ao adicionar o
repositório manualmente.

**Descoberta.** Dá para ler a chave pública completa de cada repositório direto do
computador, sem internet (`rpm -q gpg-pubkey-... --qf '%{DESCRIPTION}'`). Chave pública não
é segredo, então pode ser guardada junto com as listas.

**Opções.**
- **A. Guardar a chave pública no backup (recomendada).** O restore importa a chave e confere
  o "fingerprint" (a impressão digital dela). Funciona sem internet e não depende de o
  fornecedor manter o endereço da chave. Resolve também os repositórios que hoje ficam sem
  fingerprint. Custo: o backup passa a guardar o texto de umas 10 chaves.
- **B. Guardar só o endereço oficial da chave de cada fornecedor.** Arquivos menores, mas
  precisamos descobrir e conferir cada endereço, e ele pode mudar ou sair do ar.
- **C. Misturar:** chave guardada (A), e o endereço quando existir, só para informação.

**Perguntas para você.**
1. Pode ser a opção A?
2. Guardar todas as chaves de terceiros que estão no computador ou só as dos repositórios
   que entram no backup? (Sugestão: só as dos repositórios que entram.)
3. Como saber qual chave pertence a qual repositório? Para COPR já casamos pelo nome da chave.
   Para os demais (Microsoft, Vivaldi, Google, 1Password…) a ligação precisa ser confirmada
   por você ou por uma regra de nomes. Podemos listar as chaves no restore e importar todas.

**Impacto.** Roles do Ansible (`repos`) e o backup (`generated/`). Nada é feito enquanto você
não decidir.

## 2. Itens de resposta curta
- `chatgpt` (instalado de um RPM baixado) fica fora da lista. Para o backup parar de listá-lo
  como pendência, adicione ao seu `~/.config/distro-recovery/decisions.yml`:

  ```yaml
  exclude:
    - "chatgpt"
  ```
- `--ansible`: ainda sem efeito. Será definido junto com os roles.
