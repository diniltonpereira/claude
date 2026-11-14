"use strict";
/* Fila de envio do aparelho (IndexedDB): lançamentos feitos sem conexão, com o comprovante.
 * Cada item é do usuário que o criou e leva uma chave de idempotência: reenviar não duplica. */
const Fila = (() => {
  const BANCO = "lastro", LOJA = "fila";
  let conexao;

  function abrir() {
    if (conexao) return conexao;
    conexao = new Promise((ok, erro) => {
      const r = indexedDB.open(BANCO, 1);
      r.onupgradeneeded = () => {
        const loja = r.result.createObjectStore(LOJA, { keyPath: "chave" });
        loja.createIndex("usuario", "usuario");
      };
      r.onsuccess = () => ok(r.result);
      r.onerror = () => erro(r.error);
    });
    return conexao;
  }

  async function operar(modo, fn) {
    const db = await abrir();
    return new Promise((ok, erro) => {
      const tx = db.transaction(LOJA, modo);
      const resultado = fn(tx.objectStore(LOJA));
      tx.oncomplete = () => ok(resultado && "result" in resultado ? resultado.result : undefined);
      tx.onerror = () => erro(tx.error);
    });
  }

  const novaChave = () => (crypto.randomUUID ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""));

  return {
    novaChave,
    adicionar: (item) => operar("readwrite", (s) => s.put({ status: "pendente", erro: null, criado_em: new Date().toISOString(), ...item })),
    atualizar: (item) => operar("readwrite", (s) => s.put(item)),
    remover: (chave) => operar("readwrite", (s) => s.delete(chave)),
    listar: (usuario) => operar("readonly", (s) => s.index("usuario").getAll(usuario)),
  };
})();
