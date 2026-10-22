# mesh-alerts

Serviço leve para cruzar alertas oficiais da Defesa Civil (CAP) com municípios configurados e preparar previsões meteorológicas do INMET para distribuição futura via Meshtastic.

## Estado atual

A validação regional CAP consulta o feed oficial da Defesa Civil, interpreta validade, atualizações e cancelamentos e cruza geometrias com os municípios monitorados. A primeira etapa da integração de previsão implementa a consulta e a formatação compacta da API do INMET.

**O agendador INMET está implementado, mas não é iniciado automaticamente pelo sistema operacional.** A execução padrão simula as mensagens e não transmite. O envio real exige a opção explícita `--send` e um destino TCP informado. Os testes automatizados usam transporte simulado e não transmitem mensagens. A previsão do INMET permanece separada dos alertas CAP.

**Escopo meteorológico:** usar somente a previsão do INMET. Os alertas meteorológicos do próprio INMET não fazem parte deste projeto.

## Configuração

Os municípios, horários, fuso horário e intervalos ficam em [config.yaml](config.yaml). A configuração inicial inclui Aramina, Buritizal, Guará, Igarapava, Ituverava e Miguelópolis.

Instale as dependências:

```bash
python3 -m pip install -r requirements.txt
```

Os horários configurados são 06:00 (manhã), 12:00 (tarde) e 18:00 (noite), no fuso `America/Sao_Paulo`. Para cada consulta, o módulo seleciona exclusivamente a data local atual e o período correspondente. Se a API não fornecer o período de hoje, a previsão é considerada indisponível; não há fallback para dias futuros.

O formato compacto da previsão tem duas linhas, por exemplo:

```text
ITUVERAVA/SP • MANHÃ
Muitas nuvens com possibilidade de chuva isolada | 32/20°C | U85/50% | Vento fraco
```

O texto meteorológico é preservado conforme o campo `resumo` retornado pelo INMET. Temperaturas e umidade aparecem na ordem máxima/mínima. Campos de ícones e tendências são ignorados.

Os intervalos configurados são de 3 segundos entre mensagens de um mesmo boletim e 30 segundos entre municípios. Como cada previsão compacta é uma única mensagem, os 3 segundos não se aplicam ao boletim INMET. No envio real, o agendador aguarda 30 segundos antes de processar o próximo município.

## Validar a previsão localmente

O módulo fornece funções para consultar e formatar previsões, mas não envia mensagens:

```bash
python3 - <<'PY'
from mesh_alerts_config import carregar_configuracao
from mesh_alerts_inmet import gerar_mensagem_municipal

config = carregar_configuracao()
for municipio in config["municipios"]:
    try:
        print(gerar_mensagem_municipal(municipio, "manha", config=config))
    except Exception as erro:
        print(f'{municipio["nome"]}: previsão indisponível: {erro}')
    print()
PY
```

O comando consulta a previsão da manhã da data local atual para cada município. Se o INMET não fornecer esse período para hoje, imprime o motivo e não substitui a previsão por dados futuros. O comando não transmite nada pelo rádio ou MQTT.


## Agendador INMET

Faça uma simulação pontual do período da manhã, sem transmitir:

```bash
python3 executar_boletins_inmet.py --period manha
```

Sem `--period`, o comando verifica se o horário atual corresponde a um horário agendado e encerra.

Para deixar o processo verificando os horários configurados, ainda em simulação:

```bash
python3 executar_boletins_inmet.py --loop
```

O agendador consulta os horários locais 06:00, 12:00 e 18:00 e seleciona, respectivamente, manhã, tarde e noite da data atual. Se o período de hoje não estiver disponível, não substitui por uma previsão futura. O estado de envios reais fica em SQLite, por data, período e município.

**Somente para habilitar transmissão real via TCP**, informe o host do dispositivo Meshtastic e a opção explícita `--send`:

```bash
python3 executar_boletins_inmet.py --loop --send --tcp-host 192.168.0.2
```

Ajuste host, porta, canal e destino conforme a sua instalação. A reserva persistente é gravada antes da tentativa de envio para evitar retransmissão automática após uma interrupção. Se o transporte falhar ou o resultado ficar incerto, o boletim daquele município não é reenviado automaticamente; confira o estado e decida manualmente. Isso privilegia evitar duplicatas, mas não garante entrega exatamente uma vez.

Não foi feito teste de transmissão física ou MQTT nesta etapa. O agendador não altera a lógica dos alertas CAP nem consulta alertas meteorológicos do INMET.

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

Os códigos IBGE configurados são:

| Município | Código |
|---|---:|
| Aramina | 3503000 |
| Buritizal | 3508207 |
| Guará | 3517703 |
| Igarapava | 3520103 |
| Ituverava | 3524105 |
| Miguelópolis | 3529708 |

A interpretação temporal CAP considera `effective` e `expires`; um `onset` futuro não desativa o aviso. Atualizações e cancelamentos referenciados são tratados. Os cruzamentos consideram códigos IBGE, polígonos e círculos, com diagnósticos para geometrias problemáticas e metadados administrativos incompatíveis.

## Testes

Execute os testes unitários sem acessar a rede:

```bash
python3 -m unittest discover -s tests -v
```

Os testes cobrem interpretação CAP, validade temporal, geometria, atualizações/cancelamentos, seleção estrita da data e período INMET, formatação compacta e configuração. O workflow `Python unit tests` executa os testes unitários sem depender das APIs externas.

A validação ao vivo CAP/IBGE fica separada no workflow `Live CAP and IBGE validation`, que pode ser iniciada manualmente e também está agendada a cada seis horas. Uma falha de rede nessa etapa não significa, por si só, que a lógica geográfica esteja com defeito.

## Próximas etapas

- Integrar futuramente a saída CAP com transporte, sem alterar as regras já testadas.
- Validar a execução no ambiente local e confirmar o CI antes de qualquer teste físico. Nenhuma transmissão física foi testada nesta etapa.

## Licença

Consulte [LICENSE](LICENSE).
