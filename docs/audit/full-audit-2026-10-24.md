# Auditoria técnica: mesh-alerts

- Data: 2026-10-24
- Branch de trabalho: `audit/full-audit-2026-10-24`
- Escopo: revisão estática do código-fonte, testes existentes e regressões validadas pelos workflows do GitHub Actions.
- Estado: achados priorizados e regressões principais verificadas na CI; A05 requer decisão operacional explícita antes de qualquer alteração de comportamento. Esta revisão não substitui uma avaliação independente de implantação e rede.
- Restrição operacional: esta auditoria não altera a branch `main`, não abre pull request e não realiza transmissão via rádio.

## Resumo executivo

A revisão identificou riscos e lacunas que mereciam testes dirigidos. Foram adicionadas regressões para os principais cenários. No commit `66f3a87`, os workflows de testes (Python 3.10–3.13 e build), segurança/qualidade e validação live CAP concluíram com sucesso. Isso confirma os checks configurados, não a ausência de defeitos nem a segurança de toda a infraestrutura. O job `ibge-live-probe` foi ignorado conforme a configuração do workflow.

## Achados priorizados

### A01 — Configuração carregada no momento da importação [CORRIGIDO, CI PASSOU]
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade original:** P2, portabilidade/CLI.
- **Evidência original:** `DEFAULT_CONFIG = load_config()` no escopo do módulo fazia a configuração ser carregada durante a importação.
- **Correção aplicada:** a configuração padrão e os municípios agora são carregados somente quando necessários.
- **Regressão coberta:** `tests/test_region_import.py` importa `mesh_alerts.region` a partir de um diretório temporário.
- **Validação:** workflow de testes passou em Python 3.10, 3.11, 3.12 e 3.13 no commit `a623675`.

### A02 — Falha de transmissão INMET exige reconciliação manual [MITIGADO]
- **Área:** `src/mesh_alerts/inmet_scheduler.py` e `src/mesh_alerts/inmet_cli.py`.
- **Prioridade:** P2, operação.
- **Risco original:** uma exceção do transporte deixa registro `failed` e impede retry automático. Isso evita duplicação quando o rádio pode ter transmitido apesar de a chamada ter falhado, mas o operador precisava inspecionar SQLite manualmente.
- **Mitigação aplicada:** novo comando somente leitura `mesh-alerts-inmet --status --database <caminho>` lista data, período, código municipal, estado, horário e detalhe. Não abre interface de rádio nem transmite. O caminho de envio automático continua sem retry de registros existentes.
- **Procedimento operacional:** consultar o status; verificar logs do dispositivo/rádio e confirmar com operadores receptores se o boletim chegou; se a entrega continuar incerta, não retransmitir automaticamente. Qualquer reenvio deve ser uma decisão operacional explícita e documentada.
- **Regressão adicionada:** força falha do transporte e verifica que os registros `failed` e seus detalhes são exibidos pela função de consulta.
- **Validação:** workflows Python unit tests, security/quality e live CAP passaram no commit `4e90017` para as alterações anteriores de A02/A04. Para A03, os testes e checks posteriores passaram no commit `8571233`; o job `ibge-live-probe` é separado e foi ignorado conforme a configuração do workflow.

### A03 — Leitura HTTP e descompressão gzip sem limite explícito de tamanho [MITIGADO, CI PASSOU]
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade:** P2, resiliência.
- **Evidência original:** leitura HTTP e descompressão gzip sem limites.
- **Correção aplicada:** downloads usados pelo módulo `region.py` são limitados a 16 MiB; saída gzip é limitada a 64 MiB antes do parsing.
- **Regressões adicionadas:** testes para resposta HTTP acima do limite e expansão gzip excessiva.
- **Correção adicional:** feeds CAP locais e GeoJSON de malhas locais agora têm limite de leitura de 16 MiB. A descompressão gzip usada nas respostas do IBGE também usa o limite de saída de 64 MiB, em vez de `gzip.decompress()` sem limite.
- **Regressões adicionadas:** arquivos locais de feed CAP e malha GeoJSON acima do limite são rejeitados; resposta gzip do caminho alternativo de malha municipal do IBGE também é testada contra expansão excessiva.
- **Validação:** workflows Python unit tests (Python 3.10, 3.11, 3.12 e 3.13, incluindo build e testes do wheel), security/quality e live CAP passaram no commit `8571233`. O job `ibge-live-probe` foi ignorado conforme a configuração do workflow; não é uma validação ao vivo do IBGE.

