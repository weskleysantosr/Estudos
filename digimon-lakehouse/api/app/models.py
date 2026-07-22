from pydantic import BaseModel


class DigimonSummary(BaseModel):
    digimon_id: int
    name: str
    x_antibody: bool | None
    release_date: str | None
    image_url: str | None
    levels: list[str]
    types: list[str]
    attributes: list[str]
    fields: list[str]
    next_evolution_count: int


class PaginatedDigimons(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DigimonSummary]


class StatItem(BaseModel):
    label: str
    digimon_count: int


class EvolutionChain(BaseModel):
    root_digimon_name: str
    leaf_digimon_name: str
    depth: int
    digimon_id_path: list[int]


class DigimonEvolution(BaseModel):
    direction: str  # "next" (evolui pra) ou "prior" (evoluiu de)
    related_digimon_id: int
    related_digimon_name: str
    related_digimon_image_url: str | None
    condition: str | None
    related_digimon_levels: list[str]
