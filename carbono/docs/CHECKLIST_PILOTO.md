# Checklist de lançamento do piloto

50 itens em 7 fases, para colocar o Lastro Carbono no ar com uma prefeitura. Cada item indica o responsável e, quando há, a seção do `docs/IMPLANTACAO.md` com o passo a passo.

**Caminho crítico:** as credenciais do gov.br e do ITI (fase 2) têm prazo e dependem do domínio oficial e da integração com o Login Único. Peça na semana 1.

## Decisões e responsáveis (Semana 1)

Quem responde pelo piloto, qual é o escopo e como o órgão formaliza a adesão.

- [ ] **Definir o instrumento jurídico do piloto** — _Jurídico_. Contratação, termo de cooperação ou outro instrumento, conforme a Lei 14.133/2021 e a orientação da assessoria jurídica. A plataforma não define isso.
- [ ] **Designar o gestor do piloto e um substituto** — _Gestão do piloto_. Por ato formal. É quem decide escopo, prazos e prioridades e responde pelo piloto.
- [ ] **Definir o controlador e o encarregado (DPO)** — _Encarregado (LGPD)_. Os nomes vão em CARBONO_CONTROLADOR e CARBONO_ENCARREGADO e aparecem na política de privacidade e no aceite. (IMPLANTACAO §6)
- [ ] **Fechar o escopo** — _Gestão do piloto_. Unidades e prédios, ano-base do inventário, contratos terceirizados participantes e se haverá compra de créditos no piloto.
- [ ] **Garantir o organismo de verificação independente** — _Gestão do piloto_. Contratação ou parceria com organismo acreditado para verificar o inventário (Lei 15.042/2024, art. 32, parágrafo único). Costuma ter prazo longo: comece já.
- [ ] **Combinar os indicadores do piloto** — _Gestão do piloto_. Por exemplo: % de lançamentos com comprovante, terceirizadas que lançam até o dia limite, tempo entre submissão e aprovação.

## Credenciais e pedidos com prazo (Pedir na semana 1)

É o caminho crítico: os pedidos ao gov.br e ao ITI têm prazo e dependem uns dos outros.

- [ ] **Reservar um subdomínio oficial de governo** — _TI_. Ex.: lastro.municipio.mg.gov.br. Sem domínio oficial não sai a credencial de produção da assinatura gov.br (Portaria SGD/MGI nº 7.076/2024, arts. 3º e 5º). (IMPLANTACAO §12)
- [ ] **Gerar par de chaves PGP com data de validade** — _TI_. Usado para receber as credenciais do gov.br. Chave sem data de expiração não é aceita. (IMPLANTACAO §12)
- [ ] **Pedir a credencial do Login Único (homologação)** — _Gestor público_. Pelo Serviço de Integração aos Produtos de Identidade Digital gov.br. URL de retorno /auth/govbr/retorno, URL de logout /painel/, escopos openid email profile govbr_confiabilidades_idtoken. (IMPLANTACAO §3)
- [ ] **Pedir a credencial da API de assinatura (homologação)** — _Gestor público_. Só gestor público pode pedir. Sai em até 3 dias úteis. URL de retorno /assinaturas/iti/retorno. (IMPLANTACAO §12)
- [ ] **Conta de e-mail de envio e registros do domínio** — _TI_. SMTP do órgão ou serviço contratado, remetente do domínio do órgão e SPF, DKIM e DMARC configurados. (IMPLANTACAO §10)
- [ ] **Escolher o fundo do mapa** — _TI_. OpenStreetMap só para o piloto. Em produção, provedor contratado ou servidor de mapas do Estado (CARBONO_MAPA_TILES). (IMPLANTACAO §11)
- [ ] **Contas gov.br prata ou ouro para quem vai assinar** — _Usuários_. Representantes do órgão e das empresas parceiras. Conta bronze é barrada na assinatura. (IMPLANTACAO §12)
- [ ] **Pedir as credenciais de produção** — _Gestor público_. Depois da homologação, com o vídeo dos fluxos. A produção da assinatura sai em até 5 dias úteis e exige o Login Único já integrado. (IMPLANTACAO §3 e §12)

## Infraestrutura (Semanas 2 e 3)

Servidor, rede, segredos, rotinas e monitoramento.

