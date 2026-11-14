"""Aferição: cálculo de emissões por atividade, seguindo o GHG Protocol.

Duas formas de lançar uma atividade:
- por dado de atividade (litros, MWh, toneladas…) com um fator da tabela;
- por massa de gás emitida (ex.: recarga de gás refrigerante), convertida pelo GWP.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fatores import GWP_AR5, fator


@dataclass(frozen=True)
class ResultadoAtividade:
    escopo: int
    tco2e: float            # entra no total do escopo
    tco2_biogenico: float   # relatado à parte (GHG Protocol)
    memoria: str            # memória de cálculo, para o verificador


def calcular_por_fator(codigo: str, quantidade: float,
                       fracao_biogenica: float | None = None) -> ResultadoAtividade:
    if quantidade < 0:
        raise ValueError("A quantidade não pode ser negativa.")
    f = fator(codigo)
    frac = f.fracao_biogenica_padrao if fracao_biogenica is None else fracao_biogenica
    if not 0 <= frac <= 1:
        raise ValueError("A fração biogênica deve estar entre 0 e 1.")
    # tco2e_por_unidade refere-se ao combustível fóssil puro; a mistura é ponderada.
    fossil = quantidade * (1 - frac) * f.tco2e_por_unidade
    biog = quantidade * frac * f.tco2_biogenico_por_unidade_pura
    memoria = (f"{quantidade:g} {f.unidade} × (1 − {frac:g}) × {f.tco2e_por_unidade:g} tCO2e/"
               f"{f.unidade} = {fossil:.6f} tCO2e | fonte: {f.fonte} ({f.ano_referencia})")
    return ResultadoAtividade(f.escopo, round(fossil, 6), round(biog, 6), memoria)


def calcular_por_gas(gas: str, toneladas: float, escopo: int = 1) -> ResultadoAtividade:
    if toneladas < 0:
        raise ValueError("A massa não pode ser negativa.")
    if gas not in GWP_AR5:
        raise ValueError(f"Gás sem GWP cadastrado: {gas!r}")
    if escopo not in (1, 2, 3):
        raise ValueError("Escopo deve ser 1, 2 ou 3.")
    t = toneladas * GWP_AR5[gas]
    memoria = f"{toneladas:g} t {gas} × GWP-AR5 {GWP_AR5[gas]:g} = {t:.6f} tCO2e"
    return ResultadoAtividade(escopo, round(t, 6), 0.0, memoria)
