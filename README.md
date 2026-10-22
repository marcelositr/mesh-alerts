# mesh-alerts

Serviço leve para validar alertas da Defesa Civil e do INMET e preparar mensagens regionais para redes Meshtastic via LoRa.

## Estado atual

O repositório está na fase de validação de dados. O teste regional do CAP da Defesa Civil cruza áreas de alerta com os limites dos municípios de Aramina, Buritizal, Guará, Igarapava, Ituverava e Miguelópolis.

**Ainda não há publicação para rádios, MQTT ou canais Meshtastic.** A validação geográfica não substitui os avisos oficiais da Defesa Civil.

## Requisitos

- Python 3.10 ou superior
- Dependência Python declarada em `requirements.txt`

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

Os testes cobrem a interpretação temporal do CAP, polígonos, círculos e referências de atualização/cancelamento. A validação territorial ainda depende de consultar as malhas do IBGE. Quando um alerta cruza municípios monitorados, mas seus códigos IBGE apontam exclusivamente para outros estados, o script exibe um aviso de consistência geográfica. O aviso não descarta nem altera o alerta: a geometria CAP continua sendo avaliada.

## Próximas etapas

- Validar os resultados com amostras CAP reais, incluindo atualização e cancelamento.
- Definir a política de apresentação de severidade, urgência, certeza e instruções.
- Só depois projetar e testar a integração de saída para Meshtastic.