- [ ] **Provisionar o servidor em região do Brasil** — _TI_. Referência: 2 vCPU, 4 GB de RAM (o ClamAV usa cerca de 1,5 GB), 40 GB de disco, Docker e Docker Compose. (IMPLANTACAO §1)
- [ ] **Liberar a saída de rede do servidor** — _TI_. Login Único (sso.acesso.gov.br), API de assinatura do ITI (endereços da credencial), pncp.gov.br, database.clamav.net, o servidor SMTP e o provedor do mapa.
- [ ] **Proxy reverso com TLS** — _TI_. Caddy, Nginx ou balanceador na frente; a porta 8000 fica só local. O cookie de sessão exige HTTPS. (IMPLANTACAO §2.3)
- [ ] **Gerar o CARBONO_SEGREDO e guardá-lo em cofre** — _TI_. Não pode ser trocado depois: gera os códigos dos CPFs. Sem ele o sistema não sobe. (IMPLANTACAO §2.1)
- [ ] **Preencher o .env** — _TI_. URL pública, LGPD, gov.br, ITI, SMTP, mapa e PNCP. Nunca versionar o .env. (.env.example)
- [ ] **Subir a plataforma e o antivírus** — _TI_. docker compose build e up. O /saude precisa responder "ok" com o antivírus "ok". (IMPLANTACAO §2.2)
- [ ] **Criar a administração com verificação em duas etapas** — _TI_. python -m plataforma criar-admin. Cadastrar o segredo TOTP no aplicativo autenticador. (IMPLANTACAO §2.2)
- [ ] **Agendar as rotinas diárias** — _TI_. rotina-diaria, varrer-anexos e backup, com cópia dos backups para outro local no Brasil. (IMPLANTACAO §4)
- [ ] **Testar a restauração do backup** — _TI_. Antes do lançamento e depois uma vez por mês. (IMPLANTACAO §4.1)
- [ ] **Monitorar o /saude de fora** — _TI_. Resposta 503 significa antivírus fora do ar: os envios de arquivo ficam bloqueados até ele voltar. (IMPLANTACAO §5.1)

## Jurídico e LGPD (Semanas 1 a 4, em paralelo)

Textos, bases legais e cláusulas que a plataforma pressupõe.

- [ ] **Revisar a política de privacidade e os termos de uso** — _Jurídico_. Ao mudar o texto, trocar CARBONO_TERMOS_VERSAO: todos aceitam de novo. (IMPLANTACAO §6)
- [ ] **Registrar as operações de tratamento e avaliar o relatório de impacto** — _Encarregado (LGPD)_. LGPD, arts. 37 e 38. (IMPLANTACAO §6)
- [ ] **Confirmar a base legal dos e-mails automáticos** — _Jurídico_. Execução do contrato e obrigação legal (LGPD, art. 7º, II e V, e art. 23). Lembretes podem ser desligados pelo titular; notificações formais não. (IMPLANTACAO §10)
- [ ] **Revisar os modelos de ETP, TR e relatório** — _Jurídico_. Foram escritos a partir da estrutura dos arts. 18 e 6º, XXIII, da Lei 14.133/2021, sem conferência com o texto vigente no Planalto. (IMPLANTACAO §13)
- [ ] **Cláusula de relato de emissões nos contratos participantes** — _Fiscais de contrato_. Por aditivo, quando cabível. Sem cláusula, o atraso no lançamento é só informativo, não inadimplência.

## Cadastros e dados (Semanas 3 e 4)

O que precisa estar na plataforma antes do primeiro lançamento.

- [ ] **Cadastrar as organizações** — _Gestão do piloto_. Prefeitura (CNPJ e código IBGE), organismo verificador e terceirizadas. (IMPLANTACAO §2.4)
- [ ] **Cadastrar os usuários e enviar os convites** — _Gestão do piloto_. Servidores com CPF (entram pelo gov.br); empresas por e-mail, com convite válido por 72 horas. (IMPLANTACAO §2.4)
- [ ] **Cadastrar unidades, prédios e instalações** — _Gestão do piloto_. Com o nº da instalação na CEMIG (UC) em cada prédio, para as faturas caírem na unidade certa. (IMPLANTACAO §8)
- [ ] **Conferir e publicar os fatores de emissão vigentes** — _Gestão do piloto_. O fator do SIN carregado vem marcado "CONFERIR ano vigente": publique o valor oficial do MCTI do ano-base. (IMPLANTACAO §2.4)
- [ ] **Cadastrar os contratos das terceirizadas** — _Fiscais de contrato_. Com o dia limite de lançamento e a cláusula de relato, e convidar as empresas.
- [ ] **Carregar as faturas de energia do ano-base** — _Gestão do piloto_. PDF com conferência linha a linha ou planilha (CSV ou XLSX). (IMPLANTACAO §8)

