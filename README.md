# mesh-alerts

Serviço leve para identificar alertas CAP da Defesa Civil que cruzam municípios configurados e preparar mensagens para redes Meshtastic via TCP.

## Configuração para quem vai instalar

A configuração principal fica em **`config.yaml`**. Edite esse arquivo antes de executar o serviço. Não é necessário editar o código Python para trocar de região ou ajustar o nó Meshtastic.

As seções principais são:

- `regiao`: nome da região, estado e UF.
- `municipios`: lista de municípios, cada um com nome e código IBGE de sete dígitos.
- `meshtastic`: host, porta TCP, índice do canal e destino da mensagem.
- `servico`: intervalo de consulta, banco SQLite, diretório de TXT, nível de log, timeout/repetições HTTP e limite da mensagem de rádio.
- `fontes`: URL do feed CAP e URL-base da malha municipal do IBGE.

O arquivo incluído contém a configuração regional existente para Aramina, Buritizal, Guará, Igarapava, Ituverava e Miguelópolis. Use códigos IBGE como texto, entre aspas. Não inclua segredos ou credenciais nesse arquivo.

O programa valida o arquivo na inicialização e apresenta uma mensagem de erro para campos ausentes ou inválidos. Se precisar de outra configuração, crie uma cópia e informe-a com `--config /caminho/para/config.yaml`.

## Requisitos e instalação

- Python 3.10 ou superior.
- Dependências declaradas em `pyproject.toml` (Shapely, PyYAML e o cliente Meshtastic). O `requirements.txt` permanece disponível para instalações que prefiram instalar apenas as dependências.

```bash
python3 -m pip install -e .
```

## Validar alertas atuais

```bash
python3 -m mesh_alerts.region
```

O script lê `config.yaml`, consulta o feed CAP configurado e carrega as malhas dos municípios selecionados no IBGE. Para analisar um XML CAP local:

```bash
python3 -m mesh_alerts.region --feed /caminho/para/defesa-civil-cap.xml
```

O feed pode falhar por indisponibilidade externa ou de rede. O programa informa o erro e não substitui dados atuais por amostras fictícias. Os testes unitários usam amostras versionadas e não dependem da internet.

## Serviço contínuo e transmissão TCP

Configure `meshtastic.host` com o endereço do nó Meshtastic que tenha a interface TCP habilitada. Confira também porta, canal e destino. Inicie um único ciclo de teste com:

```bash
python3 -m mesh_alerts.service --once
```

Para executar continuamente:

```bash
python3 -m mesh_alerts.service
```

Argumentos como `--tcp-host`, `--tcp-port`, `--channel-index`, `--destination`, `--database`, `--output-dir` e `--interval` podem sobrescrever a configuração para uma execução pontual. As variáveis de ambiente `MESH_ALERTS_*` também continuam disponíveis para instalações existentes e para systemd; veja `deploy/mesh-alerts.env.example`.

O banco SQLite deve ficar em armazenamento local persistente. Não o coloque em `/tmp`, não o apague entre reinicializações e proteja o banco e o diretório de saída de acordo com a política local de retenção.

## Execução contínua com systemd (Linux)

A unidade está em `deploy/mesh-alerts.service`. Instale o projeto no ambiente virtual com `/opt/mesh-alerts/.venv/bin/pip install -e /opt/mesh-alerts`. O modelo pressupõe o código em `/opt/mesh-alerts`, ambiente virtual em `/opt/mesh-alerts/.venv` e usuário de serviço `mesh-alerts`. Ajuste os caminhos e permissões antes de instalar. O arquivo `config.yaml` deve estar presente no diretório de trabalho e precisa permitir que o usuário do serviço o leia.

O systemd pode reiniciar o processo após falhas, mas não recupera internet, energia ou o nó de rádio por conta própria.

## Persistência e recuperação

- SQLite usa transações, WAL e sincronização `FULL`; alertas e mensagens enfileiradas sobrevivem à reinicialização.
- A identidade do alerta usa o par CAP `sender` + `identifier`; o mesmo alerta não cria uma segunda mensagem na fila.
- Atualizações e cancelamentos desativam os alertas referenciados, mesmo quando a nova área não cruza os municípios monitorados.
- Falhas de transporte identificadas podem retornar à fila para nova tentativa.
- Se o processo cair enquanto um envio está em andamento, a mensagem vira `uncertain` e não é reenviada automaticamente. Isso evita duplicações silenciosas, mas pode deixar uma mensagem sem entrega.
- Para resolver um envio incerto, use o banco correto e o ID registrado: `python3 -m mesh_alerts.service --resolve-uncertain ID --resolution sent` ou `--resolution retry`. A opção `retry` pode duplicar uma mensagem que já chegou à rede.
- O status `sent` significa que a chamada foi aceita pelo cliente Meshtastic TCP. Não comprova recepção por outro rádio.

## Testes

Execute os testes unitários sem consultar a rede:

```bash
python3 -m unittest discover -s tests -v
```

O workflow `Python unit tests` executa os testes em Python 3.12. A validação ao vivo CAP/IBGE fica separada no workflow `Live CAP and IBGE validation`, que pode ser iniciada manualmente e também roda a cada seis horas. Essa etapa depende das fontes externas.

## Limites da implementação

O serviço contínuo implementado atualmente consome CAP da Defesa Civil. A menção a INMET e outras fontes descreve uma possível evolução, não uma integração já disponível. A validação geográfica não substitui os avisos oficiais da Defesa Civil. Faça testes controlados com um nó Meshtastic antes de considerar o serviço pronto para uso operacional.
