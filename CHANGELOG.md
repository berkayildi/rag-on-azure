# Changelog

## [0.1.0](https://github.com/berkayildi/rag-on-azure/compare/v0.0.1...v0.1.0) (2026-05-10)


### Features

* add bicep modules for monitor, kv, search, openai, container app ([5e57808](https://github.com/berkayildi/rag-on-azure/commit/5e578084f09193d88bc88fe872c0be8f7a31ed4d))
* **api:** add /metrics Prometheus endpoint with retrieval/generation histograms ([#21](https://github.com/berkayildi/rag-on-azure/issues/21)) ([a6b8da6](https://github.com/berkayildi/rag-on-azure/commit/a6b8da6a423d4a9ee78835b92b5443015388bce7))
* **api:** replace /ingest 501 stub with background-task pipeline invocation ([#23](https://github.com/berkayildi/rag-on-azure/issues/23)) ([935bd4d](https://github.com/berkayildi/rag-on-azure/commit/935bd4de0777fbdf89ae70793f1f6c0c9688cd8e))
* **app:** add /readyz readiness endpoint pinging dependencies ([226b0d7](https://github.com/berkayildi/rag-on-azure/commit/226b0d76c2aa98af6b4a01833276f8e58e7eb83d))
* **app:** add cross-cutting models (Message, Chunk) ([c164296](https://github.com/berkayildi/rag-on-azure/commit/c16429653e3ca6a8a01498cc58f9e02d876ce72c))
* **app:** add KeyVaultClient with TTL-cached signing-key fetch ([ecd8066](https://github.com/berkayildi/rag-on-azure/commit/ecd8066113381674b9bd1454750df7fe55fcbec1))
* **app:** add LLMClient protocol with AzureOpenAIClient implementation ([d0aca62](https://github.com/berkayildi/rag-on-azure/commit/d0aca62af42f56edb8994652abc318bc1ff0bbcb))
* **app:** add TenantAwareSearchClient with audit-grade tests ([a498c09](https://github.com/berkayildi/rag-on-azure/commit/a498c09ebbbb14348e06272ae474b26de2914745))
* **app:** admin-gated /ingest endpoint with tenant_admin claim ([bebf588](https://github.com/berkayildi/rag-on-azure/commit/bebf58843c47848599c6dcf81aa6f8a9dec01dc6))
* **app:** api contract models and dev-mode auth dependency ([17025ff](https://github.com/berkayildi/rag-on-azure/commit/17025ff5f9e64be98e4487c5bb0bb341dcc3d901))
* **app:** fastapi service wrapping langgraph workflow ([c002808](https://github.com/berkayildi/rag-on-azure/commit/c0028085aa384cd6980cfa47e5c8c4d89153f264))
* **app:** generation short-circuit on empty retrieval + phase D verification ([92b86a4](https://github.com/berkayildi/rag-on-azure/commit/92b86a45ed397502beab3f029305dbd5657dbe42))
* **app:** langgraph workflow with understand/retrieve/generate nodes ([9b2fc91](https://github.com/berkayildi/rag-on-azure/commit/9b2fc91267aabf8835e46aa028f769aeb9fedaba))
* **app:** validate PEM shape during KeyVaultClient ping ([f15d6a9](https://github.com/berkayildi/rag-on-azure/commit/f15d6a973b681bfa90fa5accbaf54fb4e8615c04))
* **app:** verify JWT signatures against Key Vault key ([b0131a2](https://github.com/berkayildi/rag-on-azure/commit/b0131a25da115dfcd8272b160d9af1655136f3fe))
* **ci:** add bicep what-if and deploy stages with OIDC ([d130e51](https://github.com/berkayildi/rag-on-azure/commit/d130e5134191a4bc9f947fabf6c1a64c516beb7b))
* **ci:** add bootstrap-oidc.sh and document federated identity setup ([ef98adc](https://github.com/berkayildi/rag-on-azure/commit/ef98adc98667475dc5a1653398f9c1587676674b))
* **ci:** add ci.yml workflow with lint, test, gitleaks, build stages ([a66d07a](https://github.com/berkayildi/rag-on-azure/commit/a66d07a6efbd945bd16a1fcd9ba5fb358d7c22bf))
* **ci:** publish eval-gate artefacts to llm-benchmarks repo via GitHub App ([#22](https://github.com/berkayildi/rag-on-azure/issues/22)) ([80e8a68](https://github.com/berkayildi/rag-on-azure/commit/80e8a685d80781f4c17992349a6448b0cb3f8081))
* **deploy:** dockerfile + ghcr build workflow + lockfile note ([b04feee](https://github.com/berkayildi/rag-on-azure/commit/b04feeea1802abcd93cdd9481e5c1b57bcbb8d15))
* **eval:** mcp-llm-eval gate via snapshot-and-evaluate against deployed AI Search ([#18](https://github.com/berkayildi/rag-on-azure/issues/18)) ([5eb6d43](https://github.com/berkayildi/rag-on-azure/commit/5eb6d433e8210b20aa30dc912e5c26ef27bc5944))
* **infra:** flip ENABLE_DEV_AUTH=false for prod-mode auth ([bc25024](https://github.com/berkayildi/rag-on-azure/commit/bc25024aa639b8f1d46d27b32e4085b4a3177356))
* **infra:** optional developer principal rbac on search service ([2db97f2](https://github.com/berkayildi/rag-on-azure/commit/2db97f2eb2aabd8328d4eff0aebff4548b8abbb9))
* **infra:** optional developer rbac on openai account ([908e2ea](https://github.com/berkayildi/rag-on-azure/commit/908e2eabbc4e4e3faff59841a52f804e026432a1))
* **infra:** swap container app to ghcr image + add app env vars ([d2b0c1d](https://github.com/berkayildi/rag-on-azure/commit/d2b0c1dfd01898c069bae6cc918c28e4e0c352c0))
* **ingest:** emit .fetched.jsonl index from fetch run ([5616f42](https://github.com/berkayildi/rag-on-azure/commit/5616f42358d72479b20bf64e18e239c2c3930241))
* **ingest:** implement chunk into chunks.jsonl ([9088c73](https://github.com/berkayildi/rag-on-azure/commit/9088c73c44d96fb970b83f13806c32238dc52f02))
* **ingest:** implement fetch with hash-based cache ([a16dcdb](https://github.com/berkayildi/rag-on-azure/commit/a16dcdb63139d4cb5fb657aaad9c30573b07961d))
* **ingest:** index schema with hnsw vector profile ([79f433f](https://github.com/berkayildi/rag-on-azure/commit/79f433fb5db7547faac43ca798aea3089fde51db))
* **ingest:** orchestrator with content_hash idempotence ([9e0ab90](https://github.com/berkayildi/rag-on-azure/commit/9e0ab906c0d4fb69a439547b226d2967b3af1001))
* **ingest:** pdf text extraction via pypdf with page-citation markers ([a063122](https://github.com/berkayildi/rag-on-azure/commit/a06312221ed39cd014a98de310ba6594df03eb9b))
* **ingest:** pivot corpus to fca publications ([5523948](https://github.com/berkayildi/rag-on-azure/commit/5523948486dda12ed77ad17bbb50a8a58a9fb640))
* **ingest:** provisional azure openai embedding client ([0031cd1](https://github.com/berkayildi/rag-on-azure/commit/0031cd151310c314fb5a518fb0085d3850b4471b))


### Bug Fixes

* **ci:** split unit-test invocation per package to avoid conftest collision ([ee0713f](https://github.com/berkayildi/rag-on-azure/commit/ee0713f7fc753e26fa3299742350c52fd2cabaf9))
* **eval:** calibrate gate thresholds + small ci/docs followups ([#19](https://github.com/berkayildi/rag-on-azure/issues/19)) ([1d59de5](https://github.com/berkayildi/rag-on-azure/commit/1d59de5bd0e2c991ab4c5e8fe206d8c4d1a429a0))
* **infra:** chat deployment to gpt-4o, sweden central standard ([27e1c64](https://github.com/berkayildi/rag-on-azure/commit/27e1c644c810b38b9df401e532f4ca883a2c3dcd))
* **infra:** chat model version to 2024-11-20 (validator rejects 2024-08-06) ([87ea265](https://github.com/berkayildi/rag-on-azure/commit/87ea265fa5bc1cacf6a776e5c11f08f3afcea57d))
* **infra:** default to swedencentral, mixed openai skus (standard + datazonestandard) ([0fc1803](https://github.com/berkayildi/rag-on-azure/commit/0fc1803062ad94d68cbaab9dee219db0d06adbd8))
* **infra:** stop overwriting jwt-signing-key on every deploy ([#17](https://github.com/berkayildi/rag-on-azure/issues/17)) ([e926077](https://github.com/berkayildi/rag-on-azure/commit/e926077686a9039ab02be531cbf3010da5901fb3))
* **ingest:** adapt schema.py to azure-search-documents 12.x SearchFieldDataType Enum ([08ef1dc](https://github.com/berkayildi/rag-on-azure/commit/08ef1dc81f1b591047d2b6fc8199ae073e84c24a))
* **ingest:** add aiohttp dep for azure-identity.aio transport ([0932f26](https://github.com/berkayildi/rag-on-azure/commit/0932f2613b82de42369d1597b4ec8082ad9e8b00))
* **ingest:** drop redundant type-ignore now that markdownify ships stubs ([ca9747f](https://github.com/berkayildi/rag-on-azure/commit/ca9747ff977cb9a3b4d690e73bd01b82e68db5f3))
* **ingest:** partial fetch failure should not abort pipeline ([459251a](https://github.com/berkayildi/rag-on-azure/commit/459251ae06d6c29315bc51cbdcbc047ff8ace5a4))
* **ingest:** update broken hmrc-aml-tcsp url to current gov.uk path ([c224777](https://github.com/berkayildi/rag-on-azure/commit/c224777f9143247387e29aff8ec4c995afd19538))


### Documentation

* **agents:** capture Wave 2 follow-ups for Day 8 ([9012538](https://github.com/berkayildi/rag-on-azure/commit/9012538795d2f31425bbd9f56eaefa0a04319ab9))
* **agents:** operational quirks from day 6 phase 5 deployment ([63fe9ee](https://github.com/berkayildi/rag-on-azure/commit/63fe9eec5b94d6b8ad7c01c1fb40d65a6a5187c9))
* align rg naming convention with azd default ([80eb166](https://github.com/berkayildi/rag-on-azure/commit/80eb16676d76e43e23532525898d5884e846aa74))
* fix azd down flag (--force-delete is not valid) ([6187364](https://github.com/berkayildi/rag-on-azure/commit/61873641e6effb6bf7f8a672a00f9e333ae4cdc9))
* note pre-commit mypy deps stay in sync with pyproject.toml ([e0ad6e5](https://github.com/berkayildi/rag-on-azure/commit/e0ad6e5722176bd67500245f82cb2d136a74e23e))
* **readme:** comprehensive ship-quality README pass for v0.1.0 ([#25](https://github.com/berkayildi/rag-on-azure/issues/25)) ([0a59b8f](https://github.com/berkayildi/rag-on-azure/commit/0a59b8f552b4e256ae8a0119f6e697547b94f443))
* route readme + agents.md through make targets ([5d03201](https://github.com/berkayildi/rag-on-azure/commit/5d0320157a37093a1339d30ac10facf392b4e80f))
* switch chat model to gpt-4o due to gpt-4o-mini deprecation ([05103bd](https://github.com/berkayildi/rag-on-azure/commit/05103bd4d8fa105a64af9e592867e0cd837fbd6d))
* switch default region to sweden central with mixed openai skus ([992cb22](https://github.com/berkayildi/rag-on-azure/commit/992cb22dd841ebb1e9c877fee207fd62a6f8cdd4))
* update skill location ([410dff3](https://github.com/berkayildi/rag-on-azure/commit/410dff3dd26ff94a5cf40d026182c388f95dbd75))