## Testes de aceitação em homologação (Semanas 4 e 5)

Cada teste com o resultado esperado. Registre a data e quem testou.

- [ ] **Entrar com o gov.br e sair** — _TI_. Esperado: entrada pelo Login Único e, ao sair, passagem pelo logout do gov.br. (IMPLANTACAO §3)
- [ ] **Administração com duas etapas e bloqueio** — _TI_. Esperado: sem código não entra; 5 senhas erradas bloqueiam por 15 minutos. (IMPLANTACAO §5)
- [ ] **Arquivo de teste EICAR dentro de um ZIP** — _TI_. Esperado: envio recusado e tentativa registrada na trilha de auditoria. (IMPLANTACAO §5.1)
- [ ] **Importar uma fatura real da CEMIG e uma planilha** — _Gestão do piloto_. Esperado: instalação, mês e consumo corretos; sem duplicidade no mesmo mês. (IMPLANTACAO §8)
- [ ] **Lançar uma atividade sem sinal no celular** — _Usuários_. Esperado: fica em "Para enviar" e sobe sozinha quando a conexão volta. (IMPLANTACAO §7)
- [ ] **Receber convite e lembrete por e-mail** — _TI_. Esperado: chegam na caixa de entrada, não no spam, com o remetente do órgão. (IMPLANTACAO §10)
- [ ] **Assinar um contrato de teste** — _Usuários_. Esperado: conta bronze barrada com o link oficial; conta prata assina; PDF e .p7s validados em validar.staging.iti.br. (IMPLANTACAO §12)
- [ ] **Vincular um pedido e registrar uma fonte de preço** — _Gestão do piloto_. Esperado: contratação real do órgão conferida no PNCP; fonte de outro ente aparece no ETP. (IMPLANTACAO §14)
- [ ] **Restaurar um backup em servidor de teste** — _TI_. Esperado: manifesto, integridade e cadeia do livro-razão conferidos. (IMPLANTACAO §4.1)
- [ ] **Gravar os vídeos de homologação do gov.br** — _TI_. Login Único e assinatura, com a barra de endereços visível: conta bronze, conta prata, download do PDF e dos .p7s e logout. (IMPLANTACAO §12)

## Lançamento e acompanhamento (Semana 6 em diante)

Virada para produção, treinamento e revisões do piloto.

- [ ] **Treinar cada papel** — _Gestão do piloto_. Elaborador, verificador, moderador, fiscal de contrato e terceirizada, cada um no seu fluxo.
- [ ] **Definir o canal de suporte do primeiro mês** — _Gestão do piloto_. Quem atende, por onde e em quanto tempo.
- [ ] **Comunicar as terceirizadas** — _Fiscais de contrato_. Prazo de lançamento, como lançar com comprovante e a política de privacidade.
- [ ] **Virar para as credenciais de produção** — _TI_. Login Único e assinatura de produção no .env; conferir /saude, um e-mail e uma assinatura real.
- [ ] **Revisar o piloto em 30, 60 e 90 dias** — _Gestão do piloto_. Com os indicadores combinados na semana 1 e a lista de ajustes.

## Limites conhecidos do piloto

- Assinatura em arquivo .p7s separado do PDF; a assinatura embutida no PDF (PAdES) ainda não existe.
- Só assinatura avançada gov.br; a qualificada ICP-Brasil e um assinador privado para quem não tem conta prata ou ouro ainda não existem.
- No PNCP, a busca de compras de créditos é manual no portal: a busca por palavra-chave não tem API pública.
- Não há integração com o Registro Central do SBCE, porque a API oficial ainda não existe.
- Banco SQLite: suficiente para um órgão no piloto; os critérios para migrar ao PostgreSQL estão na seção 1 da IMPLANTACAO.
