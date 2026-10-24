# Arquitetura

O `mesh-alerts` contém três fluxos operacionais: validação pontual de alertas CAP, processamento contínuo de CAP com saída Meshtastic e geração agendada de previsões INMET.

## Fluxos

### Alertas CAP

1. `region.py` obtém o feed CAP e os limites municipais do IBGE, ou recebe um arquivo GeoJSON local para os limites.
2. `cap.py` interpreta os dados CAP, valida a vigência temporal e determina a interseção geográfica com os municípios configurados.
3. O comando `mesh-alerts-validate` apresenta o resultado sem transmitir mensagens.

No serviço contínuo, `service.py` usa o mesmo processamento regional, registra o ciclo de vida dos alertas e prepara mensagens separadas para os municípios atingidos: identificação municipal (`MESH ALERTS | MUNICÍPIO/UF`), headline CAP sem o rótulo redundante inicial `Defesa Civil:` e classificação CAP (`severity / urgency / certainty`). Uma quarta mensagem de orientação só é acrescentada quando a instrução contém frases acionáveis que não aparecem no headline/description; links e orientações repetidas não são enviados como mensagem extra. Os campos CAP completos continuam preservados no estado persistente para auditoria.

### Saída do serviço CAP

1. `state.py` mantém o estado dos alertas e a fila de saída em SQLite.
2. `service.py` grava mensagens preparadas no diretório de saída configurado e despacha itens pendentes pelo transporte TCP do Meshtastic.
3. Quando o resultado de uma tentativa de envio fica incerto, o item é registrado como incerto e não é reenviado silenciosamente. Isso reduz duplicações, mas não garante entrega exatamente uma vez.

### Previsão INMET

1. `inmet_config.py` carrega e valida a configuração dos municípios e dos horários.
2. `inmet.py` consulta a API de previsão do INMET e formata o boletim para a data local e o período solicitados.
3. `inmet_scheduler.py` controla os horários e o estado persistente dos boletins.
4. `inmet_cli.py` oferece execução pontual ou contínua. A simulação é o padrão; a transmissão exige `--send` e um host TCP.

O projeto usa o INMET para **previsão do tempo**, não para receber alertas meteorológicos.

## Componentes principais

| Módulo | Responsabilidade |
| --- | --- |
| [`config.py`](../src/mesh_alerts/config.py) | Carregar e validar a configuração geral. |
| [`cap.py`](../src/mesh_alerts/cap.py) | Interpretar alertas CAP, tempos, referências e geometrias. |
| [`region.py`](../src/mesh_alerts/region.py) | Obter o feed, carregar limites municipais e validar interseções. |
| [`service.py`](../src/mesh_alerts/service.py) | Orquestrar o serviço CAP e o envio TCP. |
| [`state.py`](../src/mesh_alerts/state.py) | Persistir estado dos alertas e fila de saída. |
| [`inmet_config.py`](../src/mesh_alerts/inmet_config.py) | Validar configuração específica do INMET. |
| [`inmet.py`](../src/mesh_alerts/inmet.py) | Consultar e formatar previsões. |
| [`inmet_scheduler.py`](../src/mesh_alerts/inmet_scheduler.py) | Controlar agenda e estado dos boletins. |
| [`inmet_cli.py`](../src/mesh_alerts/inmet_cli.py) | Expor os comandos de boletins INMET. |

## Configuração e implantação

A configuração de referência está em [`config.yaml`](../config.yaml). Os exemplos de implantação estão em [`deploy/`](../deploy/). O README principal descreve os comandos disponíveis e distingue as operações que podem transmitir mensagens.

Os testes automatizados estão em [`tests/`](../tests/); a forma de executá-los e a cobertura da CI estão em [Desenvolvimento e testes](development.md).
