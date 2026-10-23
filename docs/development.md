# Desenvolvimento e testes

## Ambiente local

Requer Python 3.10 ou posterior. Na raiz do repositório, crie um ambiente virtual e instale o pacote em modo editável:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

As dependências diretas são declaradas em [`pyproject.toml`](../pyproject.toml) e também listadas em [`requirements.txt`](../requirements.txt). O pacote instala os comandos `mesh-alerts-validate`, `mesh-alerts-service` e `mesh-alerts-inmet`.

## Testes locais

Execute a suíte completa com a biblioteca padrão `unittest`:

```bash
python -m unittest discover -s tests -v
```

Para compilar os módulos e testes sem executá-los:

```bash
python -m compileall -q src tests
```

A suíte automatizada usa dados de teste e fixtures versionados. Ela não deve transmitir mensagens pelo Meshtastic nem depender de consultas externas ao vivo. Os testes e fixtures estão organizados em [`tests/`](../tests/).

## Verificações no GitHub Actions

O workflow [`tests.yml`](../.github/workflows/tests.yml) instala o pacote, compila o código, executa os testes e verifica consistência das dependências em Python 3.10, 3.11, 3.12 e 3.13. Também constrói as distribuições do pacote, instala o wheel, verifica os comandos de terminal e executa a suíte contra a instalação construída.

O workflow [`live-cap-validation.yml`](../.github/workflows/live-cap-validation.yml) valida o feed CAP atual usando o GeoJSON municipal versionado em [`tests/fixtures/`](../tests/fixtures/). A consulta de disponibilidade ao vivo do IBGE é um job separado, executado por agendamento ou disparo manual, e seus erros são avisos. A validação com fixture e a sondagem ao vivo respondem a perguntas diferentes.

O workflow [`security.yml`](../.github/workflows/security.yml) executa Ruff, Bandit e pip-audit, valida sintaxe e expressões dos workflows com actionlint, audita riscos de GitHub Actions com zizmor e verifica segredos no histórico com Gitleaks. O actionlint é baixado com verificação de SHA-256; as referências de Actions são fixadas em revisões imutáveis. O zizmor bloqueia achados de alta severidade, enquanto os resultados de menor severidade precisam ser avaliados separadamente.

Esses checks devem ser confirmados como obrigatórios nas regras da branch principal quando o plano e a visibilidade do repositório permitirem essa configuração.

## Releases e dependências

O workflow [`release.yml`](../.github/workflows/release.yml) é acionado por tags cujo nome começa com `v`. Na própria revisão apontada pela tag, ele executa testes em Python 3.10 a 3.13, Ruff, Bandit, pip-audit, actionlint, zizmor e Gitleaks. A publicação só começa quando os testes e a auditoria de segurança passam. Depois, constrói wheel e distribuição de fontes com a versão derivada da tag e anexa os artefatos a uma GitHub Release. Tags contendo `alpha`, `beta` ou `rc`, sem diferenciar maiúsculas de minúsculas, geram releases marcadas como pré-lançamento. Esse fluxo publica na GitHub Release, não no PyPI.

O arquivo [`dependabot.yml`](../.github/dependabot.yml) agenda atualizações semanais para dependências Python e GitHub Actions. Os PRs gerados pelo Dependabot ainda devem passar pelos mesmos checks de CI.

## Pontos de entrada

Os comandos instalados são definidos em [`pyproject.toml`](../pyproject.toml):

- `mesh-alerts-validate` chama `mesh_alerts.region:main`.
- `mesh-alerts-service` chama `mesh_alerts.service:main`.
- `mesh-alerts-inmet` chama `mesh_alerts.inmet_cli:main`.

Os módulos também podem ser invocados com `python -m mesh_alerts.region`, `python -m mesh_alerts.service` e `python -m mesh_alerts.inmet_cli`, respectivamente.

## Configuração e implantação

Use [`config.yaml`](../config.yaml) como referência da configuração geral. Os parâmetros e exemplos de ambiente estão em [`deploy/mesh-alerts.env.example`](../deploy/mesh-alerts.env.example); as unidades systemd estão em [`deploy/mesh-alerts.service`](../deploy/mesh-alerts.service) e [`deploy/systemd/mesh-alerts-inmet.service`](../deploy/systemd/mesh-alerts-inmet.service).

O serviço CAP pode transmitir mensagens reais quando executado. O agendador INMET simula por padrão e requer a opção explícita `--send` para transmissão. Confira o [README principal](../README.md) antes de executar comandos que enviam mensagens.
