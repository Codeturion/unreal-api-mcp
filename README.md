# unreal-api-mcp

<!-- mcp-name: io.github.Codeturion/unreal-api-mcp -->

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**MCP server that gives AI agents accurate Unreal Engine C++ API documentation. Prevents hallucinated signatures, wrong `#include` paths, and deprecated API usage.**

Supports **UE 5.4**, **5.5**, **5.6**, and **5.7** with separate databases for each version. Works with Claude Code, Cursor, Windsurf, or any MCP-compatible AI tool. No Unreal Engine installation required.

## Quick Start

```bash
pip install unreal-api-mcp
```

Add to your MCP config (`.mcp.json`, `mcp.json`, or your tool's MCP settings), setting `UNREAL_VERSION` to match your project:

```json
{
  "mcpServers": {
    "unreal-api": {
      "command": "unreal-api-mcp",
      "args": [],
      "env": {
        "UNREAL_VERSION": "5.5"
      }
    }
  }
}
```

Valid values: `"5.4"`, `"5.5"`, `"5.6"`, or `"5.7"`. On Windows, use `unreal-api-mcp.exe`.

On first run the server downloads the correct database (~43-48 MB) to `~/.unreal-api-mcp/`.

## How It Works

1. **Version detection.** The server figures out which UE version to serve:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | `UNREAL_VERSION` env var | `"5.5"`, `"5.6"`, `"5.7"` |
| 2 | `UNREAL_PROJECT_PATH` | Reads `.uproject` `EngineAssociation` field, maps `5.5.1` to `"5.5"` |
| 3 | Default | `"5.7"` |

2. **Database download.** If the database for that version isn't cached locally, it downloads from GitHub (one time).

3. **Serve.** All tool calls query the version-specific SQLite database. Exact lookups return in <1ms, searches in <5ms.

Each version has its own database with the correct signatures, deprecation warnings, and member lists for that release.

## Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `search_unreal_api` | Find APIs by keyword | "character movement", "spawn actor" |
| `get_function_signature` | Exact signature with parameters and return type | `AActor::GetActorLocation` |
| `get_include_path` | Resolve `#include` for a type | "ACharacter" -> `#include "GameFramework/Character.h"` |
| `get_class_reference` | Full class reference card | "APlayerController" -> all functions/properties/delegates |
| `get_deprecation_warnings` | Check if an API is obsolete | "K2_AttachRootComponentTo" -> Use AttachToComponent() instead |

## Coverage

All Engine Runtime, Editor, Developer modules, plus built-in plugins (Enhanced Input, Gameplay Abilities, Common UI, Niagara, Chaos, and hundreds more).

| Version | Records | Deprecated | Modules | DB Size |
|---------|---------|------------|---------|---------|
| UE 5.5 | 100,379 | 3,689 | 860 | 43 MB |
| UE 5.6 | 110,467 | 4,205 | 981 | 48 MB |

Record breakdown (UE 5.6):

| Type | Count | Source |
|------|-------|--------|
| Classes (UCLASS) | 9,789 | `AActor`, `ACharacter`, `UGameplayStatics`, ... |
| Structs (USTRUCT) | 8,617 | `FHitResult`, `FVector`, `FTransform`, ... |
| Enums (UENUM) | 3,327 | `EMovementMode`, `ECollisionChannel`, ... |
| Functions (UFUNCTION) | 22,854 | Signatures with params, return types, specifiers |
| Properties (UPROPERTY) | 63,478 | Types, specifiers, doc comments |
| Delegates | 2,402 | Dynamic multicast, delegate declarations |

Does **not** cover third-party plugins or marketplace assets. For those, rely on project source.

## Benchmarks

### Token cost: MCP vs file-based lookup

In a 10-step character movement development workflow, MCP uses **5x fewer tokens** than a skilled agent and **25x fewer** than a naive agent reading full headers:

| Step | Category | MCP | Skilled Agent | Naive Agent |
|------|----------|-----|---------------|-------------|
| 1. Find character movement component | Search | 781 | 9,349 | 44,247 |
| 2. Get `#include` for ACharacter | Include | 72 | 3,499 | 14,997 |
| 3. Check SetMovementMode signature | Signature | 107 | 9,349 | 44,247 |
| 4. Look up AddMovementInput params | Signature | 226 | 2,049 | 7,749 |
| 5. Get LaunchCharacter for dash ability | Signature | 183 | 3,499 | 14,997 |
| 6. Get `#include` for FHitResult | Include | 49 | 992 | 2,462 |
| 7. Search line trace functions | Search | 10 | 7,773 | 36,366 |
| 8. Full class reference for movement comp | Reference | 10,093 | 9,349 | 44,247 |
| 9. Check EMovementMode enum values | Search | 69 | 7,873 | 36,866 |
| 10. Check deprecated movement APIs | Deprecation | 18 | 9,349 | 44,247 |
| **TOTAL** | | **11,608** | **63,081** | **290,425** |

Token counts: MCP = actual tool output. Naive = full header file. Skilled = grep result + partial file read + tool overhead.

MCP returns structured, accurate data in a single call every time. No file scanning, no grepping through 70,000 headers, no hallucination risk.

### Query latency

Measured on UE 5.6 database (110,467 records), 50 iterations per query:

| Query | Median | p95 |
|-------|--------|-----|
| Exact FQN lookup (`get_function_signature`) | <1ms | <1ms |
| FTS search: specific function name | <1ms | <1ms |
| FTS search: keyword ("spawn actor") | 1ms | 1ms |
| Include path resolution | 2ms | 2ms |
| Class reference (full member list) | 22ms | 23ms |
| Deprecation check | 24ms | 25ms |

<details>
<summary>Accuracy</summary>

| Test | Result |
|------|--------|
| Search top-1 relevance (8 common queries) | 100% |
| Include path resolution (6 key classes) | 100% |
| Function signature accuracy (3 common functions) | 100% |
| Class reference completeness (2 classes) | 100% |
| Deprecation detection (1 deprecated API) | 100% |

Ranking uses BM25 with tuned column weights (member name 10x, class name 5x) plus core module boosting to ensure `AActor::GetActorLocation` ranks above niche plugin APIs.

</details>

## CLAUDE.md Snippet

Add this to your project's `CLAUDE.md` (or equivalent instructions file). **This step is important.** Without it, the AI has the tools but won't know when to reach for them.

```markdown
## Unreal Engine API Lookup (unreal-api MCP)

Use the `unreal-api` MCP tools to verify UE C++ API usage instead of guessing. **Do not hallucinate signatures or #include paths.**

| When | Tool | Example |
|------|------|---------|
| Unsure about a function's parameters or return type | `get_function_signature` | `get_function_signature("AActor::GetActorLocation")` |
| Need the `#include` for a type | `get_include_path` | `get_include_path("ACharacter")` |
| Want to see all members on a class | `get_class_reference` | `get_class_reference("UCharacterMovementComponent")` |
| Searching for an API by keyword | `search_unreal_api` | `search_unreal_api("spawn actor")` |
| Checking if an API is deprecated | `get_deprecation_warnings` | `get_deprecation_warnings("K2_AttachRootComponentTo")` |

**Rules:**
- Before writing a UE API call you haven't used in this conversation, verify the signature with `get_function_signature`
- Before adding a `#include`, verify with `get_include_path` if unsure
- Covers: all Engine Runtime/Editor modules, built-in plugins (Enhanced Input, GAS, CommonUI, Niagara, etc.)
- Does NOT cover: third-party plugins or marketplace assets
```

## Setup Details

<details>
<summary>Auto-detect version from .uproject</summary>

Instead of setting `UNREAL_VERSION`, you can point to your Unreal project. The server reads the `EngineAssociation` field from your `.uproject` file:

```json
{
  "mcpServers": {
    "unreal-api": {
      "command": "unreal-api-mcp",
      "args": [],
      "env": {
        "UNREAL_PROJECT_PATH": "F:/Unreal Projects/MyProject"
      }
    }
  }
}
```

</details>

<details>
<summary>Environment variables</summary>

| Variable | Purpose | Example |
|----------|---------|---------|
| `UNREAL_VERSION` | UE version to serve | `5.5`, `5.6`, `5.7` |
| `UNREAL_PROJECT_PATH` | Auto-detect version from .uproject | `F:/Unreal Projects/MyProject` |
| `UNREAL_INSTALL_PATH` | Override UE install path (for `ingest` only) | `H:/UE_5.6` |

</details>

<details>
<summary>Building databases locally</summary>

If you want to build a database from your own Unreal Engine installation instead of downloading:

```bash
# Build for a specific version
python -m unreal_api_mcp.ingest --unreal-version 5.6 --unreal-install "H:/UE_5.6"
python -m unreal_api_mcp.ingest --unreal-version 5.5 --unreal-install "H:/UE_5.5"
```

Databases are written to `~/.unreal-api-mcp/unreal_docs_{version}.db` by default.

</details>

<details>
<summary>Project structure</summary>

```
unreal-api-mcp/
├── src/unreal_api_mcp/
│   ├── server.py          # MCP server (5 tools)
│   ├── db.py              # SQLite + FTS5 database layer
│   ├── version.py         # Version detection + DB download
│   ├── header_parser.py   # Parse Unreal C++ headers (UCLASS, UFUNCTION, etc.)
│   ├── unreal_paths.py    # Locate UE installs + discover modules
│   └── ingest.py          # CLI ingestion pipeline
└── pyproject.toml
```

Databases are stored in `~/.unreal-api-mcp/` (downloaded on first run).

</details>

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Could not download UE X database" | Check internet connection. Or build locally: `python -m unreal_api_mcp.ingest --unreal-version 5.6 --unreal-install H:/UE_5.6` |
| Wrong API version being served | Set `UNREAL_VERSION` explicitly. Check stderr: `unreal-api-mcp: serving UE <version>` |
| Server won't start | Check `python --version` (needs 3.10+). Check path: `which unreal-api-mcp` or `where unreal-api-mcp` |
| Third-party plugins return no results | Marketplace/third-party plugins are not indexed. Only built-in Engine and Plugin APIs are covered. |

---

## Contact

Need a custom MCP server for your engine or framework? I build MCP tools that cut token waste and prevent hallucinations for AI-assisted game development. If you want something similar for your team's stack, reach out.

fuatcankoseoglu@gmail.com

## License

MIT
