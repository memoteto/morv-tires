# Morv Tires — Engineering navigation

Use the repository's actual source at the current commit as the authority. Also follow the existing engineering and business rules in `CLAUDE.md`. Knowledge maps are read-only navigation aids, not a substitute for source inspection or tests.

## Two complementary maps

- **Graphify**: branch `graphify-maps`, root `metadata.json`, `GRAPH_REPORT.md`, `graph.json` and `graph.html`. Use the metadata first. The code-only map covers `morv/` and updates after relevant changes reach main. It contains static relationships; unresolved or missing edges are not proof that a dependency does not exist.
- **Understand Anything**: branch `knowledge-maps`, `.ua/` (older snapshots: `morv/.ua/`). Read `meta.json` and the relevant part of `knowledge-graph.json` or `code-lessons.json`. Use the Arabic explanations and architectural layers for orientation, not as current implementation evidence. The snapshot may be older than main.

## For each development task

1. Check the current source branch and commit. Read the maps' metadata and check changes since their recorded source commits in the relevant files.
2. Use a small, targeted graph query or extract only the relevant nodes and edges by file path/symbol; do not put an entire graph into the model context. If the graph is unavailable, stale, or irrelevant, proceed with targeted source inspection.
3. Verify every material dependency, function, business rule and proposed change against current source and tests. Treat static guesses and dynamic dispatch as unverified. Inspect shared UI components and accounting/inventory constraints where relevant.
4. Do not regenerate Understand Anything or run semantic labeling/LLM graph extraction without explicit owner approval. Do not add API keys. Graphify generation is code-only, with no AI labeling, on GitHub Actions.
5. Never merge generated map branches into main or edit their generated files as a substitute for fixing source. Keep existing engineering approval and verification rules.

If Graphify's metadata commit differs from the current commit, inspect relevant source changes before using the map. Graphify does not cover tests or repository-wide documentation. A successful map build is not evidence that the application or its accounting is correct.

## Keep development tooling outside the product

- Never merge `graphify-maps` or `knowledge-maps` into `main`; publish generated files only to their designated branches.
- Do not add Graphify, Understand Anything, or their dependencies to `requirements.txt` or the application/build dependency list in `requirements-dev.txt`. Install Graphify only in its independent workflow or an isolated development environment.
- Never ship generated graphs, reports, caches, tools or their dependencies inside the application, EXE, or installer.
- Do not introduce a runtime integration layer, synchronization service, or adapter between the two tools. Keep integration at the level of shared guidance and targeted queries only.
- Keep the existing application, CI and packaging paths independent of knowledge-map generation. Do not make product startup, builds or business operations depend on either tool.
- A map must cover all tracked SQL migrations before publication; coverage means source-file representation, not proof that every SQL statement or relationship was extracted correctly.
