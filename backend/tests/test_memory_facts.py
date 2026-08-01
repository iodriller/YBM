from __future__ import annotations

from agent_control.schemas import MemoryFact, MemorySource
from helpers import make_repos


def test_memory_fact_repository_create_list_update_delete(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)

    fact = repos.memory_facts.create(
        MemoryFact(category="preference", content="Prefers dark mode", source=MemorySource.USER_STATED)
    )

    assert repos.memory_facts.get(fact.id) == fact
    assert [f.id for f in repos.memory_facts.list_all()] == [fact.id]

    updated = repos.memory_facts.update_content(fact.id, category="preference", content="Prefers dark mode, high contrast")
    assert updated is not None
    assert updated.content == "Prefers dark mode, high contrast"
    assert updated.updated_at >= fact.updated_at

    assert repos.memory_facts.delete(fact.id) is True
    assert repos.memory_facts.get(fact.id) is None
    assert repos.memory_facts.delete(fact.id) is False


def test_memory_fact_repository_filters_by_category_and_query(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)
    repos.memory_facts.create(MemoryFact(category="preference", content="Likes concise answers"))
    repos.memory_facts.create(MemoryFact(category="project", content="Working on a Python API"))
    repos.memory_facts.create(MemoryFact(category="project", content="Deploys to a Windows machine"))

    by_category = repos.memory_facts.list_all(category="project")
    assert {f.content for f in by_category} == {"Working on a Python API", "Deploys to a Windows machine"}

    by_query = repos.memory_facts.list_all(query="python")
    assert [f.content for f in by_query] == ["Working on a Python API"]


def test_memory_fact_repository_list_all_orders_most_recently_updated_first(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)
    first = repos.memory_facts.create(MemoryFact(category="a", content="first"))
    second = repos.memory_facts.create(MemoryFact(category="b", content="second"))
    repos.memory_facts.update_content(first.id, category="a", content="first, edited")

    ordered = repos.memory_facts.list_all()

    assert [f.id for f in ordered] == [first.id, second.id]
