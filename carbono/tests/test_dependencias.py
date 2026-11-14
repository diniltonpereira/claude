"""Toda biblioteca importada pela plataforma precisa estar no requirements.txt.

Sem isso, os testes passam numa máquina que já tem a biblioteca e a imagem Docker quebra em produção
(aconteceu com o Pillow, usado na checagem de imagens dos anexos)."""
import ast
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
NOME_NO_PIP = {"PIL": "pillow", "shapefile": "pyshp", "multipart": "python-multipart", "jwt": "pyjwt"}


def test_requirements_cobre_os_imports():
    pedidos = {l.split("==")[0].strip().lower() for l in (RAIZ / "requirements.txt").read_text().splitlines()
               if l.strip() and not l.startswith("#")}
    faltando = set()
    for arq in (RAIZ / "plataforma").rglob("*.py"):
        for no in ast.walk(ast.parse(arq.read_text())):
            nomes = [a.name for a in no.names] if isinstance(no, ast.Import) else \
                [no.module] if isinstance(no, ast.ImportFrom) and no.level == 0 and no.module else []
            for n in nomes:
                topo = n.split(".")[0]
                if topo in sys.stdlib_module_names or topo == "plataforma":
                    continue
                if NOME_NO_PIP.get(topo, topo).lower() not in pedidos:
                    faltando.add(f"{topo} ({arq.name})")
    assert not faltando, f"Acrescente ao requirements.txt: {sorted(faltando)}"
