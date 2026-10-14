from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceSpec:
    """Describes one PAIS resource type's list/delete endpoints.

    To expose another PAIS resource later, add a ResourceSpec here and a pair
    of thin list_*/delete_* tools in server.py — the client and error handling
    are already generic.
    """

    key: str
    list_path: str
    delete_path: str  # template containing "{id}"

    def delete_path_for(self, resource_id: str) -> str:
        return self.delete_path.format(id=resource_id)


AGENTS = ResourceSpec(
    key="agent",
    list_path="/api/v1/compatibility/openai/v1/agents",
    delete_path="/api/v1/compatibility/openai/v1/agents/{id}",
)

KNOWLEDGE_BASES = ResourceSpec(
    key="knowledge_base",
    list_path="/api/v1/control/knowledge-bases",
    delete_path="/api/v1/control/knowledge-bases/{id}",
)

DATA_SOURCES = ResourceSpec(
    key="data_source",
    list_path="/api/v1/control/data-sources",
    delete_path="/api/v1/control/data-sources/{id}",
)
