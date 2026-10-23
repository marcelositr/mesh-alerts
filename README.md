# mesh-alerts

Serviço Python para identificar alertas oficiais da Defesa Civil que intersectam municípios configurados e preparar boletins de previsão do INMET para distribuição via Meshtastic.

## Funcionalidades

- Leitura e interpretação temporal do feed CAP da Defesa Civil, incluindo alertas, atualizações e cancelamentos.
- Interseção geográfica com limites municipais do IBGE, usando polígonos e círculos CAP.
- Estado persistente SQLite e fila de saída para reduzir duplicações e preservar envios de resultado incerto.
- Consulta e formatação compacta da previsão diária do INMET por município.
- Agendamento persistente dos boletins INMET, em simulação por padrão.
- Instalação como pacote Python com comandos de terminal e unidades systemd de exemplo.

O projeto não consulta alertas meteorológicos do INMET. Para a parte meteorológica, usa somente a previsão do INMET.

## Requisitos

- Python 3.10 ou posterior.
- Acesso à internet para consultar as APIs oficiais.
- Para transmissão, um dispositivo Meshtastic acessível por TCP e configuração de host, porta, canal e destino.

## Instalação para desenvolvimento

Na raiz do repositório:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

O pacote define estes comandos:

| Comando | Função |
| --- | --- |
| `mesh-alerts-validate` | Consulta o feed CAP e valida as interseções geográficas |
| `mesh-alerts-service` | Executa o serviço contínuo CAP para Meshtastic |
| `mesh-alerts-inmet` | Executa ou agenda boletins de previsão do INMET |

Também é possível invocar os módulos com `python -m mesh_alerts.region`, `python -m mesh_alerts.service` e `python -m mesh_alerts.inmet_cli`.

## Configuração

Edite [config.yaml](config.yaml) para definir os municípios, a região, as fontes, os parâmetros do serviço e o agendamento INMET. A configuração inicial monitora:

| Município | Código IBGE |
| --- | ---: |
| Aramina | `3503000` |
| Buritizal | `3508207` |
| Guará | `3517703` |
| Igarapava | `3520103` |
| Ituverava | `3524105` |
| Miguelópolis | `3529708` |

Os códigos IBGE devem ser mantidos como strings de sete dígitos. A seção `meshtastic` define host TCP, porta, canal e destino; a seção `servico` define intervalo, timeouts, tentativas, banco SQLite e diretório de saída.

## Validação regional CAP

Consultar o feed CAP atual e as malhas municipais atuais do IBGE:

```bash
mesh-alerts-validate
```

A validação não transmite mensagens. Ela lista os alertas vigentes que intersectam os municípios configurados e emite diagnósticos sobre geometrias CAP problemáticas e metadados geográficos incompatíveis.

Para analisar um arquivo XML CAP local:

```bash
mesh-alerts-validate --feed /caminho/para/defesa-civil-cap.xml
```

Para usar uma referência GeoJSON local de limites municipais, sem consultar a API de malhas do IBGE:

```bash
mesh-alerts-validate --boundaries tests/fixtures/ibge-sp-municipios-api.geojson
```

Esse último comando ainda consulta o feed CAP atual. A referência GeoJSON versionada serve para validação repetível e não substitui uma atualização periódica dos limites oficiais. O acesso às fontes externas pode falhar por indisponibilidade ou timeout; nesses casos, a ferramenta informa o erro em vez de inventar ou substituir dados.

## Serviço contínuo CAP

O serviço CAP consulta as fontes, persiste o estado no SQLite e tenta enviar mensagens pendentes ao destino Meshtastic configurado. **Ao contrário do validador, esse serviço pode transmitir mensagens reais.** Revise host, porta, canal, destino e permissões antes de iniciá-lo.

Para executar um único ciclo de processamento:

```bash
mesh-alerts-service --once
```

Para execução contínua:

```bash
mesh-alerts-service
```

O banco e o diretório de saída são configurados em `config.yaml`; variáveis de ambiente podem sobrescrever esses valores. Para implantação com systemd, use caminhos absolutos graváveis pelo usuário de serviço, como `/var/lib/mesh-alerts/alerts.sqlite3` e `/var/lib/mesh-alerts/output`.

