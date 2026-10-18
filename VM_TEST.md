# Teste do restore em uma VM

O `restore` instala pacotes e mexe em repositórios, então **não deve ser testado pela primeira
vez no seu computador**. O script `scripts/vm-test.sh` cria uma VM Fedora nova e descartável, roda
o `restore` dentro dela e confere o resultado. Quando termina, a VM é apagada.

Nada acontece no seu computador além de arquivos em `~/.cache/distro-recovery-vm/`. O script
não usa `sudo`, não usa libvirt e não instala nada no host.

## Como rodar

```bash
scripts/vm-test.sh                       # dados de exemplo (tests/vm/smoke), cerca de 5-10 min
scripts/vm-test.sh --data ~/.config/distro-recovery   # o seu backup de verdade
```

Na primeira vez ele baixa a imagem oficial do Fedora Cloud (cerca de 500 MB) e a guarda em
cache. Nas próximas, só reaproveita.

No final aparece `RESULTADO: PASSOU` (código de saída 0) ou `RESULTADO: FALHOU` (código 1).

| Opção | O que faz |
|---|---|
| `--data DIR` | Dados a restaurar: `DIR/generated/fedora.yml` e, se existir, `DIR/decisions.yml`. Só leitura. |
| `--release N` | Versão do Fedora da VM (padrão 44). |
| `--memory MB`, `--cpus N` | Recursos da VM (padrão 4096 MB e 2 CPUs). |
| `--keep` | Não apaga a VM no final. Mostra o comando `ssh` para entrar e como desligar. |
| `--no-kvm` | Sem aceleração (muito mais lento; use só se não tiver `/dev/kvm`). |
| `--refresh-image` | Baixa a imagem base de novo. |
| `--cache-dir DIR` | Onde guardar a imagem (padrão `~/.cache/distro-recovery-vm`). |

### O que precisa estar instalado no seu computador

`qemu-system-x86_64`, `genisoimage` (ou `mkisofs`/`xorriso`), `ssh`, `scp`, `ssh-keygen`,
`curl`, `gpg`, `python3` e `uv`. Para ter aceleração, o seu usuário precisa de acesso a
`/dev/kvm`. Se faltar algo, o script diz o que instalar.

## Como funciona

```
seu computador                                 VM (Fedora, descartável)
──────────────                                 ────────────────────────
1. baixa a imagem oficial e confere
   assinatura GPG + SHA256 (cache)
2. empacota a ferramenta (uv build)
3. cria um disco "por cima" da imagem
   (a imagem base nunca é alterada)
4. cria um disquete cloud-init com um
   usuário "tester" e uma chave ssh nova
5. liga o QEMU (ssh só em 127.0.0.1)  ───────▶ 6. boot, cloud-init cria o usuário
7. copia ferramenta, dados e guest.sh ───────▶
8. guest.sh setup: instala ansible-core, a coleção, e a ferramenta
9. guest.sh test: roda os testes abaixo, como usuário comum com sudo
10. mostra o resultado, apaga a VM    ◀─────── (logs voltam se falhar)
```

- **Imagem base:** Fedora Cloud Base (Generic) oficial, de `dl.fedoraproject.org`. O script
  confere a assinatura do arquivo CHECKSUM com a chave do Fedora que já está no seu sistema
  (`/etc/pki/rpm-gpg`) e depois o SHA256 da imagem.
- **Rede:** modo "usuário" do QEMU (sem `root`). A VM acessa a internet normalmente. Só uma porta
  do seu computador é aberta, em `127.0.0.1`, para o ssh.
- **Isolamento:** a VM tem o próprio disco (cópia por cima da imagem) e não vê seus arquivos. Só
  recebe uma cópia dos dados que você indicou em `--data` (`generated/fedora.yml` e
  `decisions.yml`; o resto da pasta não é enviado).

## O que o teste verifica

O roteiro é o `tests/vm/guest.sh`. Ele prepara um pacote "canário" (por padrão `tree`) e
tira uma foto do sistema antes e depois.

1. **`restore --check` não altera nada.** Mesmos pacotes e mesmos arquivos `.repo` de antes.
2. **`restore` termina com código 0.**
3. **Repositórios.** Cada repositório dos dados está habilitado, e os criados por URL têm
   `gpgcheck=1`. Nenhum `.repo` novo com `gpgcheck=0`.
4. **Chaves.** Se há chaves por endereço, o número de chaves GPG do sistema aumentou (as chaves
   foram baixadas e importadas).
5. **Grupos.** Cada grupo está instalado.
6. **Pacotes.** Cada pacote está instalado **ou** foi reportado como ausente nos repositórios
   (sem derrubar o restore). Os do `exclude` não foram instalados.
7. **Nunca remove.** Nenhum pacote que já existia sumiu, e o canário continua lá.
8. **Idempotência.** Uma segunda execução termina com código 0 e `changed=0`.

### Dados de exemplo (`tests/vm/smoke`)

Pequenos e públicos, de propósito:

| Item | Para quê |
|---|---|
| Repositório do 1Password + `1password-cli` | Testa um repositório externo real, com a chave baixada da internet. |
| Grupo `rpm-development-tools` | Testa a instalação de grupos. |
| `htop` | Pacote comum. |
| `tmux` em `generated` **e** em `exclude` | Prova que `exclude` vence. |
| `zsh` só em `include` | Prova que `include` adiciona ao gerado. |
| `pacote-que-nao-existe-xyz` | Deve ser reportado como ausente, sem falhar. |

### O que este teste **não** cobre

- **`swaps`** (troca de pacotes): exigem repositórios extras (como o RPM Fusion). Se os seus
  dados tiverem `swaps`, o teste só avisa que remoções são esperadas.
- **Downgrade:** garantido por teste estático do playbook, não por execução.
- **Repositórios COPR** só são exercitados se estiverem nos dados (`--data`).
- **Hardware:** drivers (NVIDIA etc.) dependem da máquina.

## Usando o seu backup de verdade

```bash
distro-recovery backup --ansible --verify-keys
scripts/vm-test.sh --data ~/.config/distro-recovery
```

Pode demorar bastante (centenas de pacotes e repositórios). Pacotes que não existirem mais nos
repositórios aparecem como `INFO ... reportado, sem falhar`. Isso é esperado e não reprova o teste.

## Se falhar

1. Os logs são salvos em `~/.cache/distro-recovery-vm/last-failure/`:
   `test.log` (saída do roteiro), `setup.log` (preparação) e `logs/` (saída completa do
   Ansible: `check.log`, `restore1.log`, `restore2.log`).
2. Para investigar dentro da VM, rode com `--keep` e use o comando `ssh` mostrado. Para
   desligar: o script imprime o comando exato.
3. Erros comuns:
   - *"não consegui listar ..."*: sem internet, ou a versão do Fedora não existe em `--release`.
   - *"assinatura do arquivo CHECKSUM inválida"*: falta o pacote `fedora-gpg-keys` para a versão
     escolhida (use a mesma versão do seu Fedora ou instale a chave).
   - *"a VM não respondeu ao ssh a tempo"*: sem KVM a VM é lenta; veja o console em
     `~/.cache/distro-recovery-vm/run-*/serial.log` (impresso ao falhar).

## Limpeza

A execução apaga a própria pasta de trabalho. Para liberar também a imagem em cache:

```bash
rm -rf ~/.cache/distro-recovery-vm
```
