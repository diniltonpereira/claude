# Instagram @diniltonpereira — publicação automática

Este repositório é a central de publicação do Instagram **@diniltonpereira**. Cada post é uma pasta em `instagram/posts/`. O GitHub Actions confere a fila a cada 30 minutos e publica pela API oficial do Instagram tudo o que já passou do horário marcado.

```
Claude (conversa) ──► commit em posts/ ──► GitHub Actions ──► API do Instagram ──► post no ar
                                              │
                                              └─► grava o link em state/publicados.json
```

## Como usar pelo Claude

Basta pedir na conversa, por exemplo:

- "Crie um carrossel sobre prestação de contas no MROSC para quinta às 19h."
- "Segue o vídeo do Reel do Tema 32. Pode agendar."
- "Adie o post do centro de custos para segunda."
- "Publique agora o checklist do estatuto."
- "O que já foi publicado esta semana?"

O Claude cria ou edita a pasta do post, faz o commit e o GitHub publica na hora marcada.

## Estrutura de um post

```
posts/2026-10-21-checklist-do-estatuto/
├── post.yml
├── slide01.jpg
└── slide02.jpg …
```

```yaml
titulo: "Checklist do estatuto"
tipo: carrossel            # imagem | carrossel | reel
status: agendado           # rascunho | agendado | aguardando_video
publicar_em: "2026-10-21T12:00:00-03:00"
midia:
  - slide01.jpg
  - slide02.jpg
legenda: |
  Texto da legenda.

  Livro completo: diniltonpereira.com.br/livros (link na bio).
hashtags: [terceirosetor, OSC]
```

| Status | O que acontece |
| --- | --- |
| `rascunho` | Fica no repositório e nunca é publicado. |
| `agendado` | É publicado na primeira execução depois de `publicar_em`. |
| `aguardando_video` | É um Reel ainda sem vídeo. Basta colocar o `video.mp4` na pasta e mudar para `agendado`. |

## Regras da API que o validador confere

- **Imagens:** só JPEG, com proporção entre 4:5 e 1,91:1 e até 1440 px de largura. O comando `python -m publisher preparar posts/<pasta>` converte PNG em JPEG.
- **Carrossel:** de 2 a 10 itens. Todos precisam ter a mesma proporção do primeiro, porque o Instagram recorta pelo primeiro.
- **Reel:** um arquivo `.mp4` ou `.mov`, com capa opcional (`capa: capa.jpg`).
- **Legenda:** até 2.200 caracteres e 30 hashtags.
- **Limite da conta:** 100 posts publicados pela API a cada 24 horas. O publicador solta no máximo 3 por execução.

## Comandos

```bash
pip install -r requirements.txt
python -m publisher validar        # confere todos os posts
python -m publisher listar         # mostra a fila
python -m publisher publicar --simular
python -m publisher conta          # confere token e cota (precisa de IG_USER_ID e IG_ACCESS_TOKEN)
python -m pytest -q                # testes com a API simulada
```

## Workflows

| Arquivo | Quando roda | O que faz |
| --- | --- | --- |
| `publicar.yml` | A cada 30 min, a cada push em `posts/` e manualmente | Publica os posts vencidos e registra o link em `state/publicados.json` |
| `validar.yml` | Push e pull request | Roda os testes e valida os posts |
| `renovar-token.yml` | Dias 1 e 20 de cada mês | Renova o token de 60 dias e regrava o segredo |

A configuração inicial, feita uma única vez, está em [CONFIGURACAO.md](CONFIGURACAO.md).
