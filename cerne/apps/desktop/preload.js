// Só sinaliza ao painel que ele roda no aplicativo de desktop (pede o endereço do servidor no login).
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("cerneDesktop", { plataforma: process.platform });