Após uma consulta bem-sucedida ao IBGE, o serviço grava um cache GeoJSON validado junto ao banco. Se a API ficar indisponível, o serviço pode continuar usando esse cache por até 365 dias, desde que ele contenha todos os municípios configurados e geometrias válidas. O primeiro carregamento ainda exige acesso ao IBGE ou um cache previamente preparado. O serviço registra um aviso quando recorre ao cache; ele não aceita automaticamente caches mais antigos.

A fila persistente evita enfileiramentos duplicados e não repete automaticamente uma transmissão cujo resultado ficou incerto. Isso reduz duplicações, mas não garante entrega exatamente uma vez. Confirme manualmente qualquer item marcado como incerto antes de decidir se deve ser reenviado.

## Boletins de previsão INMET

Os horários padrão são 06:00 (manhã), 12:00 (tarde) e 18:00 (noite), no fuso `America/Sao_Paulo`. O agendador seleciona apenas a previsão da data local atual e do período correspondente. Se a API não fornecer o período de hoje, a previsão é considerada indisponível; não há fallback para dias futuros.

Simular um boletim pontual, sem transmitir:

```bash
mesh-alerts-inmet --period manha
```

Executar o agendador continuamente, ainda em simulação:

```bash
mesh-alerts-inmet --loop
```

O envio real exige a opção explícita `--send` e um host TCP informado. Exemplo ilustrativo, usando um endereço reservado para documentação:

```bash
mesh-alerts-inmet --period manha --send --tcp-host 192.0.2.10
```

Substitua o endereço de exemplo pelo endereço real do dispositivo Meshtastic e confirme porta, canal e destino. Não habilite transmissão real antes de validar o transporte e o comportamento no ambiente pretendido.

O estado do agendador INMET é persistido em SQLite por data, período e município. Uma falha ou resultado incerto de transporte não provoca reenvio automático. O arquivo `deploy/systemd/mesh-alerts-inmet.service` é configurado intencionalmente em modo de simulação, sem `--send`.

## Implantação com systemd

As unidades de exemplo estão em:

- `deploy/mesh-alerts.service`: serviço contínuo CAP, com envio pelo Meshtastic configurado.
- `deploy/systemd/mesh-alerts-inmet.service`: agendador INMET em modo de simulação.

As unidades pressupõem o projeto em `/opt/mesh-alerts`, um ambiente virtual em `/opt/mesh-alerts/.venv` e uma conta de sistema `mesh-alerts`. Instale o pacote no ambiente virtual, revise `config.yaml` e configure o arquivo de ambiente do serviço CAP a partir de `deploy/mesh-alerts.env.example` em `/etc/mesh-alerts/mesh-alerts.env`.

O systemd cria `/var/lib/mesh-alerts` para o estado persistente. Confirme que a configuração, o código e o ambiente virtual são legíveis pela conta de serviço e que apenas os diretórios de estado precisam de escrita.

Antes de ativar uma unidade, revise seu conteúdo e confirme se o serviço pode transmitir. O serviço INMET permanece em simulação por padrão. A unidade CAP não é um modo de simulação.

## Testes e CI

Execute a suíte local:

```bash
python -m unittest discover -s tests -v
```

Os testes unitários não acessam a rede nem transmitem mensagens. O GitHub Actions verifica Python 3.10 a 3.13, compila os módulos, executa os testes, verifica dependências e constrói e instala o pacote.

O workflow de validação CAP consulta o feed oficial ao vivo e cruza os alertas com a referência GeoJSON versionada. A disponibilidade da API de malhas do IBGE é sondada separadamente e não bloqueia essa validação, porque o endpoint pode expirar no ambiente de CI. Isso não comprova a disponibilidade atual da API de malhas no ambiente de implantação.

## Limitações conhecidas

- A validação de CI usa uma referência GeoJSON versionada; os limites devem ser atualizados periodicamente a partir da fonte oficial.
- A disponibilidade de CAP, IBGE e INMET depende de serviços externos.
- Não há confirmação de teste de transmissão física ou MQTT nesta etapa.
- O envio via Meshtastic depende da conectividade TCP e das configurações do dispositivo.

## Licença

Distribuído sob a licença MIT. Consulte [LICENSE](LICENSE).
