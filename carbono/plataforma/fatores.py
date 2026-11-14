"""Fatores de emissão e potenciais de aquecimento global (GWP).

Os valores abaixo são REFERÊNCIAS para o MVP. Antes de qualquer inventário oficial,
atualize-os com a versão vigente da Ferramenta de Cálculo do Programa Brasileiro
GHG Protocol (FGV) e com o fator médio anual do SIN publicado pelo MCTI.
Cada fator guarda a fonte e o ano para que o verificador possa conferir.
"""
from __future__ import annotations

from dataclasses import dataclass

# GWP de 100 anos, IPCC AR5 (padrão adotado pelo Programa Brasileiro GHG Protocol).
GWP_AR5: dict[str, float] = {
    "CO2": 1.0,
    "CH4": 28.0,
    "N2O": 265.0,
    "HFC-134a": 1300.0,
    "HFC-32": 677.0,
    "R-410A": 1924.0,  # mistura 50/50 HFC-32 e HFC-125
    "SF6": 23500.0,
}


@dataclass(frozen=True)
class Fator:
    codigo: str
    descricao: str
    unidade: str
    escopo: int
    tco2e_por_unidade: float  # por unidade do combustível fóssil PURO, em CO2e
    fonte: str
    ano_referencia: int
    # Fração renovável padrão (ex.: etanol anidro na gasolina C, biodiesel no diesel B).
    # O CO2 dessa fração é biogênico: relatado à parte e fora do total do escopo.
    fracao_biogenica_padrao: float = 0.0
    tco2_biogenico_por_unidade_pura: float = 0.0


FATORES: dict[str, Fator] = {
    f.codigo: f
    for f in [
        Fator(
            "diesel_b", "Óleo diesel B (fóssil + biodiesel), combustão móvel/estacionária",
            "litro", 1, 0.002681, "IPCC 2006, v.2, cap. 3 (diesel fóssil); densidade ANP", 2024,
            fracao_biogenica_padrao=0.14, tco2_biogenico_por_unidade_pura=0.002431,
        ),
        Fator(
            "gasolina_c", "Gasolina C (gasolina A + etanol anidro)",
            "litro", 1, 0.002270, "IPCC 2006, v.2, cap. 3 (gasolina A); densidade ANP", 2024,
            fracao_biogenica_padrao=0.30, tco2_biogenico_por_unidade_pura=0.001526,
        ),
        Fator(
            "etanol_hidratado", "Etanol hidratado (100% biogênico)",
            "litro", 1, 0.0, "IPCC 2006 — CO2 biogênico, não soma ao escopo 1", 2024,
            fracao_biogenica_padrao=1.0, tco2_biogenico_por_unidade_pura=0.001457,
        ),
        Fator("gnv", "Gás natural veicular / gás natural", "m3", 1, 0.001999,
              "IPCC 2006, v.2; PCI médio ANP", 2024),
        Fator("glp", "Gás liquefeito de petróleo", "kg", 1, 0.002932,
              "IPCC 2006, v.2", 2024),
        Fator("energia_sin", "Energia elétrica comprada do SIN (abordagem por localização)",
              "MWh", 2, 0.0385, "MCTI — fator médio anual do SIN (CONFERIR ano vigente)", 2023),
        Fator("residuo_aterro", "Resíduos sólidos urbanos enviados a aterro sanitário",
              "tonelada", 3, 0.86, "Estimativa simplificada IPCC Waste Model — substituir por "
              "cálculo FOD específico", 2024),
        Fator("viagem_aerea_nacional", "Viagem aérea doméstica por passageiro",
              "passageiro.km", 3, 0.000158, "Referência GHG Protocol — atualizar", 2024),
    ]
}


def fator(codigo: str) -> Fator:
    try:
        return FATORES[codigo]
    except KeyError:
        raise ValueError(f"Fator de emissão desconhecido: {codigo!r}") from None
