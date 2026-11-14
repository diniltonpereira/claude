# Carbono Público: gestão, aferição, moderação e compensação de carbono

Plataforma para **órgãos públicos, PPPs e concessões** inventariarem emissões, submeterem o inventário a verificação independente e compensarem com créditos rastreáveis. Foi construída sobre o marco legal brasileiro: Lei 15.042/2024 (SBCE), Lei 12.187/2009, Lei 14.133/2021, Lei 11.079/2004, Lei 8.987/1995 e Lei 11.284/2006. A análise jurídica está em [`docs/MARCO_LEGAL.md`](docs/MARCO_LEGAL.md).

```
 Aferição            Moderação (MRV)                         Registro                Compensação
 atividades ──► rascunho ► submetido ► em_verificação ► verificado ► aprovado ──► cancelar CRVE/CBE
 (fatores GHG)      ▲  elaborador   organismo acreditado   controladoria    │      (livro-razão com hash)
                    └────────── devolvido (parecer obrigatório) ◄────────────┘
                                         │
                             Enquadramento SBCE (10 mil / 25 mil tCO2e) + obrigações contratuais
```

## Módulos

| Arquivo | Função |
| --- | --- |
| `plataforma/fatores.py` | Fatores de emissão com fonte e ano, e GWP do AR5 |
| `plataforma/calculo.py` | Aferição por fator ou por gás, com parcela biogênica separada e memória de cálculo |
| `plataforma/mrv.py` | Instalações e fontes, lançamento de atividades e fluxo de verificação e moderação com segregação de funções |
| `plataforma/enquadramento.py` | Faixa no SBCE por instalação (art. 30), fase de implantação (art. 50), obrigações por tipo e checagens de projeto público (arts. 43 e 49) e de repartição com comunidades (art. 47) |
| `plataforma/registro.py` | Emissão, transferência e cancelamento (art. 2º, II; art. 45) de lotes, com livro-razão somente-inclusão encadeado por SHA-256 |
| `plataforma/compensacao.py` | Compensação voluntária (art. 45) e conciliação periódica sobre emissões líquidas (art. 34), com limite de CRVE do PNA e validade da CBE por período |
| `plataforma/api.py` | API REST (FastAPI). Documentação em `/docs` |

## Regras que o sistema garante

- **Segregação de funções:** quem elabora não verifica nem modera. O verificador precisa ser organismo de inspeção acreditado (art. 32, parágrafo único) e externo à organização. Toda devolução exige parecer.
- **Sem dupla contagem (art. 2º, IX):** uma unidade cancelada nunca volta a circular, e o saldo não fica negativo. As operações rodam em transação com trava de escrita.
- **Auditoria:** o livro-razão não aceita UPDATE nem DELETE (triggers), e `GET /registro/auditoria` recalcula a cadeia de hashes.
- **Compensação com lastro:** só se compensa inventário aprovado, sem passar do total, com safra entre o ano do inventário e 5 anos antes.
- **Conciliação conforme a lei:** só CBE e CRVE (art. 10), sobre emissões líquidas (art. 2º, V). A CRVE respeita o limite do PNA (art. 21, IV), e a CBE vale no próprio período, salvo autorização (art. 11, § 2º).
- **Idempotência:** operações reenviadas com a mesma `chave_idempotencia` não se repetem.
- **Acesso:** cada usuário só lê e movimenta dados da própria organização (testado contra IDOR).

## Painel web

A própria aplicação serve um painel em `http://127.0.0.1:8000/painel/` (a raiz redireciona para ele). O menu muda conforme o papel do usuário:

| Papel | O que faz no painel |
| --- | --- |
| Elaboração | Cadastra instalações e fontes, cria o inventário, lança atividades com evidência, submete, vê o enquadramento com os fundamentos legais e compensa (com confirmação antes de cancelar) |
| Verificação (organismo acreditado) | Assume inventários da fila, conclui ou devolve com parecer |
| Moderação | Aprova ou devolve inventários verificados |
| Registro | Cadastra organizações e usuários e escritura lotes emitidos |
| Público, sem login | Transparência: créditos cancelados e integridade do livro-razão |

A trilha do inventário mostra cada etapa carimbada com quem agiu e quando. Para experimentar: `python -m plataforma demo --banco demo.db` e depois `python -m plataforma servir --banco demo.db`. Usuários da demonstração: 1 registro, 2 elaboração (prefeitura), 3 verificação, 4 moderação, 6 elaboração (concessionária).

## Como rodar

```bash
cd carbono
pip install -r requirements.txt
python -m pytest -q                      # 51 testes (inclui o painel)
python -m plataforma demo                # ciclo completo com dados fictícios
python -m plataforma iniciar             # cria o banco e o 1º registrador
python -m plataforma servir --porta 8000 # painel em /painel/, API em /docs
```

Na API, identifique o usuário pelo cabeçalho `X-Usuario-Id` (somente no MVP).

## Antes de usar em produção

1. Trocar o `X-Usuario-Id` por login gov.br (OIDC) ou certificado ICP-Brasil.
2. Atualizar os fatores de emissão (MCTI e Programa Brasileiro GHG Protocol) e os limiares do SBCE quando sair a regulamentação definitiva.
3. Migrar do SQLite para PostgreSQL se houver muitos entes simultâneos.
4. Integrar com o Sistema de Registro Central do SBCE e com o processo eletrônico (SEI) para guardar as evidências.
5. Acompanhar a regulamentação do órgão gestor e ajustar os parâmetros (lista em `docs/MARCO_LEGAL.md`, seção 4).
