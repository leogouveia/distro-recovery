# Backup da HOME

`backup-home` cria um arquivo **tar + zstd** da HOME. É independente do inventário de
pacotes (`backup`) e funciona no Linux com GNU tar e zstd, inclusive fora do Fedora.
Não instala pacotes nem usa sudo. Instale `tar` e `zstd` pelo gerenciador da sua distro.

Escolha uma pasta existente **fora da HOME**, preferencialmente em outro disco. O nome
final deve terminar em `.tar.zst` e ainda não existir. Não há sobrescrita automática.
A publicação atômica exige suporte a hard links no destino (por exemplo, ext4 ou Btrfs);
FAT/exFAT não oferecem esse recurso.

```bash
# Confira origem, destino, exclusões e comando antes de executar:
distro-recovery backup-home -o /mnt/backup/home-2026-10-21.tar.zst --dry-run

# Depois de revisar o plano e fechar aplicativos que gravam dados:
distro-recovery backup-home -o /mnt/backup/home-2026-10-21.tar.zst --verbose

# Sem reinstalar a ferramenta, a partir deste repositório:
uv run distro-recovery backup-home -o /mnt/backup/home-2026-10-21.tar.zst --dry-run
```

O terminal mostra as três etapas: criação, teste zstd e leitura do tar. Com `--verbose`
(ou `-v`), lista também os itens processados durante a criação, inclusive diretórios.
A listagem não é uma confirmação de sucesso; aguarde a mensagem final. Se arquivos mudarem
durante a leitura, o resultado será "concluído com avisos", com a integridade do arquivo
verificada. As mensagens do tar são exibidas ao terminar a criação.
Não há porcentagem estimada: o comando não faz uma leitura prévia de toda a HOME.

`--source PASTA` permite arquivar outra HOME ou uma pasta de teste. A origem padrão é
`Path.home()` (normalmente `$HOME`): execute como seu usuário. Não execute como root para
arquivar sua HOME, pois a origem padrão passaria a ser a HOME do root.

## O que fica de fora

Por padrão são excluídos:

- `.cache` na raiz da origem;
- `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache` e `.ruff_cache`, em qualquer nível;
- `.npm/_cacache`, `.npm/_logs` e `.cargo/registry/cache`, na raiz da origem.

Configurações, documentos, fotos, `.ssh`, `.gnupg`, `.config`, `.local/share`, `.git`,
ambientes `.venv`, SDKs e dados de aplicativos permanecem incluídos. `build` e `dist`
**não são excluídos por padrão**: podem conter trabalho sem outra cópia. Links simbólicos
são guardados como links; seu conteúdo externo à origem não é copiado. Sistemas de arquivos
montados dentro da origem são percorridos. Sockets não são arquivados pelo tar.

Os padrões usam as regras de exclusão do **GNU tar**: curingas habilitados, correspondência
em qualquer nível e `*` pode atravessar `/`. Use `./` para ancorar à raiz da origem.
Excluir uma pasta exclui seu conteúdo. Não há regras de reinclusão ou sintaxe de `.gitignore`.

```bash
# Exclusões adicionais, deliberadas e específicas:
distro-recovery backup-home -o /mnt/backup/home.tar.zst \
  --exclude './projetos/app/dist' --exclude './projetos/app/.venv' --dry-run

# Incluir também todos os caches padrão:
distro-recovery backup-home -o /mnt/backup/home-completa.tar.zst --no-default-excludes
```

Para reutilizar sua seleção, crie um YAML pessoal (não precisa adicioná-lo ao repositório):

```yaml
exclude:
  - "./projetos/app/dist"
  - "./projetos/app/build"
  - "./projetos/app/.venv"
```

```bash
distro-recovery backup-home -o /mnt/backup/home.tar.zst \
  --config ~/.config/distro-recovery/home-backup.yml --dry-run
```

O arquivo só é lido quando `--config` é informado. Ele aceita apenas `exclude`, uma lista de
strings. Suas regras e os `--exclude` repetidos se somam aos padrões; com
`--no-default-excludes`, apenas as regras explícitas são usadas. Confira a lista exibida antes
de executar. Caches excluídos precisarão ser baixados ou reconstruídos após a restauração.

## Integridade e metadados

