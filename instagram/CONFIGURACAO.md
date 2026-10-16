# Configuração inicial (uma vez só, cerca de 30 minutos)

São três partes: preparar a conta do Instagram, criar o app na Meta e preparar o GitHub. Depois disso, tudo passa a ser feito pela conversa com o Claude.

## 1. Conta do Instagram

1. No app do Instagram, abra **Configurações › Tipo de conta e ferramentas** e mude para **conta profissional**, como Criador ou Empresa. Contas pessoais não publicam pela API.
2. Confira se o perfil é **@diniltonpereira**.

## 2. App na Meta (Instagram API com login do Instagram)

1. Acesse **developers.facebook.com**, entre e clique em **Meus apps › Criar app**.
2. Escolha o caso de uso de gerenciar mensagens e conteúdo no Instagram (o nome exato pode mudar). O tipo é **Empresa**.
3. No painel do app, abra **Instagram › Configuração da API com login do Instagram**.
4. Em **Gerar tokens de acesso**, adicione a conta @diniltonpereira e autorize as permissões:
   - `instagram_business_basic`
   - `instagram_business_content_publish`
5. Copie o **token de acesso**, que vale 60 dias e é renovado automaticamente pelo workflow, e o **ID da conta do Instagram**, um número que começa com `1784…`.

Como o app publica só na sua própria conta, que tem função no app, em geral ele funciona sem passar pela revisão da Meta. Se a Meta exigir revisão, o erro vai aparecer na execução de teste do passo 4.

> Trate o token como uma senha. Ele vai **somente** para os segredos do GitHub. Nunca coloque o token em arquivo, e-mail ou conversa.

## 3. GitHub

1. Crie uma conta em github.com, se ainda não tiver.
2. Use o repositório **público** `diniltonpereira/claude` (já criado). Ele precisa ser público porque a API do Instagram baixa as imagens de um endereço aberto. As legendas e imagens ficam visíveis no repositório antes da publicação. Os tokens, não: eles ficam nos segredos.
3. Em **Settings › Secrets and variables › Actions › New repository secret**, crie:

| Nome | Valor |
| --- | --- |
| `IG_USER_ID` | ID da conta do Instagram (passo 2.5) |
| `IG_ACCESS_TOKEN` | Token de acesso (passo 2.5) |
| `GH_PAT` | Token do GitHub que permite renovar o segredo acima (veja abaixo) |

   Para criar o `GH_PAT`, vá em **Settings da sua conta › Developer settings › Fine-grained tokens › Generate new token**. Restrinja ao repositório `claude`, dê permissão **Secrets: Read and write** e validade de 1 ano.
4. Conecte o GitHub ao Claude nas configurações do Claude (conectores) e dê acesso a esse repositório. É isso que permite ao Claude criar e agendar posts pela conversa.

## 4. Teste

1. No repositório, abra **Actions › Publicar no Instagram › Run workflow** e marque **Só simular**. O resumo da execução deve listar as URLs das imagens.
2. Abra uma dessas URLs no navegador: a imagem precisa aparecer.
3. Para um teste real, peça ao Claude: "publique agora o checklist do estatuto". O link do post aparece no resumo da execução e em `state/publicados.json`.

## Problemas comuns

| Mensagem | Causa provável |
| --- | --- |
| `Invalid OAuth access token` | Token expirado ou copiado errado. Gere outro no painel da Meta e atualize `IG_ACCESS_TOKEN`. |
| `Only photo or video can be accepted as media type` | Imagem em PNG ou URL inacessível. Confira se o repositório é público. |
| `The user is not an Instagram Business` | A conta ainda é pessoal (passo 1). |
| `Application request limit reached` | Muitas chamadas em pouco tempo. A próxima execução tenta de novo. |
| Post não saiu no horário | O agendamento do GitHub pode atrasar de 5 a 15 minutos em horários de pico. |
