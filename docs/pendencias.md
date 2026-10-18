# Pendências para decidir

## 1. Chaves (assinaturas) dos repositórios — DECIDIDO e implementado

Decisão (17/10/2026): o backup guarda o **endereço oficial da chave**, e o restore baixa a
chave atual da internet e importa, como à mão (`rpm --import <endereço>` e `gpgkey=<endereço>`
no `.repo`). Não guardamos a chave nem fixamos fingerprint, porque os fornecedores trocam de
chave (o Vivaldi já tem duas).

- A origem de uma importação **não fica registrada** no computador (só a chave e a data).
- Os 7 endereços oficiais testados entregam exatamente a chave instalada.
- Repositórios **COPR** não precisam de endereço: `dnf copr enable dono/projeto` importa a chave.
- Se o `.repo` local perdeu o endereço (só `file://`), a ferramenta usa a **tabela de
  fornecedores conhecidos** (`src/distro_recovery/repokeys.py`: VS Code e VSCodium). Você pode
  sobrescrever ou acrescentar no seu `decisions.yml`:

  ```yaml
  repos:
    gpgkey:
      code: "https://packages.microsoft.com/keys/microsoft.asc"
  ```
- Repositório desconhecido sem endereço continua em `pending.yml` (`local_gpgkey`).
- `backup --verify-keys` (opcional, usa internet) baixa cada chave e confere se ela é uma das
  instaladas. Avisa `gpgkey_mismatch` ou `gpgkey_unreachable`, sem mudar o código de saída.
- Chaves `file://` entregues por um pacote (ex.: RPM Fusion) são aceitas como estão.

Pendente para o Ansible: role `repos` usar `rpm_key` com o endereço, `yum_repository` com
`gpgcheck` ligado e `community.general.copr` para os COPR.

## 2. Itens de resposta curta
- `chatgpt` (instalado de um RPM baixado) fica fora da lista. Para o backup parar de listá-lo
  como pendência, adicione ao seu `~/.config/distro-recovery/decisions.yml`:

  ```yaml
  exclude:
    - "chatgpt"
  ```

## 3. Ansible: pontos a validar na primeira execução real
- **Repositórios duplicados neste computador.** Os arquivos `fedora-recovery-*.repo`
  (ferramenta antiga) já definem `code`, `vivaldi`, `vscodium` etc. O playbook cria os arquivos
  novos (`code.repo`...). Rodar o restore **neste mesmo computador** deixaria ids repetidos;
  em um computador limpo não há problema. Remover os arquivos antigos é decisão sua (a
  ferramenta nunca remove nada).
- `vivaldi.repo` do próprio Vivaldi seria reescrito com o mesmo endereço e `gpgcheck` ligado.
- Primeira execução recomendada: `distro-recovery restore --check` e depois `restore`, em uma
  máquina de teste.
- `release-package` (pacote `*-release`) instala o pacote pelo nome; se ele vier de uma URL
  fora dos repositórios, será preciso um endereço (ainda não há caso real).