### A04 — Redirecionamentos HTTP e destinos de rede [MITIGADO, CI PASSOU]
- **Área:** `src/mesh_alerts/http_utils.py`.
- **Prioridade:** P2, condicionada à origem configurada.
- **Correções aplicadas:** cada destino de redirecionamento é validado como HTTP(S), redirecionamentos HTTPS→HTTP são recusados, e URLs com credenciais embutidas, portas inválidas ou IPs literais não globais são bloqueadas.
- **Mitigação SSRF adicional:** a resolução DNS ocorre no caminho de conexão; todas as respostas precisam ser IPs globais, e o socket conecta ao endereço exato validado, sem uma segunda resolução do hostname. Isso reduz o risco de DNS rebinding entre a validação e a conexão. Respostas DNS mistas, com endereços públicos e não públicos, são rejeitadas por inteiro.
- **Comportamento de rede:** o opener não usa proxies configurados por variáveis de ambiente, pois um proxy faria a conexão real contornar a validação do destino. Os pedidos HTTP(S) deste módulo conectam diretamente ao endereço validado.
- **Regressões adicionadas:** esquemas inválidos, downgrade HTTPS, redirect HTTPS válido, IPs IPv4/IPv6 não públicos, credenciais, porta inválida, redirect para loopback, hostname resolvendo para IP privado, resposta DNS mista e conexão ao sockaddr exato validado.
- **Limitações restantes:** esta proteção não substitui controles de egress da infraestrutura, nem uma allowlist quando a aplicação deve consultar apenas serviços conhecidos. Endereços globalmente roteáveis também podem apontar para serviços sensíveis em redes com roteamento incomum; a política de rede deve continuar restrita ao necessário.
- **Validação:** testes Python 3.10-3.13, build/teste do wheel, análise de segurança/qualidade e validação live CAP passaram no commit `6e54da2`. O job `ibge-live-probe` foi ignorado conforme a configuração do workflow; isso não equivale a uma validação ao vivo do IBGE.

### A05 — Cancelamento CAP altera estado, mas não envia aviso de cancelamento pelo rádio [DECISÃO OPERACIONAL PENDENTE]
- **Área:** `src/mesh_alerts/service.py` e testes do serviço.
- **Prioridade:** decisão funcional/operacional P2.
- **Evidência estática e regressão:** `process_feed()` não cria mensagem para `msgType=Cancel`; `tests/test_service.py::test_cancel_updates_state_without_broadcasting_cancel_message` confirma que o alerta original passa a `cancelled`, as mensagens pendentes são invalidadas e nenhum texto `CANCELADO` é produzido. Este teste fixa o comportamento atual, não decide se o produto deve transmitir um cancelamento.
- **Impacto possível:** destinatários que já receberam o alerta podem não saber que ele foi cancelado.
- **Recomendação:** manter o comportamento atual até que o responsável operacional confirme o requisito. Se os destinatários precisam receber revogações, especificar antes da implementação: texto inequívoco de cancelamento, identificação do alerta original, prioridade, tratamento de cancelamento recebido após envio do alerta, deduplicação e política para destinos/canais. Um aviso de cancelamento não deve ser tratado como um alerta comum nem retransmitido silenciosamente.
- **Decisão pendente:** requisito de produto/segurança operacional. A auditoria não implementa transmissão de cancelamento sem essa definição, porque uma mudança incorreta pode confundir operadores e destinatários.

### A06 — Hash de deduplicação misturava conteúdo CAP com estado derivado [CORRIGIDO, CI PASSOU]
- **Área:** `src/mesh_alerts/state.py`
- **Prioridade original:** P2 provisória, consistência de estado.
- **Evidência original:** o hash canônico incluía `matches_region` e `is_current`; para uma identidade já persistida, uma mudança nesses valores podia retornar `conflict` sem atualizar o estado.
- **Correção aplicada:** para um alerta `Alert` já conhecido, quando remetente, identificador, envio, expiração e conteúdo de origem permanecem iguais, a persistência atualiza os campos derivados e o hash em vez de tratar a mudança de estado temporal como conflito. A comparação ignora a lista derivada de cidades. Quando o alerta deixa de estar vigente, mensagens pendentes são invalidadas.
- **Limite da correção:** a atualização automática foi deliberadamente restrita a mensagens `Alert` sem referências, para não confundir mudanças legítimas de conteúdo em atualizações/cancelamentos com mudanças de estado derivado.
- **Regressão adicionada:** processar um alerta, reprocessá-lo após a expiração e confirmar estado `expired`, `active=0` e ausência de mensagens pendentes.
- **Validação:** teste de regressão passou nos workflows Python unit tests (Python 3.10, 3.11, 3.12 e 3.13, incluindo build do pacote) e security/quality no commit `3041ff6`.

### A07 — Arquivos TXT podem ser gravados antes da aceitação persistente [CORRIGIDO, CI PASSOU]
- **Área:** `src/mesh_alerts/service.py`
- **Prioridade:** P2, consistência entre artefatos.
- **Evidência original:** TXT era escrito antes da decisão persistente, permitindo divergência em caso de `conflict`.
- **Correção aplicada:** TXT só é escrito depois de `process_cap_alert()` retornar `queued`; duplicatas e conflitos não sobrescrevem arquivos.
- **Regressão coberta:** `test_conflicting_alert_does_not_overwrite_existing_text_files` compara os arquivos antes e depois do conflito.
- **Validação:** workflow de testes passou em Python 3.10, 3.11, 3.12 e 3.13 no commit `a623675`.