O tar guarda permissões, UID/GID, mtime, links simbólicos e hard links internos, arquivos
esparsos, ACLs, atributos estendidos e contextos SELinux (quando disponíveis e legíveis).
Não segue links simbólicos. A leitura pode atualizar o horário de acesso (atime), conforme
a montagem do sistema de arquivos. Não usa `--atime-preserve=system`: essa opção pode impedir
a leitura de itens acessíveis que pertençam a outro usuário. Não preserva ctime nem promete
preservar data de criação ou metadados que
o usuário não consegue ler. Restaurar donos, ACLs e atributos de segurança depende das
permissões e do sistema de arquivos de destino.

O arquivo temporário é criado no destino com permissão **0600**. Antes de publicar o nome
final, o comando testa todo o fluxo zstd e lê a estrutura do tar, sem extrair arquivos. Erros
de criação ou verificação removem o temporário; um arquivo final existente nunca é substituído,
mesmo se aparecer durante a execução. Uma interrupção normal também limpa o temporário;
queda de energia ou SIGKILL podem deixar um `.backup-home-*.partial` incompleto.

O comando retorna **0** após publicar o arquivo verificado (também quando há avisos de
arquivos alterados durante a leitura) ou mostrar um dry-run válido;
**1** em falhas de validação, leitura, compressão ou verificação. Erros de uso da CLI retornam
**2**. O **dry-run só mostra o plano**: não percorre a HOME, não estima espaço, nem verifica
permissões de leitura/gravação de todos os arquivos. A leitura completa acontece na execução.

Este comando permite fazer o backup com aplicativos em execução. Se o tar detectar arquivos
mudando ou encolhendo durante a leitura, o backup continua e termina com avisos, desde que
sejam apenas esses avisos e sockets ignorados. Os itens afetados são identificados nas mensagens
do tar. Eles podem conter uma cópia inconsistente ou, se encolherem, preenchimento com zeros;
não são removidos automaticamente do arquivo. Para obter uma cópia consistente de bancos
de dados e aplicativos, feche-os antes do backup.

A verificação confirma que o arquivo compactado e a estrutura tar podem ser lidos; não
garante consistência dos aplicativos nem compara cada conteúdo com a origem. Reserve espaço para o
arquivo compactado: o tamanho depende dos dados e a falta de espaço causa erro.

O arquivo contém segredos como chaves privadas e sessões. Ele não é criptografado; proteja o
disco e o transporte. Mantenha outra cópia em local separado para recuperação de falha do disco.

## Conferir e ensaiar a restauração

```bash
zstd --test /mnt/backup/home-2026-10-21.tar.zst
tar --list --zstd --file /mnt/backup/home-2026-10-21.tar.zst

# Pasta nova e vazia: não extrair sobre a HOME em uso.
mkdir /mnt/backup/ensaio-home
tar --extract --zstd --file /mnt/backup/home-2026-10-21.tar.zst \
  --directory /mnt/backup/ensaio-home --same-permissions --acls --xattrs
```

Confira os documentos e configurações extraídos antes de planejar a restauração definitiva.
Para recuperar proprietários originais como administrador, o GNU tar oferece `--same-owner`
e `--numeric-owner`; use apenas quando os UID/GID da máquina de destino corresponderem aos
originais. Contextos SELinux de outra instalação podem exigir `restorecon` conforme a política
do sistema de destino. O comando `restore` do distro-recovery continua restaurando pacotes;
ele não extrai este arquivo.

## Avisos e erros de leitura

`socket ignored` é um aviso normal: sockets são pontos de comunicação de aplicativos
em execução e são recriados por eles. Não é necessário copiá-los.

`Cannot open: Operation not permitted` ou `Permission denied` significa que um item não pôde
ser aberto. A versão inicial usava `--atime-preserve=system`, que pode provocar isso mesmo
quando a leitura normal é permitida, se o item pertence a outro usuário. A versão atual
remove essa exigência sem alterar permissões, donos ou SELinux. Se o erro persistir,
verifique as permissões e restrições específicas do caminho. Não há exclusão automática
por erro de leitura e não se usa `--ignore-failed-read`. Se decidir deixar algo de fora,
use um `--exclude './caminho/exato'` explícito e revise com `--dry-run`.

O retorno 1 na criação só é tolerado quando as mensagens indicam exclusivamente arquivos
modificados/encolhidos durante a leitura (e eventualmente sockets ignorados). O compressor
também pode retornar 1; por isso o código de saída sozinho não é considerado sucesso.
Erros de permissão, arquivos que não puderam ser abertos, diagnósticos não reconhecidos e
falhas de compressão ou verificação continuam interrompendo a execução: o comando informa
a etapa, remove o arquivo parcial e não publica o backup.
