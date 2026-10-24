# mesh-alerts

Serviço Python que identifica alertas oficiais da Defesa Civil que atingem municípios configurados e prepara boletins de previsão do tempo do INMET para distribuição por Meshtastic.

O projeto reúne três ferramentas: validação regional de alertas CAP, serviço contínuo de alertas CAP com fila persistente de saída e agendador de boletins de previsão do INMET. **O INMET é usado para previsão do tempo, não como fonte de alertas meteorológicos.**

## Requisitos

- Python 3.10 ou posterior.
- Acesso à internet para consultar as fontes oficiais.
- Para transmissão, um dispositivo Meshtastic acessível por TCP.

As dependências Python são instaladas pelo pacote: Shapely, Meshtastic, PyYAML e defusedxml.

## Instalação

Na raiz do repositório:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Para trabalhar no código, instale em modo editável:

```bash
python -m pip install -e .
```

## Configuração

Edite [`config.yaml`](config.yaml). A configuração incluída monitora Aramina, Buritizal, Guará, Igarapava, Ituverava e Miguelópolis, em São Paulo. Ajuste municípios, fontes, armazenamento, intervalo de consulta e parâmetros Meshtastic para seu ambiente.

### Canais Meshtastic

A configuração separa os boletins por canal: **INMET usa o índice `1`** (`inmet.canal`) e **alertas CAP usam o índice `2`** (`meshtastic.canal`). Na configuração padrão, o índice `0` não é usado para envios automáticos deste projeto. Os índices podem ser alterados no YAML; `mesh-alerts-inmet --channel-index N` sobrescreve o canal do INMET para uma execução. O serviço CAP também aceita `--channel-index N` ou `MESH_ALERTS_CHANNEL_INDEX` como sobrescrita.

Essa separação controla os envios feitos pelo software, não impede que outros participantes com acesso ao canal enviem mensagens manualmente.

Os comandos usam `config.yaml` por padrão e aceitam `--config /caminho/arquivo.yaml` quando aplicável.

## Comandos

| Comando | Finalidade |
| --- | --- |
| `mesh-alerts-validate` | Consulta o feed CAP e identifica alertas que intersectam os municípios configurados. Não transmite mensagens. |
| `mesh-alerts-service` | Processa alertas CAP continuamente e envia mensagens pelo Meshtastic via TCP. |
| `mesh-alerts-inmet` | Prepara ou agenda boletins de previsão do INMET. Simula por padrão. |

Validar o feed atual:

```bash
mesh-alerts-validate
```

Executar um ciclo do serviço CAP:

```bash
mesh-alerts-service --once
```

Iniciar o serviço CAP contínuo:

```bash
mesh-alerts-service
```

Simular o boletim da manhã para a data local atual:

```bash
mesh-alerts-inmet --period manha
```

Executar o agendador INMET em simulação:

```bash
mesh-alerts-inmet --loop
```

O envio real dos boletins INMET exige `--send` e o endereço TCP do dispositivo, por exemplo:

```bash
mesh-alerts-inmet --period manha --send --tcp-host 192.0.2.10
```

O endereço acima pertence à faixa reservada para documentação e deve ser substituído pelo endereço real. O canal do INMET será o índice `1` configurado em `inmet.canal`, salvo se `--channel-index` for informado. Antes de habilitar o envio, confirme host, porta, canal e destino. O serviço CAP pode transmitir mensagens reais quando iniciado; o validador regional não transmite.

## Dados e execução como serviço

O serviço CAP persiste estado e fila de saída em SQLite e grava mensagens preparadas no diretório de saída configurado. Os caminhos padrão estão em `config.yaml`; variáveis de ambiente documentadas em [`deploy/mesh-alerts.env.example`](deploy/mesh-alerts.env.example) podem sobrescrever parâmetros de implantação.

Há exemplos de unidades systemd em [`deploy/`](deploy/). A unidade CAP transmite pelo Meshtastic configurado; a unidade INMET permanece em simulação.

## Documentação técnica

A documentação para quem desenvolve ou mantém o projeto fica em [`docs/`](docs/README.md), com a [arquitetura](docs/architecture.md) e as [instruções de desenvolvimento e testes](docs/development.md).

## Licença

Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE).
