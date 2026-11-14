# Carbono Público: gestão, aferição, moderação e compensação de carbono

Plataforma para **órgãos públicos, PPPs e concessões** inventariarem emissões, submeterem o inventário a verificação independente e compensarem com créditos rastreáveis. Foi construída sobre o marco legal brasileiro: Lei 15.042/2024 (SBCE), Lei 12.187/2009, Lei 14.133/2021, Lei 11.079/2004, Lei 8.987/1995 e Lei 11.284/2006. A análise jurídica está em [`docs/MARCO_LEGAL.md`](docs/MARCO_LEGAL.md).

```
 Aferição            Moderação (MRV)                         Registro                Compensação
 atividades ──► rascunho ► submetido ► em_verificação ► verificado ► aprovado ──► aposentar CRVE/CBE
 (fatores GHG)      ▲  elaborador     OVV independente     controladoria    │      (livro-razão com hash)
                    └────────── devolvido (parecer obrigatório) ◄────────────┘
                                         │
                             Enquadramento SBCE (10 mil / 25 mil tCO2e) + obrigações contratuais
```

## Módulos

| Arquivo | Função |
| --- | --- |
| `plataforma/fatores.py` | Fatores de emissão com fonte e ano, e GWP do AR5 |
| `plataforma/calculo.py` | Aferição por fator ou por gás, com parcela biogênica separada e memória de cálculo |
| `plataforma/mrv.py` | Fluxo de verificação e moderação com segregação de funções |
| `plataforma/enquadramento.py` | Faixa no SBCE e obrigações por tipo (órgão público, PPP, concessão, concessão florestal) |
| `plataforma/registro.py` | Emissão, transferência, aposentadoria e cancelamento de lotes, com livro-razão somente-inclusão encadeado por SHA-256 |
| `plataforma/compensacao.py` | Compensação voluntária e conciliação SBCE com regras de elegibilidade |
| `plataforma/api.py` | API REST (FastAPI). Documentação em `/docs` |

## Regras que o sistema garante

- **Segregação de funções:** quem elabora não verifica nem modera. O verificador precisa ser OVV credenciado e externo à organização. Toda devolução exige parecer.
- **Sem dupla contagem:** uma unidade aposentada ou cancelada nunca volta a circular, e o saldo não fica negativo. As operações rodam em transação com trava de escrita.
- **Auditoria:** o livro-razão não aceita UPDATE nem DELETE (triggers), e `GET /registro/auditoria` recalcula a cadeia de hashes.
- **Compensação com lastro:** só se compensa inventário aprovado, sem passar do total, com safra entre o ano do inventário e 5 anos antes. CBE não entra em compensação voluntária, e a CRVE na conciliação respeita o limite configurado.
- **Idempotência:** operações reenviadas com a mesma `chave_idempotencia` não se repetem.
- **Acesso:** cada usuário só lê e movimenta dados da própria organização (testado contra IDOR).

## Como rodar

```bash
cd carbono
pip install -r requirements.txt
python -m pytest -q                      # 25 testes
python -m plataforma demo                # ciclo completo com dados fictícios
python -m plataforma iniciar             # cria o banco e o 1º registrador
python -m plataforma servir --porta 8000 # API em http://127.0.0.1:8000/docs
```

Na API, identifique o usuário pelo cabeçalho `X-Usuario-Id` (somente no MVP).

## Antes de usar em produção

1. Trocar o `X-Usuario-Id` por login gov.br (OIDC) ou certificado ICP-Brasil.
2. Atualizar os fatores de emissão (MCTI e Programa Brasileiro GHG Protocol) e os limiares do SBCE quando sair a regulamentação definitiva.
3. Migrar do SQLite para PostgreSQL se houver muitos entes simultâneos.
4. Integrar com o Sistema de Registro Central do SBCE e com o processo eletrônico (SEI) para guardar as evidências.
5. Validar os pontos ⚠️ de `docs/MARCO_LEGAL.md` no texto oficial.
