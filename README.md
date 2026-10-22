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

Os testes unitários cobrem interpretação temporal, polígonos, círculos, referências de atualização/cancelamento e uma amostra CAP real com limites municipais versionados. O workflow `Python unit tests` executa esses testes sem depender da rede. A validação ao vivo fica separada no workflow `Live CAP and IBGE validation`, que pode ser iniciada manualmente e também está agendada para cada seis horas; ela consulta o feed CAP oficial e as malhas atuais do IBGE. Essa etapa depende da disponibilidade das fontes externas, e uma falha de rede não significa, por si só, que a lógica geográfica esteja com defeito.

A interpretação temporal considera `effective` e `expires` para decidir se um bloco está vigente. Um `onset` futuro não desativa o aviso: ele indica quando o evento é esperado para começar. Datas fornecidas, mas malformadas, fazem o bloco ser rejeitado. Códigos geográficos CAP com `valueName=IBGE` também são cruzados diretamente com os seis municípios monitorados, inclusive quando a área não contém polígonos ou círculos. Quando há interseção geométrica, mas os códigos IBGE apontam exclusivamente para outros estados, o script exibe um aviso. Polígonos inválidos são diagnosticados antes do reparo e a geometria reparada continua sendo avaliada.

**Limite de uso:** este estágio monitora e imprime resultados no terminal. Não envia mensagens a rádios, MQTT ou canais Meshtastic, não roda como serviço contínuo e não substitui os canais oficiais da Defesa Civil. Para alertas operacionais, mantenha as fontes oficiais como referência e investigue qualquer falha na consulta ao vivo.

## Próxima etapa

- Avaliar severidade, urgência, certeza e instruções na saída.
- Só depois projetar e testar a integração de saída para Meshtastic.
