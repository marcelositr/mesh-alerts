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

### A02 — Falha de transmissão INMET exige reconciliação manual [MITIGADO]
- **Área:** `src/mesh_alerts/inmet_scheduler.py` e `src/mesh_alerts/inmet_cli.py`.
- **Prioridade:** P2, operação.
- **Risco original:** uma exceção do transporte deixa registro `failed` e impede retry automático. Isso evita duplicação quando o rádio pode ter transmitido apesar de a chamada ter falhado, mas o operador precisava inspecionar SQLite manualmente.
- **Mitigação aplicada:** novo comando somente leitura `mesh-alerts-inmet --status --database <caminho>` lista data, período, código municipal, estado, horário e detalhe. Não abre interface de rádio nem transmite. O caminho de envio automático continua sem retry de registros existentes.
- **Procedimento operacional:** consultar o status; verificar logs do dispositivo/rádio e confirmar com operadores receptores se o boletim chegou; se a entrega continuar incerta, não retransmitir automaticamente. Qualquer reenvio deve ser uma decisão operacional explícita e documentada.
- **Regressão adicionada:** força falha do transporte e verifica que os registros `failed` e seus detalhes são exibidos pela função de consulta.
- **Validação:** workflows CI da alteração pendentes.

### A03 — Leitura HTTP e descompressão gzip sem limite explícito de tamanho [MITIGADO, CI PENDENTE]
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade:** P2, resiliência.
- **Evidência original:** leitura HTTP e descompressão gzip sem limites.
- **Correção aplicada:** downloads usados pelo módulo `region.py` são limitados a 16 MiB; saída gzip é limitada a 64 MiB antes do parsing.
- **Regressões adicionadas:** testes para resposta HTTP acima do limite e expansão gzip excessiva.
- **Limitação restante:** feeds e malhas locais são arquivos locais confiáveis e não têm limite de leitura comprimida antes da descompressão; avaliar se o uso operacional exige limites também para arquivos locais.

### A04 — Redirecionamentos HTTP e destinos de rede [MITIGADO PARCIALMENTE]
- **Área:** `src/mesh_alerts/http_utils.py`.
- **Prioridade:** P2, condicionada à origem configurada.
- **Correção aplicada anteriormente:** cada destino de redirecionamento é validado como HTTP(S), e redirecionamentos HTTPS→HTTP são recusados.
- **Correção adicional:** URLs com credenciais embutidas e portas inválidas são recusadas; endereços IP literais não globais, incluindo privados, loopback, link-local e reservados, são bloqueados, inclusive em redirects.
- **Regressões adicionadas:** esquemas inválidos, downgrade HTTPS, redirect HTTPS válido, IPs IPv4/IPv6 não públicos, credenciais, porta inválida e redirect para loopback.
- **Limitação restante importante:** isso não bloqueia hostname público que resolva para IP privado/loopback nem cobre DNS rebinding. Para proteção SSRF completa, a aplicação precisa de allowlist de hosts ou resolução DNS validada ligada à conexão e controles de egress na infraestrutura.

### A05 — Cancelamento CAP altera estado, mas não envia aviso de cancelamento pelo rádio
- **Área:** `src/mesh_alerts/service.py` e testes do serviço.
- **Prioridade:** decisão funcional/operacional P2.
- **Evidência estática:** o serviço documenta que cancelamentos atualizam o ciclo de vida e invalidam mensagens pendentes, sem criar um aviso de cancelamento para transmissão.
- **Impacto possível:** destinatários que já receberam o alerta podem não saber que ele foi cancelado.
- **Recomendação:** confirmar explicitamente o requisito operacional. Se os destinatários precisam receber revogações, definir formato e prioridade para uma mensagem de cancelamento.
- **Observação:** comportamento intencional não é, por si só, bug; precisa de decisão de produto e segurança operacional.

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
- **Validação:** testes de unidade, análise de segurança/qualidade e validação live CAP/IBGE passaram no commit `3041ff6`.

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
- A02 e A05 continuam pendentes de análise/decisão funcional. A06 e A08 foram corrigidos com regressões adicionadas; os workflows de testes, segurança/qualidade e validação live CAP/IBGE passaram no commit `3041ff6`.

## Próximos passos

1. Preservar o resultado verde da CI para A06 e A08 ao continuar alterações.
2. Exercitar falha de transporte INMET e documentar recuperação manual, sem retransmissão silenciosa.
3. Confirmar se cancelamentos CAP precisam ser transmitidos aos nós Meshtastic.
4. Avaliar proteção de destinos resolvidos por DNS e limitar entradas de arquivos locais se elas não forem totalmente confiáveis.

## Limitações

- Não foi possível clonar o repositório nesta sessão por falha de resolução de `github.com`; os arquivos foram inspecionados via integração GitHub.
- Os testes automatizados foram executados via GitHub Actions; não foi possível executar uma cópia local independente nesta sessão por falha de resolução de `github.com`.
- Nenhuma transmissão de rádio foi executada.
- As severidades originais são triagem estática, não avaliação de exploração confirmada.
- Nenhum resultado de teste hipotético deve ser reportado como execução real.

## Estado da branch

A branch de auditoria é separada de `main`. Este relatório não autoriza merge, criação de pull request ou alteração da branch principal.
