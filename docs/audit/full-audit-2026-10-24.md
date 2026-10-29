# Auditoria técnica: mesh-alerts

- Data: 2026-10-24
- Branch de trabalho: `audit/full-audit-2026-10-24`
- Escopo: revisão estática do código-fonte, testes existentes e checks CI previamente concluídos no commit-base.
- Estado: revisão estática consolidada; testes dinâmicos independentes ainda pendentes.
- Restrição operacional: esta auditoria não altera a branch `main`, não abre pull request e não realiza transmissão via rádio.

## Resumo executivo

A revisão identificou riscos e lacunas de comportamento que merecem testes dirigidos. Nenhum dos cenários abaixo foi reproduzido dinamicamente nesta sessão, portanto as severidades são provisórias e os itens não devem ser interpretados como vulnerabilidades exploradas. Os workflows CI anteriormente consultados passaram no commit-base, mas isso não substitui a reprodução dos cenários descritos.

## Achados priorizados

### A01 — Configuração carregada no momento da importação [CORRIGIDO, CI PASSOU]
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade original:** P2, portabilidade/CLI.
- **Evidência original:** `DEFAULT_CONFIG = load_config()` no escopo do módulo fazia a configuração ser carregada durante a importação.
- **Correção aplicada:** a configuração padrão e os municípios agora são carregados somente quando necessários.
- **Regressão coberta:** `tests/test_region_import.py` importa `mesh_alerts.region` a partir de um diretório temporário.
- **Validação:** workflow de testes passou em Python 3.10, 3.11, 3.12 e 3.13 no commit `a623675`.

### A02 — Falha de transmissão INMET não é recuperada automaticamente
- **Área:** `src/mesh_alerts/inmet_scheduler.py`
- **Prioridade:** P2, operação.
- **Evidência estática:** o registro é reservado antes do I/O externo; uma exceção atualiza o estado para `failed`, e a existência do registro impede nova tentativa automática.
- **Impacto possível:** um boletim pode não ser transmitido após falha, exigindo procedimento operacional manual. A decisão reduz o risco de duplicação quando o resultado da transmissão é incerto.
- **Recomendação:** documentar a reconciliação manual e avaliar um comando explícito de recuperação que exija decisão do operador.
- **Teste recomendado:** simular exceção do transporte, reiniciar o processo e confirmar que não há retransmissão silenciosa.

### A03 — Leitura HTTP e descompressão gzip sem limite explícito de tamanho [MITIGADO, CI PENDENTE]
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade:** P2, resiliência.
- **Evidência original:** leitura HTTP e descompressão gzip sem limites.
- **Correção aplicada:** downloads usados pelo módulo `region.py` são limitados a 16 MiB; saída gzip é limitada a 64 MiB antes do parsing.
- **Regressões adicionadas:** testes para resposta HTTP acima do limite e expansão gzip excessiva.
- **Limitação restante:** feeds e malhas locais são arquivos locais confiáveis e não têm limite de leitura comprimida antes da descompressão; avaliar se o uso operacional exige limites também para arquivos locais.

### A04 — Redirecionamentos HTTP não são revalidados explicitamente [MITIGADO PARCIALMENTE]
- **Área:** `src/mesh_alerts/http_utils.py`
- **Prioridade:** P2, condicionada à origem configurada.
- **Evidência original:** destinos de redirecionamento não eram revalidados explicitamente.
- **Correção aplicada:** cada destino de redirecionamento é validado como HTTP(S), e redirecionamentos de HTTPS para HTTP são recusados.
- **Regressões adicionadas:** testes para esquema não permitido, downgrade HTTPS e redirecionamento HTTPS válido.
- **Limitação restante:** não há lista de hosts permitidos nem bloqueio explícito de destinos privados/loopback; isso continua dependente da confiança na URL configurada e no serviço de origem.

### A05 — Cancelamento CAP altera estado, mas não envia aviso de cancelamento pelo rádio
- **Área:** `src/mesh_alerts/service.py` e testes do serviço.
- **Prioridade:** decisão funcional/operacional P2.
- **Evidência estática:** o serviço documenta que cancelamentos atualizam o ciclo de vida e invalidam mensagens pendentes, sem criar um aviso de cancelamento para transmissão.
- **Impacto possível:** destinatários que já receberam o alerta podem não saber que ele foi cancelado.
- **Recomendação:** confirmar explicitamente o requisito operacional. Se os destinatários precisam receber revogações, definir formato e prioridade para uma mensagem de cancelamento.
- **Observação:** comportamento intencional não é, por si só, bug; precisa de decisão de produto e segurança operacional.

