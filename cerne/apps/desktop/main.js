// Cerne · aplicativo Windows. Empacota o painel e o serve em app://cerne (origem autorizada no CORS da API).
const { app, BrowserWindow, Menu, net, protocol, shell } = require("electron");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.join(__dirname, "painel");
const ORIGIN = "app://cerne";
const CSP = [
  "default-src 'self'", "script-src 'self'", "style-src 'self'", "font-src 'self'",
  "img-src 'self' data: blob:", "connect-src https: http://localhost:* http://127.0.0.1:*",
  "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'",
].join("; ");

protocol.registerSchemesAsPrivileged([
  { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
]);

if (!app.requestSingleInstanceLock()) app.quit();

function servePainel() {
  protocol.handle("app", async (req) => {
    const { host, pathname } = new URL(req.url);
    if (host !== "cerne") return new Response("Não encontrado", { status: 404 });
    const rel = decodeURIComponent(pathname) === "/" ? "index.html" : decodeURIComponent(pathname);
    const file = path.normalize(path.join(ROOT, rel));
    if (!file.startsWith(ROOT + path.sep)) return new Response("Proibido", { status: 403 });
    const res = await net.fetch(pathToFileURL(file).toString());
    const headers = new Headers(res.headers);
    headers.set("Content-Security-Policy", CSP);
    return new Response(res.body, { status: res.status, headers });
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280, height: 820, minWidth: 380, minHeight: 560,
    backgroundColor: "#0A1B33", title: "Cerne", show: false, autoHideMenuBar: true,
    icon: path.join(__dirname, "build", "icon.ico"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, sandbox: true, nodeIntegration: false, spellcheck: false,
    },
  });
  win.once("ready-to-show", () => win.show());
  // nada sai de app://cerne dentro da janela; links externos abrem no navegador padrão
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(ORIGIN)) e.preventDefault(); });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });
  win.loadURL(`${ORIGIN}/index.html`);
  return win;
}

app.on("second-instance", () => {
  const [w] = BrowserWindow.getAllWindows();
  if (w) { if (w.isMinimized()) w.restore(); w.focus(); }
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  servePainel();
  createWindow();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
