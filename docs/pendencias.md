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

**O que você pediu.** No restore, importar as chaves da internet, como se faz à mão
(`rpm --import <endereço>` e `gpgkey=<endereço>` no arquivo `.repo`), para sempre pegar a
chave atual.

**O que foi verificado (17/10/2026).**
- **A origem da importação não fica registrada.** O `rpm` guarda só o conteúdo da chave e a
  data; o log do `dnf5` e o histórico não têm o endereço. Não dá para recuperar "de onde a
  chave veio", só "qual chave está instalada".
- **Mas o endereço oficial entrega a chave instalada.** Baixei a chave de 7 endereços
  oficiais (VS Code, VSCodium, WebApp Manager, Android Studio, Google Chrome, Vivaldi,
  1Password) e a impressão digital (fingerprint) de todas é idêntica à instalada.
- Guardar a chave no backup (opção antiga A) fica **descartada**: a chave ficaria velha se o
  fornecedor trocar (o Vivaldi já tem duas, KEY11 e KEY12). Também não vamos fixar o
  fingerprint no restore, pelo mesmo motivo.

**Solução recomendada.**
1. Cada repositório guarda o **endereço oficial da chave** (`gpgkey`), como já faz para os
   repositórios cujo arquivo `.repo` tem endereço (Vivaldi, Google, 1Password, Android Studio).
2. No restore: baixa e importa a chave desse endereço, depois cria o repositório com
   `gpgcheck=1` e o mesmo endereço (equivale ao seu exemplo do VS Code).
3. **COPR** (WebApp Manager, Android Studio): não precisa de endereço, o comando
   `dnf copr enable dono/projeto` já importa a chave certa.
4. Falta resolver só os repositórios cujo arquivo `.repo` perdeu o endereço (VS Code e
   VSCodium, criados pela ferramenta antiga). Proposta: uma **tabela pequena de fornecedores
   conhecidos** dentro da ferramenta (`code` -> `https://packages.microsoft.com/keys/microsoft.asc`,
   `gitlab.com_paulcarroty_vscodium_repo` ->
   `https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/-/raw/master/pub.gpg`), usada só
   quando o `.repo` local não tem endereço, e que você pode sobrescrever no `decisions.yml`.
   Repositório desconhecido sem endereço continua indo para o `pending`.
5. Opcional: o backup poderia conferir, com internet, se o endereço entrega a chave instalada
   e avisar se não.

**Perguntas para você.**
1. Concorda com a solução acima (endereço oficial no backup, chave baixada no restore)?
2. Posso criar a tabela de fornecedores conhecidos com VS Code e VSCodium para começar?
3. Quer a conferência opcional do item 5 (exige internet no backup)?

**Impacto.** Backup (`generated/`) e role `repos` do Ansible.

## 2. Itens de resposta curta
- `chatgpt` (instalado de um RPM baixado) fica fora da lista. Para o backup parar de listá-lo
  como pendência, adicione ao seu `~/.config/distro-recovery/decisions.yml`:

  ```yaml
  exclude:
    - "chatgpt"
  ```
- `--ansible`: ainda sem efeito. Será definido junto com os roles.