### A06 — Hash de deduplicação inclui valores derivados e transitórios
- **Área:** `src/mesh_alerts/state.py`
- **Prioridade:** P2 provisória, consistência de estado.
- **Evidência estática:** o hash canônico inclui `matches_region` e `is_current`; quando a identidade já existe, um hash diferente retorna `conflict` sem atualizar o registro existente.
- **Impacto possível:** alterações de validade temporal ou de configuração geográfica podem ser tratadas como conflito de conteúdo e deixar o estado persistido desatualizado.
- **Mitigação observada:** a rotina de invalidação verifica também `expires_at`, não apenas a flag `active`, antes de despachar mensagens pendentes.
- **Teste recomendado:** processar o mesmo alerta antes e depois da expiração e com uma mudança de configuração geográfica; inspecionar `cap_alerts` e `outbox`.

### A07 — Arquivos TXT podem ser gravados antes da aceitação persistente [CORRIGIDO, CI PASSOU]
- **Área:** `src/mesh_alerts/service.py`
- **Prioridade:** P2, consistência entre artefatos.
- **Evidência original:** TXT era escrito antes da decisão persistente, permitindo divergência em caso de `conflict`.
- **Correção aplicada:** TXT só é escrito depois de `process_cap_alert()` retornar `queued`; duplicatas e conflitos não sobrescrevem arquivos.
- **Regressão coberta:** `test_conflicting_alert_does_not_overwrite_existing_text_files` compara os arquivos antes e depois do conflito.
- **Validação:** workflow de testes passou em Python 3.10, 3.11, 3.12 e 3.13 no commit `a623675`.

### A08 — Referências CAP descartavam o timestamp [CORRIGIDO, CI PENDENTE]
- **Área:** `src/mesh_alerts/cap.py`, `src/mesh_alerts/service.py` e `src/mesh_alerts/state.py`.
- **Prioridade original:** P2 provisória, integridade de protocolo.
- **Evidência original:** `parse_reference_keys()` validava o terceiro componente, mas retornava apenas remetente/identificador; tanto a seleção do feed quanto a persistência perdiam o timestamp.
- **Correção aplicada:** criado `parse_reference_records()`, que preserva o timestamp normalizado em UTC. A seleção de alertas só suprime uma mensagem quando remetente, identificador e timestamp enviado correspondem. O serviço passa as referências com timestamp ao estado persistente, que compara o horário com o registro local antes de invalidar alertas e mensagens pendentes. A API antiga que retorna pares foi mantida para compatibilidade.
- **Regressões adicionadas:** referência no feed com timestamp divergente não suprime o alerta original; referência divergente no estado SQLite não desativa o alerta nem remove sua mensagem pendente.
- **Validação:** aguardando workflows CI do commit que contém a correção.

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
- A03: limites de leitura HTTP e descompressão gzip implementados; testes de regressão aprovados na CI.
- A04: destinos de redirecionamento revalidados e downgrade HTTPS bloqueado; testes de regressão aprovados na CI. Restrição por host/endereço ainda não implementada.
- A07: gravação dos TXT movida para depois da aceitação persistente; teste de conflito aprovado na CI.
- A02, A05 e A06 continuam pendentes de análise/correção. A08 foi corrigido e tem regressões adicionadas; a validação CI está pendente.

## Próximos passos

1. Validar via CI os testes de A08 para supressão no feed e estado SQLite.
2. Reproduzir A06 com SQLite e validar identidade, expiração, conteúdo canônico e outbox antes de alterar a deduplicação.
3. Exercitar falha de transporte INMET e documentar recuperação manual, sem retransmissão silenciosa.
4. Confirmar se cancelamentos CAP precisam ser transmitidos aos nós Meshtastic.
5. Avaliar a necessidade operacional de bloquear redirecionamentos para hosts privados/loopback e limitar também entradas de arquivos locais.

## Limitações

- Não foi possível clonar o repositório nesta sessão por falha de resolução de `github.com`; os arquivos foram inspecionados via integração GitHub.
- Os testes automatizados foram executados via GitHub Actions; não foi possível executar uma cópia local independente nesta sessão por falha de resolução de `github.com`.
- Nenhuma transmissão de rádio foi executada.
- As severidades originais são triagem estática, não avaliação de exploração confirmada.
- Nenhum resultado de teste hipotético deve ser reportado como execução real.

## Estado da branch

A branch de auditoria é separada de `main`. Este relatório não autoriza merge, criação de pull request ou alteração da branch principal.
