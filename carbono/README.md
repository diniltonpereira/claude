# CarbonMG: inventário, terceirizadas e compensação de carbono para o setor público

Plataforma para órgãos públicos, PPPs e concessões, com foco inicial em Minas Gerais. Ela permite:

- medir as emissões com comprovante anexado;
- passar o inventário por verificação independente e aprovação;
- acompanhar as emissões das terceirizadas, com score e selo;
- compensar com créditos certificados por terceiros, com rastro público.

**Regra de ouro:** a plataforma **não certifica, não verifica e não emite créditos**. Ela organiza a mensuração, o relato e a verificação (MRV), guarda as provas e espelha créditos emitidos pelo Registro Central do SBCE (CBE e CRVE) ou por certificadoras (Verra, Gold Standard).

Base legal e técnica:
- Lei 15.042/2024 (SBCE);
- Lei 14.133/2021;
- Lei 12.187/2009;
- Leis 11.079/2004 e 8.987/1995;
- LGPD;
- GHG Protocol e ABNT NBR ISO 14064-1.

Análise jurídica em [`docs/MARCO_LEGAL.md`](docs/MARCO_LEGAL.md).

## Como rodar

Requisitos: Python 3.12 ou 3.13. Não há etapa de build.

```bash
cd carbono
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                          # 70 testes
python -m plataforma demo --banco demo.db    # dados fictícios completos
python -m plataforma servir --banco demo.db  # http://127.0.0.1:8000 (painel) e /docs (API)
```

Na demonstração, entre com estes números de usuário:

| Nº | Papel | O que ver |
| --- | --- | --- |
| 1 | Administração | Cadastros, fatores versionados, escrituração de lotes, auditoria |
| 2 | Gestão do órgão (prefeitura) | Painel, inventários, unidades, terceirizadas, compensação, relatórios |
| 3 | Verificação (organismo acreditado) | Fila de verificação, devolução com parecer |
| 4 | Moderação (controladoria) | Aprovação final |
| 6 | Gestão da concessionária | Enquadramento no SBCE por instalação |
| 7 | Fiscal de contrato | Terceirizadas, lançamento pela empresa, notificação formal, selo |
| 8 | Auditoria (TCE/TCU) | Tudo em modo leitura e a trilha de auditoria |
| 9 | Terceirizada | Lançamento mensal com nota fiscal, score, selo |

A rotina diária (lembretes, alertas e recálculo do score) deve ser agendada no servidor:

```bash
python -m plataforma rotina-diaria --banco carbono.db     # cron: 1 vez por dia
```

### Configuração

| Variável | Padrão | Uso |
| --- | --- | --- |
| `CARBONO_DB` | `carbono.db` | Arquivo do banco |
| `CARBONO_ANEXOS` | `anexos/` | Pasta dos comprovantes, gravados com o nome igual ao SHA-256 |
| `CARBONO_URL_PUBLICA` | endereço da requisição | Endereço público usado nos QR Codes (obrigatório atrás de proxy) |
| `PLATAFORMA_NOME` | `CarbonMG` | Nome exibido |

## O que a plataforma faz

| Módulo | Recursos |
| --- | --- |
| **Inventário de GEE** | Hierarquia órgão > unidade > prédio/instalação/frota; lançamento mensal ou anual; anexo obrigatório, sem o qual o lançamento fica pendente e fora do total; fatores versionados com AR5 ou AR6; emissões evitadas e remoções à parte; edição e exclusão auditadas |
| **Verificação e aprovação** | Elaboração > verificação por organismo acreditado e independente > aprovação pela controladoria; devolução com parecer; trilha carimbada |
| **Painel do gestor** | Totais por mês, escopo e unidade (somando a hierarquia), comparação com o ano anterior, meta de redução com progresso, alertas de variação de 30% e de lançamentos sem anexo |
| **SBCE** | Enquadramento por instalação (art. 30), fase de implantação (art. 50), obrigações com fundamento legal, conciliação sobre emissões líquidas |
| **Terceirizadas** | Convite por CNPJ e contrato; aceite; lançamento mensal com nota (pela empresa ou pelo fiscal, que fica marcado); situação mês a mês; inadimplência só com cláusula contratual; score 0 a 100; selo com QR Code e validade de 12 meses; notificação formal; lembretes e alertas automáticos; importação para o escopo 3 do órgão |
| **Créditos** | Espelho de CBE, CRVE e créditos voluntários; voluntário exige certificadora e certificado; cancelamento exige o nº da aposentadoria na certificadora; livro-razão com hash encadeado; certificado público verificável por hash e QR |
| **Relatórios e transparência** | Relatório do inventário em HTML para PDF e em Excel, com metodologia, fatores e versão, memória de cálculo, SHA-256 dos anexos e trilha; painel público por órgão; trilha de auditoria (quem, quando, IP, antes e depois) |

## Documentação para o desenvolvedor

- [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md): módulos, modelo de dados, papéis e permissões, regras que o banco garante, API e checklist de produção.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): o que veio da especificação CarbonMG, o que foi ajustado por razão jurídica e o que falta.
- [`docs/MARCO_LEGAL.md`](docs/MARCO_LEGAL.md): fundamentos legais.

## Avisos

- Os dados da demonstração são **fictícios**. Os fatores de emissão iniciais são referências: atualize-os pela tela de fatores com os valores oficiais (MCTI e Programa Brasileiro GHG Protocol) antes de um inventário real.
- Os valores de HFC e SF6 do conjunto AR6 estão marcados para conferência.
- O acesso por número de usuário serve só ao piloto. Ver o checklist de produção.