### A08 — Referências CAP descartavam o timestamp [CORRIGIDO, CI PASSOU]
- **Área:** `src/mesh_alerts/cap.py`, `src/mesh_alerts/service.py` e `src/mesh_alerts/state.py`.
- **Prioridade original:** P2 provisória, integridade de protocolo.
- **Evidência original:** `parse_reference_keys()` validava o terceiro componente, mas retornava apenas remetente/identificador; tanto a seleção do feed quanto a persistência perdiam o timestamp.
- **Correção aplicada:** criado `parse_reference_records()`, que preserva o timestamp normalizado em UTC. A seleção de alertas só suprime uma mensagem quando remetente, identificador e timestamp enviado correspondem. O serviço passa as referências com timestamp ao estado persistente, que compara o horário com o registro local antes de invalidar alertas e mensagens pendentes. A API antiga que retorna pares foi mantida para compatibilidade.
- **Regressões adicionadas:** referência no feed com timestamp divergente não suprime o alerta original; referência divergente no estado SQLite não desativa o alerta nem remove sua mensagem pendente.
- **Validação:** testes de unidade, análise de segurança/qualidade e validação live CAP passaram no commit `3041ff6`. O job `ibge-live-probe` foi ignorado conforme a configuração do workflow.

## Controles positivos observados

- YAML é carregado com `yaml.safe_load()`.
- O parser do feed usa `defusedxml`.
- Consultas SQLite são parametrizadas e o estado usa transações.
- Nomes de arquivos de saída são derivados de hashes, não diretamente de identificadores CAP não confiáveis.
- O envio INMET exige modo real explicitamente, e o modo de simulação não reserva entregas.
- A outbox distingue `pending`, `sending`, `sent`, `uncertain` e `failed`.
- Resultados incertos não são repetidos silenciosamente; há resolução explícita.
- Mensagens pendentes expiradas ou invalidadas são marcadas antes do despacho.
- A configuração padrão exige HTTPS para os endpoints CAP, IBGE e INMET.
- Os workflows CI consultados anteriormente passaram no commit-base. Isso confirma os checks configurados, não a ausência de defeitos.

## Progresso de implementação e validação

- A01: corrigido; teste de importação fora da raiz do projeto aprovado na CI.
- A03: limites de leitura HTTP, arquivo local e descompressão gzip implementados; regressões HTTP/gzip e arquivos locais adicionadas; CI passou no commit `8571233` em Python 3.10-3.13, build do pacote, segurança/qualidade e validação live CAP.
- A04: resolução DNS validada no caminho de conexão, endereços não públicos e respostas DNS mistas rejeitados, e socket conectado ao endereço exato validado; regressões adicionadas para hostname privado, DNS misto e pinning. CI passou no commit `6e54da2`; controles de egress continuam recomendados.
- A07: gravação dos TXT movida para depois da aceitação persistente; teste de conflito aprovado na CI.
- A02 está mitigado com consulta de status somente leitura e procedimento manual para reconciliação, sem retry automático. A05 continua aguardando decisão funcional. A06 e A08 foram corrigidos com regressões adicionadas; os workflows de testes, segurança/qualidade e validação live CAP passaram no commit `3041ff6`. No commit `66f3a87`, os workflows configurados de testes, segurança/qualidade e live CAP passaram. O job `ibge-live-probe` foi ignorado conforme a configuração do workflow.

## Próximos passos

1. No commit `66f3a87`, a CI está verde para os workflows configurados: testes Python 3.10–3.13/build, segurança/qualidade e live CAP. `ibge-live-probe` foi ignorado, portanto não declarar validação ao vivo do IBGE.
2. A02: manter reconciliação manual de falhas/incertezas INMET; não adicionar retry automático sem idempotência ou confirmação de entrega.
3. A05: a lógica e a regressão existentes confirmam que cancelamentos CAP atualizam o estado e invalidam pendências sem gerar transmissão. A decisão sobre adicionar um aviso de cancelamento continua pendente do responsável operacional; não mudar o protocolo de rádio sem requisito aprovado.
4. Para implantação, avaliar controles de egress/allowlist e testar a política de rede no ambiente real. Não considerar validação em nível de aplicação substituta de defesa de rede.

## Limitações

- Não foi possível clonar o repositório nesta sessão por falha de resolução de `github.com`; os arquivos foram inspecionados via integração GitHub.
- Os testes automatizados foram executados via GitHub Actions; não foi possível executar uma cópia local independente nesta sessão por falha de resolução de `github.com`.
- Nenhuma transmissão de rádio foi executada.
- As severidades originais são triagem estática, não avaliação de exploração confirmada.
- Nenhum resultado de teste hipotético deve ser reportado como execução real.

## Estado da branch

A branch de auditoria é separada de `main`. Este relatório não autoriza merge, criação de pull request ou alteração da branch principal.
