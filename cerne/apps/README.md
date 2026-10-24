# Cerne · Aplicativos (Android, iOS e Windows)

Os três aplicativos empacotam **o mesmo painel** de [`../painel/`](../painel/), sem duplicar código.
A diferença para o site: no login o usuário informa também o **endereço do servidor**
(ex.: `https://cerne.seuprovedor.com.br`), que fica salvo no aparelho.

| Plataforma | Pasta | Tecnologia | Saída |
|---|---|---|---|
| Android | `mobile/android` | Capacitor 8 | `.apk` (teste) e `.aab` (Play Store) |
| iOS | `mobile/ios` | Capacitor 8 (Swift Package Manager) | envio pelo Xcode para App Store Connect |
| Windows | `desktop/` | Electron 44 + electron-builder | `Cerne-Setup-0.2.0.exe` (instalador NSIS em português) |

Identificador do app em todas as lojas: **`br.com.cerne.painel`**. Para trocar, altere `mobile/capacitor.config.json`,
`android/app/build.gradle` (`namespace`/`applicationId`), o *Bundle Identifier* no Xcode e `build.appId` em `desktop/package.json`
**antes** da primeira publicação (depois não muda mais).

## Pré-requisito no servidor

A API já libera as origens dos aplicativos (`CORS_ORIGINS` no `deploy/.env`, padrão
`https://localhost,capacitor://localhost,app://cerne`). Os aplicativos só aceitam servidor **https**.

## Gerar os instaladores pelo GitHub (recomendado)

O workflow **Cerne · Aplicativos** (`.github/workflows/cerne-apps.yml`) roda a cada mudança no painel ou nos apps
(ou manualmente em *Actions → Run workflow*) e publica em **Artifacts**:

- `cerne-android`: `app-debug.apk` (instala direto no celular para testar) e, se os segredos de assinatura estiverem
  configurados, `app-release.apk` e `app-release.aab`;
- `cerne-windows`: `Cerne-Setup-0.2.0.exe`;
- o job `ios` compila para o simulador para garantir que o projeto está íntegro.

## Android → Google Play

1. Crie a **chave de upload** (uma vez; guarde o arquivo e as senhas em cofre — perder = não conseguir atualizar o app):
   ```bash
   keytool -genkeypair -v -keystore cerne-upload.jks -alias cerne -keyalg RSA -keysize 4096 -validity 10000
   ```
2. No GitHub, *Settings → Secrets → Actions*: `ANDROID_KEYSTORE_BASE64` (`base64 -w0 cerne-upload.jks`),
   `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` (`cerne`), `ANDROID_KEY_PASSWORD`.
   (Local: copie `android/keystore.properties.example` para `keystore.properties`.)
3. Rode o workflow e baixe `app-release.aab`.
4. Na [Play Console](https://play.google.com/console) (conta de desenvolvedor, taxa única de US$ 25): criar app →
   *Teste interno* → enviar o `.aab` → ativar o **Play App Signing**.
5. Ficha da loja: nome, descrição, ícone 512 px (`../marca/png/icon-512.png`), capturas de tela,
   **política de privacidade (URL obrigatória)**, formulário de *Segurança dos dados* (o app transmite apenas
   códigos internos de cliente e valores de fatura ao servidor do provedor; nada é compartilhado com terceiros).
6. Contas pessoais novas precisam de **teste fechado com 12 testadores por 14 dias** antes da produção; contas de
   organização (CNPJ, com D-U-N-S) não têm essa exigência.

A cada versão nova, aumente `versionCode` e `versionName` em `mobile/android/app/build.gradle`.

## iOS → App Store (exige um Mac)

1. Mac com **Xcode 26** ou mais novo, conta no **Apple Developer Program** (US$ 99/ano; como organização, exige D-U-N-S).
2. ```bash
   cd cerne/apps/mobile
   npm ci
   npm run ios          # copia o painel, sincroniza e abre o Xcode
   ```
3. No Xcode: *Signing & Capabilities* → escolha o *Team*; confira o *Bundle Identifier*.
   *Product → Archive* → *Distribute App → App Store Connect*.
4. No [App Store Connect](https://appstoreconnect.apple.com): ficha, capturas (iPhone 6,9" e iPad se aplicável),
   política de privacidade, *App Privacy*, e **informações de login para a revisão** — a Apple precisa de um
   servidor de demonstração e de uma chave de API de teste (crie um provedor só para isso, com dados fictícios).
5. *TestFlight* para testes internos e depois *Enviar para revisão*.

A cada versão, aumente *Version* e *Build* no Xcode (`MARKETING_VERSION`/`CURRENT_PROJECT_VERSION`).

> Revisão da Apple (diretriz 4.2): o app traz interface própria empacotada (não é só um site embutido), mas a Apple
> pode questionar apps de uso restrito a empresas. Se recusarem, a alternativa é distribuir pelo **Apple Business
> Manager** (apps personalizados para organizações) ou por TestFlight.

## Windows

- Baixe `Cerne-Setup-0.2.0.exe` dos Artifacts (ou gere: `cd desktop && npm ci && npm run dist:win` no Windows).
- O instalador **não está assinado**: o Windows SmartScreen mostra "Editor desconhecido" (*Mais informações →
  Executar assim mesmo*). Para distribuir a clientes, compre um certificado de **assinatura de código** (OV/EV) e
  configure `CSC_LINK`/`CSC_KEY_PASSWORD` no electron-builder — ou publique na **Microsoft Store** (gera MSIX assinado
  pela loja; alvo `appx` do electron-builder).
- Desenvolvimento: `cd desktop && npm ci && npm start`.

## Desenvolvimento dos apps móveis

```bash
cd cerne/apps/mobile
npm ci
npm run android   # abre o Android Studio (emulador ou celular via USB)
npm run icones    # regenera ícones/splash a partir de assets/
```

Sempre que mudar o painel, rode `npm run sync` (copia `../../painel` para `www/` e para os projetos nativos).

## Segurança

- Android: sem backup automático na nuvem (`allowBackup=false`) e sem tráfego HTTP (`usesCleartextTraffic=false`).
- Windows: janela isolada (`contextIsolation`, `sandbox`), sem Node no conteúdo, navegação presa ao app,
  CSP restrita; links externos abrem no navegador.
- Relatório PDF no celular: salvo no cache do app e entregue pela folha de compartilhamento do sistema.
