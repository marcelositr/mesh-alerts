# Auditoria técnica: mesh-alerts

- Data: 2026-10-24
- Branch de trabalho: `audit/full-audit-2026-10-24`
- Escopo: revisão estática do código-fonte, testes existentes e checks CI previamente concluídos no commit-base.
- Estado: revisão estática consolidada; testes dinâmicos independentes ainda pendentes.
- Restrição operacional: esta auditoria não altera a branch `main`, não abre pull request e não realiza transmissão via rádio.

## Resumo executivo

A revisão identificou riscos e lacunas de comportamento que merecem testes dirigidos. Nenhum dos cenários abaixo foi reproduzido dinamicamente nesta sessão, portanto as severidades são provisórias e os itens não devem ser interpretados como vulnerabilidades exploradas. Os workflows CI anteriormente consultados passaram no commit-base, mas isso não substitui a reprodução dos cenários descritos.

## Achados priorizados

### A01 — Configuração carregada no momento da importação
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade:** P2, portabilidade/CLI.
- **Evidência estática:** `DEFAULT_CONFIG = load_config()` no escopo do módulo. Como o serviço importa o módulo, a leitura do `config.yaml` padrão pode ocorrer antes de a configuração escolhida por `--config` ser processada.
- **Impacto possível:** execução fora do diretório do projeto pode falhar mesmo quando o operador fornece um arquivo de configuração válido.
- **Teste recomendado:** executar o CLI a partir de um diretório temporário, apontando `--config` para um arquivo válido fora da raiz do projeto.

### A02 — Falha de transmissão INMET não é recuperada automaticamente
- **Área:** `src/mesh_alerts/inmet_scheduler.py`
- **Prioridade:** P2, operação.
- **Evidência estática:** o registro é reservado antes do I/O externo; uma exceção atualiza o estado para `failed`, e a existência do registro impede nova tentativa automática.
- **Impacto possível:** um boletim pode não ser transmitido após falha, exigindo procedimento operacional manual. A decisão reduz o risco de duplicação quando o resultado da transmissão é incerto.
- **Recomendação:** documentar a reconciliação manual e avaliar um comando explícito de recuperação que exija decisão do operador.
- **Teste recomendado:** simular exceção do transporte, reiniciar o processo e confirmar que não há retransmissão silenciosa.

### A03 — Leitura HTTP e descompressão gzip sem limite explícito de tamanho
- **Área:** `src/mesh_alerts/region.py`
- **Prioridade:** P2, resiliência.
- **Evidência estática:** uso de `response.read()` e `gzip.decompress()` sem limite explícito de bytes.
- **Impacto possível:** uma resposta excessiva ou conteúdo comprimido desproporcional pode consumir memória em excesso.
- **Recomendação:** impor limites tanto à leitura comprimida quanto ao conteúdo descomprimido; validar conteúdo e tamanho antes do parsing.
- **Teste recomendado:** respostas grandes, gzip truncado e conteúdo com razão de expansão excessiva.

### A04 — Redirecionamentos HTTP não são revalidados explicitamente
- **Área:** `src/mesh_alerts/http_utils.py`
- **Prioridade:** P2, condicionada à origem configurada.
- **Evidência estática:** a URL inicial é validada como HTTP(S), mas `urllib.request.urlopen()` pode seguir redirecionamentos sem uma validação explícita do destino final. A configuração padrão exige HTTPS para fontes conhecidas, o que reduz a exposição no fluxo normal.
- **Impacto possível:** se uma URL configurada ou uma origem confiável for comprometida, o redirecionamento pode levar a um destino inesperado.
- **Recomendação:** validar destinos finais e definir uma política de hosts e HTTPS adequada ao modelo de implantação.
- **Teste recomendado:** redirecionamentos para esquema não permitido, host não autorizado e endereço local.

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

### A07 — Arquivos TXT podem ser gravados antes da aceitação persistente
- **Área:** `src/mesh_alerts/service.py`
- **Prioridade:** P2, consistência entre artefatos.
- **Evidência estática:** os arquivos de saída são escritos antes da chamada a `process_cap_alert()`, que pode retornar `conflict`.
- **Impacto possível:** um TXT pode ser criado ou sobrescrito mesmo que o banco recuse o conteúdo para aquela identidade CAP.
- **Recomendação:** persistir/decidir primeiro e gravar os arquivos somente para um resultado aceito, com política clara para `duplicate`.
- **Teste recomendado:** processar duas versões diferentes com o mesmo remetente e identificador e comparar TXT, banco e outbox.

### A08 — Referências CAP são reduzidas a remetente/identificador, descartando o timestamp
- **Área:** `src/mesh_alerts/cap.py` e fluxo de persistência CAP.
- **Prioridade:** P2 provisória, integridade de protocolo.
- **Evidência estática:** `parse_reference_keys()` valida que o terceiro componente seja um timestamp válido, mas retorna apenas o par remetente/identificador. A supressão no feed usa esse par.
- **Impacto possível:** uma referência com timestamp que não corresponde à mensagem original pode suprimir um alerta com a mesma identidade. O caminho de persistência também recebe referências como pares, então a correção completa deve abranger ambos os caminhos, não apenas o filtro do feed.
- **Recomendação:** preservar e comparar o timestamp da referência com o campo `sent` da mensagem referenciada; definir como tratar referências a mensagens ausentes do feed atual.
- **Teste recomendado:** referência válida, timestamp divergente, identificador igual com remetente diferente e referência a alerta ausente do feed.

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

## Plano de validação final

1. Reproduzir A08 com timestamp divergente e verificar tanto a seleção do feed quanto o estado persistido.
2. Reproduzir A06 e A07 com SQLite e diretório de saída temporários.
3. Testar A01 fora do diretório do projeto.
4. Testar respostas HTTP excessivas e redirecionamentos inesperados.
5. Exercitar falha de transporte INMET e documentar a recuperação manual.
6. Confirmar com o responsável operacional se cancelamentos precisam ser transmitidos aos nós Meshtastic.

## Limitações

- Não foi possível clonar o repositório nesta sessão por falha de resolução de `github.com`; os arquivos foram inspecionados via integração GitHub.
- Não foram executados testes locais independentes nem transmissões de rádio nesta sessão.
- As severidades são triagem estática, não avaliação de exploração confirmada.
- Nenhum resultado de teste hipotético deve ser reportado como execução real.

## Estado da branch

A branch de auditoria é separada de `main`. Este relatório não autoriza merge, criação de pull request ou alteração da branch principal.
