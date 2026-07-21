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
