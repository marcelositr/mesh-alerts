# Documentação técnica

Esta seção é destinada a quem desenvolve, revisa ou mantém o `mesh-alerts`.

- [Arquitetura](architecture.md): responsabilidades dos componentes e fluxo de dados.
- [Desenvolvimento e testes](development.md): ambiente local, suíte automatizada e pontos de entrada do código.

## Referência do repositório

- [`src/mesh_alerts/`](../src/mesh_alerts/): implementação.
- [`tests/`](../tests/): testes automatizados.
- [`tests/fixtures/`](../tests/fixtures/): amostras versionadas usadas em testes e validação.
- [`config.yaml`](../config.yaml): configuração de referência.
- [`deploy/`](../deploy/): exemplos de ambiente e unidades systemd.
- [`.github/workflows/`](../.github/workflows/): verificações automatizadas.
- [`pyproject.toml`](../pyproject.toml): metadados do pacote, dependências e comandos instalados.

As instruções de instalação e uso estão no [README principal](../README.md). Cada assunto deve ter uma única descrição de referência; use os links acima em vez de duplicar o conteúdo.
