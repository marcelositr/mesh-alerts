# mesh-alerts

Serviço leve para validar alertas da Defesa Civil e do INMET e preparar mensagens regionais para redes Meshtastic via LoRa.

## Estado atual

O repositório está na fase de validação de dados. O teste regional do CAP da Defesa Civil cruza áreas de alerta com os limites dos municípios de Aramina, Buritizal, Guará, Igarapava, Ituverava e Miguelópolis.

Há um serviço contínuo experimental para preparar mensagens regionais e enviá-las a um nó Meshtastic por TCP. Ele não usa MQTT. A validação geográfica não substitui os avisos oficiais da Defesa Civil; teste em ambiente controlado antes de uso operacional.

## Requisitos

- Python 3.10 ou superior
- Dependências Python declaradas em `requirements.txt` (`Shapely` e cliente `meshtastic`)

Instalação:

```bash
python3 -m pip install -r requirements.txt
```

## Teste regional CAP

O script `testar_alertas_regiao_ituverava.py` consulta o feed CAP oficial da Defesa Civil, carrega as malhas municipais do IBGE e lista alertas com interseção territorial.

Fonte CAP:

`https://idapfile.mdr.gov.br/idap/api/rss/cap`

Execute usando dados atuais:

```bash
python3 testar_alertas_regiao_ituverava.py
```

Para analisar uma amostra XML já salva, sem buscar o feed CAP:

```bash
python3 testar_alertas_regiao_ituverava.py --feed /caminho/para/defesa-civil-cap.xml
```

A consulta à malha do IBGE continua sendo feita pela rede em ambos os modos. O acesso ao feed pode falhar por indisponibilidade da fonte ou da conexão; o script informa o erro e não substitui os dados por uma amostra fictícia.

Os códigos IBGE utilizados são:

| Município | Código |
|---|---:|
| Aramina | 3503000 |
| Buritizal | 3508207 |
| Guará | 3517703 |
| Igarapava | 3520103 |
| Ituverava | 3524105 |
| Miguelópolis | 3529708 |

## Testes

Execute os testes unitários sem acessar a rede:

```bash
python3 -m unittest discover -s tests -v
```

Os testes unitários cobrem interpretação temporal, polígonos, círculos, referências de atualização/cancelamento e uma amostra CAP real com limites municipais versionados. O workflow `Python unit tests` executa esses testes sem depender da rede. A validação ao vivo fica separada no workflow `Live CAP and IBGE validation`, que pode ser iniciada manualmente e também está agendada para cada seis horas; ela consulta o feed CAP oficial e as malhas atuais do IBGE. Essa etapa depende da disponibilidade das fontes externas, e uma falha de rede não significa, por si só, que a lógica geográfica esteja com defeito.

A interpretação temporal considera `effective` e `expires` para decidir se um bloco está vigente. Um `onset` futuro não desativa o aviso: ele indica quando o evento é esperado para começar. Datas fornecidas, mas malformadas, fazem o bloco ser rejeitado. Códigos geográficos CAP com `valueName=IBGE` também são cruzados diretamente com os seis municípios monitorados, inclusive quando a área não contém polígonos ou círculos. Quando há interseção geométrica, mas os códigos IBGE apontam exclusivamente para outros estados, o script exibe um aviso. Polígonos inválidos são diagnosticados antes do reparo e a geometria reparada continua sendo avaliada.

## Serviço contínuo e transmissão TCP

O serviço consulta o feed CAP, cruza alertas com os seis municípios monitorados, grava mensagens TXT e mantém uma fila de saída persistente em SQLite. Quando há mensagens pendentes, conecta-se ao nó Meshtastic com TCP habilitado.

Exemplo de execução:

```bash
python3 mesh_alerts_service.py \
  --tcp-host meshtastic-node.local \
  --tcp-port 4403 \
  --channel-index 0 \
  --database data/mesh-alerts.sqlite3 \
  --output-dir output \
  --interval 60
```

Use o endereço e a porta do seu próprio nó. O nó precisa estar acessível pela rede e com a interface TCP habilitada. Para executar um único ciclo, acrescente `--once`. O diretório do banco deve ficar em armazenamento local persistente; não coloque o banco em `/tmp`, nem apague ou substitua o arquivo entre reinicializações.

### Execução contínua com systemd (Linux)

O repositório inclui um modelo de unidade em `deploy/mesh-alerts.service` e um exemplo de configuração em `deploy/mesh-alerts.env.example`. Revise os caminhos antes de instalar: o modelo pressupõe o código em `/opt/mesh-alerts`, ambiente virtual em `/opt/mesh-alerts/.venv` e usuário de serviço `mesh-alerts`.

Após criar o usuário, instalar as dependências no ambiente virtual e copiar o arquivo de ambiente para `/etc/mesh-alerts/mesh-alerts.env`, instale a unidade em `/etc/systemd/system/mesh-alerts.service`, depois execute:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mesh-alerts.service
sudo systemctl status mesh-alerts.service
journalctl -u mesh-alerts.service -f
```

O systemd reinicia o processo após falhas. Ele não consegue recuperar a internet, energia ou o nó de rádio por conta própria; o serviço preserva a fila e tenta novamente em ciclos posteriores.

### Persistência e recuperação

- O SQLite usa transações, WAL e sincronização `FULL`; alertas e mensagens enfileiradas sobrevivem à reinicialização.
- A identidade do alerta usa o par CAP `sender` + `identifier`. O mesmo alerta não cria uma segunda mensagem na fila.
- Atualizações e cancelamentos desativam os alertas referenciados, mesmo quando a nova área já não cruza os municípios monitorados.
- Falhas de transporte identificadas voltam à fila para nova tentativa.
- Se o processo cair enquanto um envio estiver em andamento, a mensagem vira `uncertain` e **não é reenviada automaticamente**, para evitar duplicações silenciosas. Essa decisão pode deixar uma mensagem sem entrega caso a queda tenha ocorrido antes do envio efetivo.
- Consulte os IDs de envios incertos no banco e resolva cada caso explicitamente. Para marcar como enviado: `python3 mesh_alerts_service.py --database data/mesh-alerts.sqlite3 --resolve-uncertain ID --resolution sent`. Para autorizar nova tentativa: substitua `sent` por `retry`. A opção `retry` pode produzir duplicação se a mensagem já tiver chegado à rede.
- O status `sent` significa que a chamada de envio foi aceita pelo cliente Meshtastic TCP. Não é confirmação de recepção pelo rádio ou pelo destinatário.

O TXT é uma cópia legível da mensagem preparada para transmissão. Proteja o banco e o diretório de saída de acordo com a política local de retenção. Mantenha a máquina e o nó Meshtastic ligados, e use um supervisor de processo do sistema operacional para reiniciar o serviço depois de falhas.

**Estado de maturidade:** esta implementação precisa passar pela CI e por testes controlados com um nó Meshtastic antes de ser considerada pronta para uso operacional. Ela não substitui os canais oficiais da Defesa Civil.
